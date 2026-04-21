"""Knowledge management tools — persistent documents that outlive a single conversation.

Documents are stored as markdown files in a two-level hierarchy::

    workspace/knowledge/<subject>/<subsubject>/<title>.md

The logical *path* for a document is ``subject/subsubject/title``
(no extension).  Use these tools to build up a shared working memory
that agents can search and reload across conversations.
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


@tool(permissions=("write",))
def create(
    subject: str,
    subsubject: str,
    title: str,
    content: str = "",
) -> str:
    """Create a new knowledge document.

    The document is stored at ``knowledge/<subject>/<subsubject>/<title>.md``.
    All three path components are required.

    Args:
        subject: Top-level category (e.g. "python", "architecture", "milabench").
        subsubject: Sub-category (e.g. "asyncio", "decisions", "benchmarks").
        title: Document title, used as the filename (e.g. "generators", "overview").
        content: The document body in markdown.
    """
    try:
        client = _require_client()
        result = client.post("/knowledge", {
            "subject": subject,
            "subsubject": subsubject,
            "title": title,
            "content": content,
        })
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.create failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
def update(
    subject: str,
    subsubject: str,
    title: str,
    content: str,
) -> str:
    """Replace the content of an existing knowledge document.

    Args:
        subject: Top-level category.
        subsubject: Sub-category.
        title: Document title.
        content: New content body (replaces existing content entirely).
    """
    try:
        client = _require_client()
        result = client.patch(f"/knowledge/{subject}/{subsubject}/{title}", {
            "content": content,
        })
        return json.dumps(result)
    except Exception as exc:
        log.exception("knowledge.update failed")
        return json.dumps({"error": str(exc)})


@tool(permissions=("write",))
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


@tool(permissions=("read",))
def get(
    subject: str,
    subsubject: str,
    title: str,
) -> str:
    """Retrieve the full content of a knowledge document.

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


@tool(permissions=("read",))
def list_documents(
    subject: str = "",
    subsubject: str = "",
) -> str:
    """List knowledge documents as a subject/subsubject tree.

    Without arguments, returns the full tree of subjects → subsubjects → titles.
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


@tool(permissions=("read",))
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


@tool(permissions=("write",))
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
