"""SQLite metadata index for the knowledge store (SQLAlchemy).

The database stores document metadata (subject, subsubject, title, tags,
faceted classification) for fast queries.  The actual document *content*
stays on the filesystem — this index is purely for lookup and filtering.

An FTS5 full-text index (``knowledge_fts``) is maintained alongside the
metadata table.  It enables ranked keyword search with Porter stemming
and BM25 scoring via :meth:`KnowledgeDB.fts_search`.

Faceted classification follows PMEST (Ranganathan):
    - personality: what (entity/topic)
    - matter: material (medium/substance)
    - energy: action (process/operation)
    - space: where (location)
    - time: when (period)

Rebuild the index from disk at any time with :meth:`KnowledgeDB.sync`.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Index,
    String,
    Text,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .models import FACETS, Facets, slugify

log = logging.getLogger(__name__)

# FTS5 virtual table DDL — tokenize with Porter stemmer + unicode61.
_FTS_CREATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    path,
    title,
    tags,
    content,
    tokenize = 'porter unicode61'
);
"""


_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "it", "its", "they", "them", "their", "what", "which",
    "who", "whom", "this", "that", "these", "those", "am", "or", "and",
    "but", "if", "of", "at", "by", "for", "with", "about", "against",
    "between", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "in", "out", "on", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where",
    "why", "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "don", "now", "also", "like",
})


# ------------------------------------------------------------------
# ORM model
# ------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    path = Column(String, primary_key=True)
    subject = Column(String, nullable=False)
    subsubject = Column(String, nullable=False)
    title = Column(String, nullable=False)
    tags = Column(Text, nullable=False, default="[]")
    # PMEST facets
    personality = Column(String, nullable=False, default="")
    matter = Column(String, nullable=False, default="")
    energy = Column(String, nullable=False, default="")
    space = Column(String, nullable=False, default="")
    time = Column(String, nullable=False, default="")

    updated_at = Column(Float, nullable=False)

    __table_args__ = (
        Index("idx_subject", "subject"),
        Index("idx_subsubject", "subject", "subsubject"),
        Index("idx_personality", "personality"),
        Index("idx_matter", "matter"),
        Index("idx_energy", "energy"),
        Index("idx_space", "space"),
        Index("idx_time", "time"),
    )

    def to_dict(self) -> dict[str, Any]:
        tags_parsed: list[str] = []
        try:
            tags_parsed = json.loads(self.tags) if self.tags else []
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "path": self.path,
            "subject": self.subject,
            "subsubject": self.subsubject,
            "title": self.title,
            "tags": tags_parsed,
            "facets": {
                "personality": self.personality or "",
                "matter": self.matter or "",
                "energy": self.energy or "",
                "space": self.space or "",
                "time": self.time or "",
            },
            "updated_at": self.updated_at,
        }


# ------------------------------------------------------------------
# Database class
# ------------------------------------------------------------------


class KnowledgeDB:
    """SQLAlchemy-backed metadata index for knowledge documents.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        url = f"sqlite:///{db_path}"
        self._engine = create_engine(url, echo=False)
        self._SessionFactory = sessionmaker(bind=self._engine)

        Base.metadata.create_all(self._engine)

        with self._engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            conn.exec_driver_sql(_FTS_CREATE)
            conn.commit()

    def _session(self) -> Session:
        return self._SessionFactory()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(
        self,
        subject: str,
        subsubject: str,
        title: str,
        *,
        tags: list[str] | None = None,
        facets: Facets | dict[str, str] | None = None,
        updated_at: float = 0.0,
        content: str = "",
    ) -> None:
        """Insert or update a document's metadata.

        When *content* is provided, the FTS5 index is also updated.
        """
        path = f"{subject}/{subsubject}/{title}"
        tags_json = json.dumps(tags or [])

        if not isinstance(facets, Facets):
            facets = Facets.from_dict(facets)

        with self._session() as session:
            existing = session.get(Document, path)
            if existing is None:
                session.add(Document(
                    path=path,
                    subject=subject,
                    subsubject=subsubject,
                    title=title,
                    tags=tags_json,
                    personality=facets.personality,
                    matter=facets.matter,
                    energy=facets.energy,
                    space=facets.space,
                    time=facets.time,
                    updated_at=updated_at,
                ))
            else:
                existing.tags = tags_json
                existing.personality = facets.personality
                existing.matter = facets.matter
                existing.energy = facets.energy
                existing.space = facets.space
                existing.time = facets.time
                existing.updated_at = updated_at
            session.commit()

        if content:
            tags_text = " ".join(tags or [])
            self._fts_upsert(path, title, tags_text, content)

    def remove(self, path: str) -> bool:
        """Remove a document from the index. Returns True if it existed."""
        with self._session() as session:
            row = session.get(Document, path)
            if row is None:
                return False
            session.delete(row)
            session.commit()
        self._fts_remove(path)
        return True

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def get(self, path: str) -> dict[str, Any] | None:
        """Fetch metadata for a single document by path."""
        with self._session() as session:
            row = session.get(Document, path)
            if row is None:
                return None
            return row.to_dict()

    def query(
        self,
        *,
        subject: str = "",
        subsubject: str = "",
        tag: str = "",
        personality: str = "",
        matter: str = "",
        energy: str = "",
        space: str = "",
        time: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query documents by metadata fields.

        All filter parameters are optional and combined with AND.
        Facet filters match as case-insensitive substring (LIKE '%value%').
        """
        stmt = select(Document)

        if subject:
            stmt = stmt.where(Document.subject == subject)
        if subsubject:
            stmt = stmt.where(Document.subsubject == subsubject)
        if tag:
            stmt = stmt.where(Document.tags.contains(f'"{tag}"'))
        if personality:
            stmt = stmt.where(Document.personality.ilike(f"%{personality}%"))
        if matter:
            stmt = stmt.where(Document.matter.ilike(f"%{matter}%"))
        if energy:
            stmt = stmt.where(Document.energy.ilike(f"%{energy}%"))
        if space:
            stmt = stmt.where(Document.space.ilike(f"%{space}%"))
        if time:
            stmt = stmt.where(Document.time.ilike(f"%{time}%"))

        stmt = stmt.order_by(Document.updated_at.desc()).limit(limit).offset(offset)

        with self._session() as session:
            rows = session.execute(stmt).scalars().all()
            return [r.to_dict() for r in rows]

    def list_subjects(self) -> list[str]:
        """Return distinct subjects."""
        stmt = select(Document.subject).distinct().order_by(Document.subject)
        with self._session() as session:
            return list(session.execute(stmt).scalars().all())

    def list_subsubjects(self, subject: str) -> list[str]:
        """Return distinct subsubjects under a subject."""
        stmt = (
            select(Document.subsubject)
            .where(Document.subject == subject)
            .distinct()
            .order_by(Document.subsubject)
        )
        with self._session() as session:
            return list(session.execute(stmt).scalars().all())

    def list_tags(self) -> list[str]:
        """Return all distinct tags across all documents."""
        stmt = select(Document.tags)
        with self._session() as session:
            rows = session.execute(stmt).scalars().all()

        all_tags: set[str] = set()
        for raw in rows:
            try:
                all_tags.update(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(all_tags)

    def list_facet_values(self, facet: str) -> list[str]:
        """Return distinct non-empty values for a given facet."""
        if facet not in FACETS:
            raise ValueError(f"Unknown facet: {facet!r}. Must be one of {FACETS}")
        col = getattr(Document, facet)
        stmt = (
            select(col)
            .where(col != "")
            .distinct()
            .order_by(col)
        )
        with self._session() as session:
            return list(session.execute(stmt).scalars().all())

    def count(self) -> int:
        """Total number of indexed documents."""
        stmt = select(func.count()).select_from(Document)
        with self._session() as session:
            return session.execute(stmt).scalar() or 0

    # ------------------------------------------------------------------
    # FTS5 full-text search
    # ------------------------------------------------------------------

    def _fts_upsert(self, path: str, title: str, tags: str, content: str) -> None:
        """Insert or replace a document in the FTS5 index."""
        with self._engine.connect() as conn:
            conn.exec_driver_sql(
                "DELETE FROM knowledge_fts WHERE path = ?", (path,)
            )
            conn.exec_driver_sql(
                "INSERT INTO knowledge_fts (path, title, tags, content) VALUES (?, ?, ?, ?)",
                (path, title, tags, content),
            )
            conn.commit()

    def _fts_remove(self, path: str) -> None:
        """Remove a document from the FTS5 index."""
        with self._engine.connect() as conn:
            conn.exec_driver_sql(
                "DELETE FROM knowledge_fts WHERE path = ?", (path,)
            )
            conn.commit()

    def fts_search(
        self,
        query: str,
        *,
        limit: int = 20,
        mode: str = "or",
    ) -> list[dict[str, Any]]:
        """Ranked full-text search using FTS5 with BM25 scoring.

        Returns a list of dicts with ``path``, ``title``, ``snippet``,
        and ``rank`` (lower is better).  Results are ordered by relevance.

        *mode* controls how terms are combined:
        - ``"or"`` (default): any matching term contributes to ranking —
          best for natural language queries.
        - ``"and"``: all terms must be present — best for precise keyword
          searches.
        """
        if not query.strip():
            return []

        terms = [t for t in query.strip().split() if t and t.lower() not in _STOP_WORDS]
        if not terms:
            return []

        joiner = " OR " if mode == "or" else " "
        fts_query = joiner.join(f'"{t}"' for t in terms)

        sql = text("""
            SELECT
                knowledge_fts.path,
                knowledge_fts.title,
                snippet(knowledge_fts, 3, '**', '**', '...', 40) AS snippet,
                bm25(knowledge_fts) AS rank
            FROM knowledge_fts
            WHERE knowledge_fts MATCH :query
            ORDER BY rank
            LIMIT :limit
        """)

        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"query": fts_query, "limit": limit}).fetchall()

        results = []
        for row in rows:
            results.append({
                "path": row[0],
                "title": row[1],
                "snippet": row[2],
                "rank": row[3],
            })
        return results

    # ------------------------------------------------------------------
    # Sync from filesystem
    # ------------------------------------------------------------------

    def sync(self, knowledge_dir: str) -> dict[str, int]:
        """Rebuild the index from the filesystem.

        Walks ``knowledge_dir/<subject>/<subsubject>/<title>.md`` and
        upserts each document (including FTS5 content).  Documents in the
        DB that no longer exist on disk are removed.  Existing tags/facets
        are preserved during sync.

        Returns a summary dict: {added, updated, removed, total}.
        """
        on_disk: set[str] = set()
        added = 0
        updated = 0

        if not os.path.isdir(knowledge_dir):
            return {"added": 0, "updated": 0, "removed": 0, "total": 0}

        for subject in sorted(os.listdir(knowledge_dir)):
            subject_dir = os.path.join(knowledge_dir, subject)
            if not os.path.isdir(subject_dir) or subject.startswith("."):
                continue
            for subsubject in sorted(os.listdir(subject_dir)):
                subsub_dir = os.path.join(subject_dir, subsubject)
                if not os.path.isdir(subsub_dir) or subsubject.startswith("."):
                    continue
                for fname in sorted(os.listdir(subsub_dir)):
                    if not fname.endswith(".md") or fname.startswith("."):
                        continue
                    title = fname[:-3]
                    path = f"{subject}/{subsubject}/{title}"
                    on_disk.add(path)

                    fpath = os.path.join(subsub_dir, fname)
                    mtime = os.path.getmtime(fpath)

                    try:
                        with open(fpath, encoding="utf-8") as f:
                            content = f.read()
                    except OSError:
                        content = ""

                    existing = self.get(path)
                    if existing is None:
                        self.upsert(subject, subsubject, title,
                                    updated_at=mtime, content=content)
                        added += 1
                    elif existing["updated_at"] < mtime:
                        self.upsert(
                            subject, subsubject, title,
                            tags=existing["tags"],
                            facets=existing["facets"],
                            updated_at=mtime,
                            content=content,
                        )
                        updated += 1
                    else:
                        # Ensure FTS is populated even if metadata unchanged
                        self._fts_upsert(path, title,
                                         " ".join(existing.get("tags", [])),
                                         content)

        # Remove entries for documents that no longer exist on disk
        with self._session() as session:
            all_paths = session.execute(select(Document.path)).scalars().all()

        removed = 0
        for p in all_paths:
            if p not in on_disk:
                self.remove(p)
                removed += 1

        total = self.count()
        log.info("knowledge db sync: added=%d updated=%d removed=%d total=%d",
                 added, updated, removed, total)
        return {"added": added, "updated": updated, "removed": removed, "total": total}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._engine.dispose()
