"""Sync the knowledge SQLite index from filesystem documents.

Usage::

    acai knowledge sync
    acai knowledge sync --set-tags python/asyncio/generators "async,coroutine"
    acai knowledge sync --set-facet python/asyncio/generators personality python
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from acai.cli import CommonArguments
from acai.cli.knowledge import get_store


def _get_db(args):
    """Build a KnowledgeDB from CLI args."""
    from acai.cli import setup

    config, _ = setup(args)
    from acai.knowledge import KnowledgeDB

    db_path = os.path.join(config.workspace, "knowledge", ".knowledge.db")
    return KnowledgeDB(db_path), config


@dataclass
class SyncArgs(CommonArguments):
    set_tags: str = argument(
        default="", help="set tags on a doc: path 'tag1,tag2'", nargs="*"
    )
    set_facet: str = argument(
        default="", help="set a facet: path <facet> <value>", nargs="*"
    )


class Sync(Command):
    """Sync the knowledge database index from filesystem documents."""

    name = "sync"
    Arguments = SyncArgs

    @staticmethod
    def execute(args) -> int:
        db, config = _get_db(args)
        knowledge_dir = os.path.join(config.workspace, "knowledge")

        if args.set_tags:
            return _handle_set_tags(db, args.set_tags)

        if args.set_facet:
            return _handle_set_facet(db, args.set_facet)

        result = db.sync(knowledge_dir)
        print("Sync complete:")
        print(f"  Added:   {result['added']}")
        print(f"  Updated: {result['updated']}")
        print(f"  Removed: {result['removed']}")
        print(f"  Total:   {result['total']}")
        return 0


def _handle_set_tags(db, tag_args: list[str]) -> int:
    if len(tag_args) < 2:
        print("Usage: --set-tags <path> <tag1,tag2,...>")
        return 1
    path = tag_args[0]
    tags = [t.strip() for t in tag_args[1].split(",") if t.strip()]

    existing = db.get(path)
    if existing is None:
        print(f"Document not found in index: {path}")
        print("Run 'acai knowledge sync' first to index documents.")
        return 1

    db.upsert(
        existing["subject"],
        existing["subsubject"],
        existing["title"],
        tags=tags,
        facets=existing["facets"],
        updated_at=existing["updated_at"],
    )
    print(f"Tags set on {path}: {tags}")
    return 0


def _handle_set_facet(db, facet_args: list[str]) -> int:
    from acai.knowledge import FACETS

    if len(facet_args) < 3:
        print(f"Usage: --set-facet <path> <facet> <value>")
        print(f"  Facets: {', '.join(FACETS)}")
        return 1
    path = facet_args[0]
    facet = facet_args[1]
    value = facet_args[2]

    if facet not in FACETS:
        print(f"Unknown facet: {facet!r}. Must be one of: {', '.join(FACETS)}")
        return 1

    existing = db.get(path)
    if existing is None:
        print(f"Document not found in index: {path}")
        print("Run 'acai knowledge sync' first to index documents.")
        return 1

    facets = existing["facets"].copy()
    facets[facet] = value

    db.upsert(
        existing["subject"],
        existing["subsubject"],
        existing["title"],
        tags=existing["tags"],
        facets=facets,
        updated_at=existing["updated_at"],
    )
    print(f"Facet '{facet}' set on {path}: {value}")
    return 0


COMMANDS = Sync
