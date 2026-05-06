"""Knowledge module — persistent document storage with SQLite metadata index.

Layout::

    workspace/knowledge/<subject>/<subsubject>/<title>.md

Documents are plain markdown files.  Metadata (tags, classification) is
indexed in a SQLite database for fast queries.
"""

from .db import KnowledgeDB
from .models import FACETS, Facets, KnowledgeDoc, slugify
from .store import KnowledgeStore

__all__ = [
    "FACETS",
    "Facets",
    "KnowledgeDB",
    "KnowledgeDoc",
    "KnowledgeStore",
    "slugify",
]
