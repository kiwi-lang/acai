"""Persistent knowledge-document storage.

Layout::

    workspace/knowledge/<subject>/<subsubject>/<title>.md

Documents are plain markdown files organised in a two-level subject
hierarchy.  The path doubles as the document identifier::

    path = "python/asyncio/generators"
    file = workspace/knowledge/python/asyncio/generators.md

Agents can create, update, search, and reload documents across
conversations — acting as a shared working memory that outlives
any single chat context.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass


def _slugify(text: str) -> str:
    """Turn a human string into a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-") or "untitled"


@dataclass
class KnowledgeDoc:
    subject: str
    subsubject: str
    title: str
    content: str
    updated_at: float

    @property
    def path(self) -> str:
        """Logical path used as the document identifier."""
        return f"{self.subject}/{self.subsubject}/{self.title}"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "subject": self.subject,
            "subsubject": self.subsubject,
            "title": self.title,
            "content": self.content,
            "updated_at": self.updated_at,
        }

    def summary(self) -> dict:
        """Like to_dict but without the full content."""
        return {
            "path": self.path,
            "subject": self.subject,
            "subsubject": self.subsubject,
            "title": self.title,
            "updated_at": self.updated_at,
        }

    def matches(self, query: str) -> bool:
        """Case-insensitive substring match on path + content."""
        q = query.lower()
        return (
            q in self.path.lower()
            or q in self.content.lower()
        )


class KnowledgeStore:
    """Thread-safe CRUD for knowledge documents stored as markdown files.

    Documents live at ``<base>/<subject>/<subsubject>/<title>.md``.
    The logical *path* is ``subject/subsubject/title`` (no extension).
    """

    def __init__(self, base_dir: str):
        self.base = base_dir
        os.makedirs(self.base, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _fs_path(self, subject: str, subsubject: str, title: str) -> str:
        return os.path.join(
            self.base,
            _slugify(subject),
            _slugify(subsubject),
            _slugify(title) + ".md",
        )

    def _parse_rel(self, rel: str) -> tuple[str, str, str] | None:
        """Parse a relative path like ``subject/subsubject/title.md``."""
        parts = rel.replace("\\", "/").split("/")
        if len(parts) != 3 or not parts[2].endswith(".md"):
            return None
        return parts[0], parts[1], parts[2][:-3]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        subject: str,
        subsubject: str,
        title: str,
        content: str = "",
    ) -> KnowledgeDoc:
        subject = _slugify(subject)
        subsubject = _slugify(subsubject)
        title = _slugify(title)
        path = self._fs_path(subject, subsubject, title)

        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._write(path, content)

        return KnowledgeDoc(
            subject=subject,
            subsubject=subsubject,
            title=title,
            content=content,
            updated_at=os.path.getmtime(path),
        )

    def get(self, subject: str, subsubject: str, title: str) -> KnowledgeDoc | None:
        path = self._fs_path(subject, subsubject, title)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return None

        return KnowledgeDoc(
            subject=_slugify(subject),
            subsubject=_slugify(subsubject),
            title=_slugify(title),
            content=content,
            updated_at=os.path.getmtime(path),
        )

    def get_by_path(self, doc_path: str) -> KnowledgeDoc | None:
        """Look up by logical path ``subject/subsubject/title``."""
        parts = doc_path.strip("/").split("/")
        if len(parts) != 3:
            return None
        return self.get(parts[0], parts[1], parts[2])

    def update(
        self,
        subject: str,
        subsubject: str,
        title: str,
        content: str,
    ) -> KnowledgeDoc | None:
        path = self._fs_path(subject, subsubject, title)
        if not os.path.isfile(path):
            return None

        with self._lock:
            self._write(path, content)

        return KnowledgeDoc(
            subject=_slugify(subject),
            subsubject=_slugify(subsubject),
            title=_slugify(title),
            content=content,
            updated_at=os.path.getmtime(path),
        )

    def append_content(
        self,
        subject: str,
        subsubject: str,
        title: str,
        content: str,
    ) -> KnowledgeDoc | None:
        path = self._fs_path(subject, subsubject, title)
        if not os.path.isfile(path):
            return None

        with self._lock:
            try:
                with open(path, encoding="utf-8") as f:
                    existing = f.read()
            except OSError:
                return None
            self._write(path, existing + content)

        return KnowledgeDoc(
            subject=_slugify(subject),
            subsubject=_slugify(subsubject),
            title=_slugify(title),
            content=existing + content,
            updated_at=os.path.getmtime(path),
        )

    def delete(self, subject: str, subsubject: str, title: str) -> bool:
        path = self._fs_path(subject, subsubject, title)
        if not os.path.isfile(path):
            return False
        with self._lock:
            os.remove(path)
            # clean up empty parent directories
            parent = os.path.dirname(path)
            for _ in range(2):
                if parent == self.base:
                    break
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)
        return True

    def delete_by_path(self, doc_path: str) -> bool:
        parts = doc_path.strip("/").split("/")
        if len(parts) != 3:
            return False
        return self.delete(parts[0], parts[1], parts[2])

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def subjects(self) -> list[str]:
        """Return sorted list of top-level subjects."""
        if not os.path.isdir(self.base):
            return []
        return sorted(
            name for name in os.listdir(self.base)
            if os.path.isdir(os.path.join(self.base, name))
            and not name.startswith(".")
        )

    def subsubjects(self, subject: str) -> list[str]:
        """Return sorted list of subsubjects under a subject."""
        subject_dir = os.path.join(self.base, _slugify(subject))
        if not os.path.isdir(subject_dir):
            return []
        return sorted(
            name for name in os.listdir(subject_dir)
            if os.path.isdir(os.path.join(subject_dir, name))
            and not name.startswith(".")
        )

    def tree(self) -> dict[str, dict[str, list[str]]]:
        """Return the full subject → subsubject → [titles] tree."""
        result: dict[str, dict[str, list[str]]] = {}
        for subject in self.subjects():
            result[subject] = {}
            for subsub in self.subsubjects(subject):
                titles = self._titles_in(subject, subsub)
                if titles:
                    result[subject][subsub] = titles
        return result

    def list(
        self,
        subject: str = "",
        subsubject: str = "",
    ) -> list[KnowledgeDoc]:
        """List documents, optionally filtered by subject/subsubject."""
        results: list[KnowledgeDoc] = []

        if subject and subsubject:
            for title in self._titles_in(subject, subsubject):
                doc = self.get(subject, subsubject, title)
                if doc:
                    results.append(doc)
        elif subject:
            for subsub in self.subsubjects(subject):
                for title in self._titles_in(subject, subsub):
                    doc = self.get(subject, subsub, title)
                    if doc:
                        results.append(doc)
        else:
            for subj in self.subjects():
                for subsub in self.subsubjects(subj):
                    for title in self._titles_in(subj, subsub):
                        doc = self.get(subj, subsub, title)
                        if doc:
                            results.append(doc)

        results.sort(key=lambda d: d.updated_at, reverse=True)
        return results

    def search(
        self,
        query: str,
        subject: str = "",
        subsubject: str = "",
    ) -> list[KnowledgeDoc]:
        """Full-text substring search across all documents."""
        return [
            doc for doc in self.list(subject=subject, subsubject=subsubject)
            if doc.matches(query)
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _titles_in(self, subject: str, subsubject: str) -> list[str]:
        d = os.path.join(self.base, _slugify(subject), _slugify(subsubject))
        if not os.path.isdir(d):
            return []
        return sorted(
            name[:-3] for name in os.listdir(d)
            if name.endswith(".md") and not name.startswith(".")
        )

    def _write(self, path: str, content: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
