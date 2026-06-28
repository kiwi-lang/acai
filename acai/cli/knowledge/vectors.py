"""Manage the vector index for semantic search.

Usage::

    acai knowledge vectors sync         -- re-index all documents
    acai knowledge vectors search <q>   -- semantic search
    acai knowledge vectors stats        -- show index statistics
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command, ParentCommand

from acai.cli import CommonArguments
from acai.cli.knowledge import ArgsWithQuery, get_store, truncate


def _get_vector_store(args):
    from acai.cli import setup

    config, _ = setup(args)
    knowledge_dir = os.path.join(config.workspace, "knowledge")
    endpoint = getattr(config, "embedding_endpoint", "") or os.environ.get(
        "ACAI_EMBEDDING_ENDPOINT", ""
    )
    model = getattr(config, "embedding_model", "") or os.environ.get(
        "ACAI_EMBEDDING_MODEL", "text-embedding"
    )

    from acai.knowledge.vectors import VectorStore

    return VectorStore(knowledge_dir, endpoint=endpoint, model=model)


@dataclass
class SyncArgs(CommonArguments):
    pass


class Sync(Command):
    """Re-index all knowledge documents into the vector store."""

    name = "sync"
    Arguments = SyncArgs

    @staticmethod
    def execute(args) -> int:
        vs = _get_vector_store(args)
        if not vs.embedding_available:
            print("error: embedding endpoint not reachable", file=sys.stderr)
            print("  Set ACAI_EMBEDDING_ENDPOINT or configure embedding_endpoint", file=sys.stderr)
            return 1

        store = get_store(args)
        print("Syncing vector index...")
        result = vs.sync(store)
        print(f"  indexed: {result['indexed']}")
        print(f"  skipped (unchanged): {result['skipped']}")
        print(f"  removed (stale): {result['removed']}")
        if result["errors"]:
            print(f"  errors: {result['errors']}")
        print("Done.")
        return 0


@dataclass
class VectorSearchArgs(ArgsWithQuery):
    limit: int = argument(default=5, help="max results")
    hybrid: bool = argument(default=True, help="combine with FTS (reciprocal rank fusion)")


class VectorSearch(Command):
    """Semantic search across knowledge documents."""

    name = "search"
    Arguments = VectorSearchArgs

    @staticmethod
    def execute(args) -> int:
        if not args.query:
            print("error: --query is required", file=sys.stderr)
            return 1

        vs = _get_vector_store(args)
        if not vs.embedding_available:
            print("error: embedding endpoint not reachable", file=sys.stderr)
            return 1

        if args.hybrid:
            from acai.cli import setup

            config, _ = setup(args)
            knowledge_dir = os.path.join(config.workspace, "knowledge")
            db_path = os.path.join(knowledge_dir, ".knowledge.db")

            fts_results: list[dict] = []
            if os.path.isfile(db_path):
                from acai.knowledge.db import KnowledgeDB

                db = KnowledgeDB(db_path)
                fts_results = db.fts_search(args.query, limit=args.limit)

            results = vs.hybrid_search(args.query, fts_results, limit=args.limit)
        else:
            results = vs.search(args.query, limit=args.limit)

        if not results:
            print(f'No results for "{args.query}".')
            return 0

        print(f"{'#':<4} {'Score':<8} {'Path':<40s} {'Preview'}")
        print("─" * 100)
        for i, hit in enumerate(results, 1):
            preview = truncate(hit.chunk_text, 50)
            print(f"{i:<4} {hit.score:<8.4f} {hit.path:<40s} {preview}")

        print(f"\n{len(results)} result(s)")
        return 0


class Stats(Command):
    """Show vector index statistics."""

    name = "stats"
    Arguments = CommonArguments

    @staticmethod
    def execute(args) -> int:
        vs = _get_vector_store(args)
        stats = vs.stats()
        print("Vector Index Statistics")
        print("─" * 40)
        print(f"  Documents:         {stats['total_documents']}")
        print(f"  Chunks:            {stats['total_chunks']}")
        print(f"  DB path:           {stats['db_path']}")
        print(f"  Embedding ready:   {stats['embedding_available']}")
        print(f"  Chunk size:        {stats['chunk_size']}")
        print(f"  Chunk overlap:     {stats['chunk_overlap']}")
        return 0


class Vectors(ParentCommand):
    """Manage the vector index for semantic search."""

    name: str = "vectors"

    @staticmethod
    def module():
        import acai.cli.knowledge.vectors
        return acai.cli.knowledge.vectors


COMMANDS = Vectors
