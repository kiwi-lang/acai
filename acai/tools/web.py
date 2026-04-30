"""Web tools — fetch URLs and lightweight web search (read-only)."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import quote_plus

import requests as http

from acai.orchestrator.tools import tool


class _StripHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"\s+", " ", raw)
        return raw.strip()


@tool(permissions=("read",), resources=("web:fetch",))
def fetch_url(url: str, max_chars: int = 80000) -> str:
    """Fetch a URL and return visible text (HTML stripped to plain text).

    Args:
        url: HTTP or HTTPS URL.
        max_chars: Truncate extracted text to this many characters.
    """
    try:
        resp = http.get(
            url,
            timeout=30,
            headers={"User-Agent": "acai-tools/1.0"},
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        body = resp.text
        if "html" in ctype.lower():
            p = _StripHTML()
            p.feed(body)
            text = p.text()
        else:
            text = body
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return json.dumps({
            "url": url,
            "status": resp.status_code,
            "content_type": ctype,
            "text": text,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "url": url})


@tool(permissions=("read",), resources=("web:search",))
def search_web(query: str, max_related: int = 8) -> str:
    """Search the web using DuckDuckGo instant answers (no API key).

    Args:
        query: Search query.
        max_related: Max related topic titles to include.
    """
    try:
        q = quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        resp = http.get(url, timeout=20, headers={"User-Agent": "acai-tools/1.0"})
        resp.raise_for_status()
        data = resp.json()
        abstract = data.get("Abstract") or ""
        abstract_url = data.get("AbstractURL") or ""
        related = []
        for topic in data.get("RelatedTopics", [])[: max_related * 2]:
            if isinstance(topic, dict) and "Text" in topic:
                related.append(topic["Text"])
            elif isinstance(topic, dict) and "Topics" in topic:
                for sub in topic.get("Topics", []):
                    if isinstance(sub, dict) and "Text" in sub:
                        related.append(sub["Text"])
            if len(related) >= max_related:
                break
        return json.dumps({
            "query": query,
            "abstract": abstract,
            "abstract_url": abstract_url,
            "related": related,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc), "query": query})
