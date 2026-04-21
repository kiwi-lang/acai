"""SSE iterators — sync and async HTTP clients that yield parsed SSE events.

Both ``SSEIterator`` (sync, uses ``requests``) and ``AsyncSSEIterator``
(async, uses ``aiohttp``) open a streaming connection to a URL and
yield ``ServerSentEvent`` objects parsed from the ``text/event-stream``
response.

Usage
-----

Sync::

    for event in SSEIterator("http://localhost:8000/v1/stream", json=payload):
        print(event.event, event.data)

Async::

    async for event in AsyncSSEIterator("http://localhost:8000/v1/stream", json=payload):
        print(event.event, event.data)
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator, AsyncIterator

import requests as _requests

log = logging.getLogger(__name__)


@dataclass
class ServerSentEvent:
    """A single SSE frame parsed from a ``text/event-stream`` response.

    Fields follow the SSE spec:
    * ``event`` — the event type (from ``event:`` line), empty string if absent.
    * ``data``  — the data payload (from ``data:`` line(s)), empty string if absent.
    * ``id``    — the event id (from ``id:`` line), empty string if absent.
    * ``retry`` — reconnection time in ms (from ``retry:`` line), ``None`` if absent.
    """
    event: str = ""
    data: str = ""
    id: str = ""
    retry: int | None = None

    def json(self) -> Any:
        """Parse ``self.data`` as JSON."""
        return _json.loads(self.data)


def _parse_sse_lines(lines: list[str]) -> ServerSentEvent:
    """Build a ``ServerSentEvent`` from the accumulated lines of one SSE frame."""
    evt = ServerSentEvent()
    data_parts: list[str] = []
    for line in lines:
        if line.startswith("event:"):
            evt.event = line[6:].strip()
        elif line.startswith("data:"):
            data_parts.append(line[5:].strip())
        elif line.startswith("id:"):
            evt.id = line[3:].strip()
        elif line.startswith("retry:"):
            try:
                evt.retry = int(line[6:].strip())
            except ValueError:
                pass
    evt.data = "\n".join(data_parts)
    return evt


def _iter_sse_frames(raw_lines: Iterator[str]) -> Iterator[ServerSentEvent]:
    """Group raw lines by blank-line separators and yield parsed events."""
    buf: list[str] = []
    for line in raw_lines:
        if not line:
            if buf:
                yield _parse_sse_lines(buf)
                buf = []
        else:
            buf.append(line)
    if buf:
        yield _parse_sse_lines(buf)


async def _aiter_sse_frames(raw_lines: AsyncIterator[str]) -> AsyncIterator[ServerSentEvent]:
    """Async version of ``_iter_sse_frames``."""
    buf: list[str] = []
    async for line in raw_lines:
        if not line:
            if buf:
                yield _parse_sse_lines(buf)
                buf = []
        else:
            buf.append(line)
    if buf:
        yield _parse_sse_lines(buf)


class SSEIterator:
    """Sync SSE iterator backed by ``requests``.

    Yields ``ServerSentEvent`` objects.
    """

    def __init__(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        json: Any = None,
        data: Any = None,
        timeout: float = 600,
        params: dict[str, str] | None = None,
        **req_kwargs,
    ):
        self.url = url
        self.method = method
        self.headers = headers
        self.json = json
        self.data = data
        self.timeout = timeout
        self.params = params
        self.req_kwargs = req_kwargs
        self._response: _requests.Response | None = None

    def _raw_lines(self) -> Iterator[str]:
        """Yield every line (including blanks) from the HTTP response."""
        self._response = _requests.request(
            self.method,
            self.url,
            headers=self.headers,
            json=self.json,
            data=self.data,
            timeout=self.timeout,
            params=self.params,
            stream=True,
            **self.req_kwargs,
        )
        self._response.raise_for_status()
        yield from self._response.iter_lines(decode_unicode=True)

    def __iter__(self) -> Iterator[ServerSentEvent]:
        try:
            yield from _iter_sse_frames(self._raw_lines())
        finally:
            self.close()

    def close(self):
        if self._response is not None:
            self._response.close()


class AsyncSSEIterator:
    """Async SSE iterator backed by ``aiohttp``.

    Yields ``ServerSentEvent`` objects.
    """

    def __init__(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        json: Any = None,
        data: Any = None,
        timeout: float = 600,
        params: dict[str, str] | None = None,
    ):
        self.url = url
        self.method = method
        self.headers = headers
        self.json = json
        self.data = data
        self.timeout = timeout
        self.params = params
        self._session = None
        self._response = None

    async def _raw_lines(self) -> AsyncIterator[str]:
        """Yield every line (including blanks) from the HTTP response."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)

        kwargs: dict[str, Any] = {}
        if self.headers:
            kwargs["headers"] = self.headers
        if self.json is not None:
            kwargs["headers"] = {**(self.headers or {}), "Content-Type": "application/json"}
            kwargs["data"] = _json.dumps(self.json)
        elif self.data is not None:
            kwargs["data"] = self.data
        if self.params:
            kwargs["params"] = self.params

        self._response = await self._session.request(self.method, self.url, **kwargs)
        self._response.raise_for_status()

        async for raw_bytes in self._response.content:
            for line in raw_bytes.decode("utf-8", errors="replace").splitlines():
                yield line

    async def __aiter__(self) -> AsyncIterator[ServerSentEvent]:
        try:
            async for event in _aiter_sse_frames(self._raw_lines()):
                yield event
        finally:
            await self.close()

    async def close(self):
        if self._response is not None:
            self._response.close()
            self._response = None
        if self._session is not None:
            await self._session.close()
            self._session = None
