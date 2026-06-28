"""Knowledge module — persistent document storage with SQLite metadata index.

Layout::

    workspace/knowledge/<subject>/<subsubject>/<title>.md

Documents are plain markdown files.  Metadata (tags, classification) is
indexed in a SQLite database for fast queries.

Vector embeddings for semantic search are stored in a separate SQLite DB
at ``workspace/knowledge/.knowledge_vectors.db``.
"""

from .db import KnowledgeDB
from .models import FACETS, Facets, KnowledgeDoc, slugify
from .store import KnowledgeStore
from .vectors import EmbeddingError, VectorHit, VectorStore

__all__ = [
    "EmbeddingError",
    "FACETS",
    "Facets",
    "KnowledgeDB",
    "KnowledgeDoc",
    "KnowledgeStore",
    "VectorHit",
    "VectorStore",
    "slugify",
]
