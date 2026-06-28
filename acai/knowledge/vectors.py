"""Vector store for knowledge documents — semantic search via embeddings.

Uses SQLite for storage and numpy for similarity computation.  Embeddings
are generated via an OpenAI-compatible ``/v1/embeddings`` endpoint (vLLM,
OpenAI, or any compatible server).

The vector index lives in the same knowledge directory as the FTS database::

    workspace/knowledge/.knowledge_vectors.db

Documents are chunked before embedding so that long documents can match
on specific sections rather than only as a whole.

Usage::

    from acai.knowledge.vectors import VectorStore

    store = VectorStore(knowledge_dir, endpoint="http://localhost:5103")
    store.index_document("python/asyncio/generators", content)
    results = store.search("how do async generators work?", limit=5)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import struct
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

_DEFAULT_CHUNK_SIZE = 512
_DEFAULT_CHUNK_OVERLAP = 64
_DEFAULT_MODEL = "text-embedding"


@dataclass
class VectorHit:
    """A single search result from the vector store."""
    path: str
    chunk_index: int
    chunk_text: str
    score: float
    metadata: dict[str, Any]


def _chunk_text(text: str, chunk_size: int = _DEFAULT_CHUNK_SIZE, overlap: int = _DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by token-approximate character count.

    Uses paragraph boundaries when possible for cleaner splits.
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if not para.strip():
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) <= chunk_size:
                current = para
            else:
                # Split long paragraphs by sentences/words
                words = para.split()
                current = ""
                for word in words:
                    if len(current) + len(word) + 1 <= chunk_size:
                        current = (current + " " + word).strip()
                    else:
                        if current:
                            chunks.append(current)
                        current = word

    if current:
        chunks.append(current)

    if overlap > 0 and len(chunks) > 1:
        overlapped: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(prev_tail + " " + chunks[i])
        chunks = overlapped

    return chunks


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _serialize_vector(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _deserialize_vector(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


class EmbeddingClient:
    """Client for OpenAI-compatible embedding endpoints."""

    def __init__(self, endpoint: str, model: str = _DEFAULT_MODEL, api_key: str = ""):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        """Get embeddings for a batch of texts."""
        import requests

        url = f"{self.endpoint}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"input": texts, "model": self.model}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise EmbeddingError(f"Embedding request failed: {exc}") from exc

        data = resp.json()
        if "data" not in data:
            raise EmbeddingError(f"Unexpected embedding response: {list(data.keys())}")

        sorted_data = sorted(data["data"], key=lambda x: x["index"])
        return [np.array(item["embedding"], dtype=np.float32) for item in sorted_data]

    def embed_one(self, text: str) -> np.ndarray:
        """Get embedding for a single text."""
        results = self.embed([text])
        return results[0]

    @property
    def available(self) -> bool:
        """Check if the embedding endpoint is reachable."""
        import requests
        try:
            resp = requests.get(f"{self.endpoint}/v1/models", timeout=3)
            return resp.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""
    pass


class VectorStore:
    """SQLite-backed vector store for knowledge document chunks.

    Parameters
    ----------
    knowledge_dir : str
        Path to the knowledge directory (contains .knowledge_vectors.db).
    endpoint : str
        URL of the OpenAI-compatible embedding server.
    model : str
        Model name to use for embeddings.
    api_key : str
        Optional API key.
    chunk_size : int
        Max characters per chunk.
    chunk_overlap : int
        Overlap characters between adjacent chunks.
    """

    def __init__(
        self,
        knowledge_dir: str,
        endpoint: str = "",
        model: str = _DEFAULT_MODEL,
        api_key: str = "",
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
    ):
        self._dir = knowledge_dir
        self._db_path = os.path.join(knowledge_dir, ".knowledge_vectors.db")
        self._embedder = EmbeddingClient(endpoint, model, api_key) if endpoint else None
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._lock = threading.Lock()
        self._dim: int | None = None

        os.makedirs(knowledge_dir, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    chunk_text TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    UNIQUE(path, chunk_index)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @property
    def embedding_available(self) -> bool:
        """Check if the embedding endpoint is configured and reachable."""
        return self._embedder is not None and self._embedder.available

    def index_document(self, path: str, content: str, metadata: dict | None = None) -> int:
        """Chunk and embed a document, storing vectors in the DB.

        Returns the number of chunks indexed.  Skips re-indexing if the
        content hash hasn't changed.
        """
        if not self._embedder:
            raise EmbeddingError("No embedding endpoint configured.")

        content_hash = _content_hash(content)

        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT content_hash FROM chunks WHERE path = ? LIMIT 1",
                    (path,),
                ).fetchone()

                if existing and existing[0] == content_hash:
                    return conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE path = ?", (path,)
                    ).fetchone()[0]

                # Content changed — re-index
                conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
                conn.commit()

            chunks = _chunk_text(content, self._chunk_size, self._chunk_overlap)
            if not chunks:
                return 0

            embeddings = self._embedder.embed(chunks)
            self._dim = len(embeddings[0])

            meta_json = json.dumps(metadata or {})

            with self._connect() as conn:
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    conn.execute(
                        """INSERT OR REPLACE INTO chunks
                           (path, chunk_index, chunk_text, content_hash, embedding, metadata)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (path, i, chunk, content_hash, _serialize_vector(emb), meta_json),
                    )
                conn.commit()

            return len(chunks)

    def remove_document(self, path: str) -> int:
        """Remove all chunks for a document. Returns count removed."""
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
                conn.commit()
                return cursor.rowcount

    def search(self, query: str, limit: int = 5, path_filter: str = "") -> list[VectorHit]:
        """Semantic search — embed query and find most similar chunks.

        Parameters
        ----------
        query : str
            Natural language query.
        limit : int
            Maximum results to return.
        path_filter : str
            Optional prefix filter on document path.

        Returns
        -------
        list[VectorHit]
            Results sorted by cosine similarity (highest first).
        """
        if not self._embedder:
            raise EmbeddingError("No embedding endpoint configured.")

        query_vec = self._embedder.embed_one(query)

        with self._connect() as conn:
            if path_filter:
                rows = conn.execute(
                    "SELECT path, chunk_index, chunk_text, embedding, metadata "
                    "FROM chunks WHERE path LIKE ?",
                    (f"{path_filter}%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT path, chunk_index, chunk_text, embedding, metadata FROM chunks"
                ).fetchall()

        if not rows:
            return []

        # Compute cosine similarity for all chunks
        results: list[VectorHit] = []
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        for path, chunk_idx, chunk_text, emb_blob, meta_str in rows:
            doc_vec = _deserialize_vector(emb_blob)
            doc_norm = np.linalg.norm(doc_vec)
            if doc_norm == 0:
                continue
            similarity = float(np.dot(query_vec, doc_vec) / (query_norm * doc_norm))
            try:
                meta = json.loads(meta_str)
            except (json.JSONDecodeError, TypeError):
                meta = {}
            results.append(VectorHit(
                path=path,
                chunk_index=chunk_idx,
                chunk_text=chunk_text,
                score=similarity,
                metadata=meta,
            ))

        results.sort(key=lambda h: h.score, reverse=True)
        return results[:limit]

    def hybrid_search(
        self,
        query: str,
        fts_results: list[dict],
        limit: int = 5,
        vector_weight: float = 0.6,
        fts_weight: float = 0.4,
    ) -> list[VectorHit]:
        """Combine vector search with FTS results using reciprocal rank fusion.

        Parameters
        ----------
        query : str
            Natural language query.
        fts_results : list[dict]
            Results from KnowledgeDB.fts_search() (dicts with 'path' key).
        limit : int
            Maximum results.
        vector_weight : float
            Weight for vector similarity ranking (0-1).
        fts_weight : float
            Weight for FTS ranking (0-1).
        """
        k = 60  # RRF constant

        # Get vector results
        try:
            vec_results = self.search(query, limit=limit * 2)
        except EmbeddingError:
            vec_results = []

        # Build RRF scores by document path
        path_scores: dict[str, float] = {}
        path_chunks: dict[str, VectorHit] = {}

        for rank, hit in enumerate(vec_results):
            rrf = vector_weight / (k + rank + 1)
            if hit.path not in path_scores or rrf > path_scores.get(hit.path, 0):
                path_scores[hit.path] = path_scores.get(hit.path, 0) + rrf
                if hit.path not in path_chunks or hit.score > path_chunks[hit.path].score:
                    path_chunks[hit.path] = hit

        for rank, fts_hit in enumerate(fts_results):
            path = fts_hit.get("path", "")
            rrf = fts_weight / (k + rank + 1)
            path_scores[path] = path_scores.get(path, 0) + rrf
            if path not in path_chunks:
                path_chunks[path] = VectorHit(
                    path=path,
                    chunk_index=0,
                    chunk_text=fts_hit.get("snippet", ""),
                    score=0.0,
                    metadata={},
                )

        # Sort by combined RRF score
        ranked = sorted(path_scores.items(), key=lambda x: x[1], reverse=True)
        results: list[VectorHit] = []
        for path, combined_score in ranked[:limit]:
            hit = path_chunks[path]
            results.append(VectorHit(
                path=hit.path,
                chunk_index=hit.chunk_index,
                chunk_text=hit.chunk_text,
                score=combined_score,
                metadata=hit.metadata,
            ))

        return results

    def stats(self) -> dict[str, Any]:
        """Return index statistics."""
        with self._connect() as conn:
            total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            total_docs = conn.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()[0]
        return {
            "total_chunks": total_chunks,
            "total_documents": total_docs,
            "db_path": self._db_path,
            "embedding_available": self.embedding_available,
            "chunk_size": self._chunk_size,
            "chunk_overlap": self._chunk_overlap,
        }

    def sync(self, store) -> dict[str, int]:
        """Re-index all documents from the KnowledgeStore.

        Parameters
        ----------
        store : KnowledgeStore
            The filesystem-backed store to sync from.

        Returns
        -------
        dict with keys: indexed, skipped, removed, errors
        """
        stats = {"indexed": 0, "skipped": 0, "removed": 0, "errors": 0}

        all_docs = store.list()
        indexed_paths: set[str] = set()

        for doc in all_docs:
            indexed_paths.add(doc.path)
            try:
                count = self.index_document(doc.path, doc.content, metadata={
                    "subject": doc.subject,
                    "subsubject": doc.subsubject,
                    "title": doc.title,
                    "tags": doc.tags,
                })
                if count > 0:
                    stats["indexed"] += 1
                else:
                    stats["skipped"] += 1
            except (EmbeddingError, Exception) as exc:
                log.warning("Failed to index %s: %s", doc.path, exc)
                stats["errors"] += 1

        # Remove vectors for deleted documents
        with self._connect() as conn:
            db_paths = {
                row[0] for row in
                conn.execute("SELECT DISTINCT path FROM chunks").fetchall()
            }

        for stale_path in db_paths - indexed_paths:
            self.remove_document(stale_path)
            stats["removed"] += 1

        return stats
