"""Knowledge management tools — persistent documents that outlive a single conversation.

Documents are stored as markdown files in a two-level hierarchy::

    workspace/knowledge/<subject>/<subsubject>/<title>.md

The logical *path* for a document is ``subject/subsubject/title``
(no extension).  Use these tools to build up a shared working memory
that agents can search and reload across conversations.

Each document can carry:
- **tags**: free-form labels for quick filtering
- **facets**: PMEST classification (personality/matter/energy/space/time)
"""

from __future__ import annotations

import json
import logging

from acai.orchestrator.context import current_context, current_client
from acai.orchestrator.tools import tool

log = logging.getLogger(__name__)


def _require_client():
    client = current_client()
    if client is None:
        raise RuntimeError("no orchestrator client in worker context")
    return client


@tool(permissions=("write",), resources=("knowledge:create",))
def create(
    subject: str,
    subsubject: str,
    title: str,
    content: str = "",
    tags: list[str] | None = None,
    facets: dict[str, str] | None = None,
) -> str:
    """Create a new knowledge document.

    The document is stored at ``knowledge/<subject>/<subsubject>/<title>.md``.
    All three path components are required.

    Args:
        subject: Top-level category (e.g. "python", "architecture", "milabench").
        subsubject: Sub-category (e.g. "asyncio", "decisions", "benchmarks").
        title: Document title, used as the filename (e.g. "generators", "overview").
        content: The document body in markdown.
        tags: List of free-form tags for filtering (e.g. ["async", "coroutine"]).
        facets: PMEST faceted classification dict. Keys:
            - personality: what (the essential entity/topic)
            - matter: material (substance, medium, constituent)
            - energy: action (operation, process, activity)
            - space: where (geographic/logical location)
            - time: when (temporal period, date, version)
    """
    try:
        client = _require_client()
        payload: dict = {
            "subject": subject,
            "subsubject": subsubject,
            "title": title,
            "content": content,
        }
        if tags:
            payload["tags"] = tags
        if facets:
            payload["facets"] = facets
        result = client.post("/knowledge", payload)
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.create failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), resources=("knowledge:update",))
def update(
    subject: str,
    subsubject: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
    facets: dict[str, str] | None = None,
) -> str:
    """Replace the content of an existing knowledge document.

    Args:
        subject: Top-level category.
        subsubject: Sub-category.
        title: Document title.
        content: New content body (replaces existing content entirely).
        tags: Updated tags (omit to keep existing tags unchanged).
        facets: Updated PMEST facets dict (omit to keep existing facets unchanged).
            Keys: personality, matter, energy, space, time.
    """
    try:
        client = _require_client()
        payload: dict = {"content": content}
        if tags is not None:
            payload["tags"] = tags
        if facets is not None:
            payload["facets"] = facets
        result = client.patch(f"/knowledge/{subject}/{subsubject}/{title}", payload)
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.update failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), resources=("knowledge:update",))
def append(
    subject: str,
    subsubject: str,
    title: str,
    content: str,
) -> str:
    """Append content to an existing knowledge document.

    Args:
        subject: Top-level category.
        subsubject: Sub-category.
        title: Document title.
        content: Text to append to the document body.
    """
    try:
        client = _require_client()
        result = client.post(
            f"/knowledge/{subject}/{subsubject}/{title}/append",
            {"content": content},
        )
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.append failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("knowledge:read",))
def get(
    subject: str,
    subsubject: str,
    title: str,
) -> str:
    """Retrieve the full content of a knowledge document (including tags and facets).

    Args:
        subject: Top-level category.
        subsubject: Sub-category.
        title: Document title.
    """
    try:
        client = _require_client()
        result = client.get(f"/knowledge/{subject}/{subsubject}/{title}")
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.get failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("knowledge:read",))
def list_documents(
    subject: str = "",
    subsubject: str = "",
) -> str:
    """List knowledge documents as a subject/subsubject tree.

    Without arguments, returns the full tree of subjects -> subsubjects -> titles.
    With a subject, returns only that subject's subsubjects and documents.
    With both, returns only the documents in that path.

    Args:
        subject: Filter to a specific subject (empty for all).
        subsubject: Filter to a specific subsubject (empty for all under subject).
    """
    try:
        client = _require_client()
        params: dict[str, str] = {}
        if subject:
            params["subject"] = subject
        if subsubject:
            params["subsubject"] = subsubject
        result = client.get("/knowledge", params)
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.list_documents failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("knowledge:read",))
def query_documents(
    subject: str = "",
    subsubject: str = "",
    tag: str = "",
    personality: str = "",
    matter: str = "",
    energy: str = "",
    space: str = "",
    time: str = "",
) -> str:
    """Query knowledge documents by metadata using faceted classification.

    Fast indexed lookup on any combination of filters (AND logic).
    Returns document metadata (path, tags, facets) without content.

    Facet filters use substring matching — e.g. personality="python"
    will match "python-stdlib" and "python".

    Args:
        subject: Filter by top-level subject (exact match).
        subsubject: Filter by sub-category (exact match).
        tag: Filter by tag (documents containing this tag).
        personality: Filter by personality facet — *what* (entity/topic).
        matter: Filter by matter facet — *material* (substance/medium).
        energy: Filter by energy facet — *action* (process/operation).
        space: Filter by space facet — *where* (location/context).
        time: Filter by time facet — *when* (period/date/version).
    """
    try:
        client = _require_client()
        params: dict[str, str] = {}
        if subject:
            params["subject"] = subject
        if subsubject:
            params["subsubject"] = subsubject
        if tag:
            params["tag"] = tag
        if personality:
            params["personality"] = personality
        if matter:
            params["matter"] = matter
        if energy:
            params["energy"] = energy
        if space:
            params["space"] = space
        if time:
            params["time"] = time
        result = client.get("/knowledge/query", params)
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.query_documents failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("read",), resources=("knowledge:read",))
def search(
    query: str,
    subject: str = "",
    subsubject: str = "",
) -> str:
    """Search knowledge documents by text query.

    Performs a case-insensitive substring search across document
    paths and content.

    Args:
        query: The search string.
        subject: Restrict search to a subject (empty for all).
        subsubject: Restrict search to a subsubject (empty for all).
    """
    try:
        client = _require_client()
        params: dict[str, str] = {"q": query}
        if subject:
            params["subject"] = subject
        if subsubject:
            params["subsubject"] = subsubject
        result = client.get("/knowledge/search", params)
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.search failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",), resources=("knowledge:delete",))
def delete(
    subject: str,
    subsubject: str,
    title: str,
) -> str:
    """Delete a knowledge document permanently.

    Args:
        subject: Top-level category.
        subsubject: Sub-category.
        title: Document title.
    """
    try:
        client = _require_client()
        resp = client.post(
            f"/knowledge/{subject}/{subsubject}/{title}/delete", {},
        )
        return json.dumps(resp)
    except Exception as exc:
        log.exception("knowledge.delete failed")
        return json.dumps({"error": str(exc)})
