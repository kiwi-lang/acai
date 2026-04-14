"""SocketIO compatibility layer for FastAPI.

Provides a ``SocketIO`` wrapper around ``python-socketio.AsyncServer``
that exposes a Flask-SocketIO-compatible API:

* ``.on(event)`` decorator strips the ``sid`` parameter.
* ``.emit()`` works from any thread by scheduling on the main loop.
* ``.run()`` boots ``uvicorn`` with the combined ASGI app.

Also provides module-level ``join_room``, ``leave_room``, ``emit``
helpers that operate on the current client's ``sid``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import threading
from contextvars import ContextVar
from typing import Any

import socketio as _sio_module
from fastapi import FastAPI as _FastAPI

_log = logging.getLogger(__name__)

# ======================================================================
# Context variables
# ======================================================================

_current_sid: ContextVar[str] = ContextVar("_sio_sid", default="")
_main_loop_ref: list[asyncio.AbstractEventLoop | None] = [None]
_current_sio: SocketIO | None = None  # set by SocketIO.__init__


# ======================================================================
# SocketIO  (Flask-SocketIO compatible wrapper)
# ======================================================================

class SocketIO:
    """Flask-SocketIO compatible wrapper around ``python-socketio.AsyncServer``.

    * ``async_mode`` is silently ignored (always ASGI).
    * ``.emit()`` works from **any** thread (sync or async).
    * ``.run()`` boots ``uvicorn`` instead of ``eventlet`` / ``gevent``.
    * Handler decorators strip the ``sid`` parameter so existing
      Flask-SocketIO handlers keep their original signatures.
    """

    def __init__(
        self,
        app=None,
        cors_allowed_origins: str | list[str] | None = None,
        async_mode: str | None = None,
        **kwargs,
    ):
        global _current_sio
        sio_kwargs: dict[str, Any] = {}
        if cors_allowed_origins is not None:
            sio_kwargs["cors_allowed_origins"] = cors_allowed_origins
        self.server = _sio_module.AsyncServer(
            async_mode="asgi",
            logger=False,
            engineio_logger=False,
            **sio_kwargs,
        )
        self._app = None
        _current_sio = self
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self._app = app

    # -- event decorator ------------------------------------------------

    def on(self, event: str):
        """Decorator for SocketIO event handlers.

        Strips the ``sid`` argument that ``python-socketio`` injects so
        Flask-SocketIO-style handlers (which have no ``sid`` param)
        continue to work unchanged.
        """

        def decorator(fn):
            params = list(inspect.signature(fn).parameters)
            n_params = len(params)
            is_async = asyncio.iscoroutinefunction(fn)

            if is_async:
                @functools.wraps(fn)
                async def wrapper(sid, *args):
                    token = _current_sid.set(sid)
                    try:
                        return await fn(*args[:n_params])
                    finally:
                        _current_sid.reset(token)
            else:
                @functools.wraps(fn)
                def wrapper(sid, *args):
                    token = _current_sid.set(sid)
                    try:
                        return fn(*args[:n_params])
                    finally:
                        _current_sid.reset(token)

            self.server.on(event)(wrapper)
            return fn

        return decorator

    # -- emit -----------------------------------------------------------

    def emit(self, event: str, data: Any = None, **kwargs):
        """Emit from **any** context (sync route, async route, background
        thread).  The coroutine is scheduled on the main event loop via
        ``run_coroutine_threadsafe``."""
        loop = _main_loop_ref[0]
        if loop is None:
            return
        coro = self.server.emit(event, data, **kwargs)
        asyncio.run_coroutine_threadsafe(coro, loop)

    # -- helpers --------------------------------------------------------

    @staticmethod
    def sleep(seconds: float):
        """Blocking sleep (for use in background threads)."""
        import time
        time.sleep(seconds)

    @staticmethod
    def start_background_task(target, *args):
        t = threading.Thread(target=target, args=args, daemon=True)
        t.start()
        return t

    # -- run (replaces socketio.run) ------------------------------------

    def run(
        self,
        app,
        host: str = "127.0.0.1",
        port: int = 5000,
        debug: bool = False,
        **kwargs,
    ):
        """Start the server with ``uvicorn``.

        *debug* sets the uvicorn log level to ``"info"`` (not ``"debug"``
        to avoid flooding from websocket frames).  For auto-reload on
        file changes use ``uvicorn --reload`` with an import-path string
        instead.
        """
        import uvicorn

        # Silence engineio/socketio wire-level logs regardless of the
        # root log level — these flood the console with every WS frame.
        for _name in ("engineio", "engineio.server", "engineio.client",
                       "socketio", "socketio.server", "socketio.client"):
            logging.getLogger(_name).setLevel(logging.WARNING)

        asgi_app = app if isinstance(app, _FastAPI) else getattr(app, "app", app)

        @asgi_app.on_event("startup")
        async def _capture_loop():
            _main_loop_ref[0] = asyncio.get_running_loop()

        combined = _sio_module.ASGIApp(self.server, asgi_app)
        uvicorn.run(
            combined,
            host=host,
            port=port,
            log_level="info",
        )


# ======================================================================
# Module-level SocketIO helpers  (flask_socketio.join_room  etc.)
# ======================================================================

def join_room(room: str, namespace: str | None = None):
    sid = _current_sid.get("")
    if _current_sio is not None and sid:
        _current_sio.server.enter_room(sid, room, namespace=namespace)


def leave_room(room: str, namespace: str | None = None):
    sid = _current_sid.get("")
    if _current_sio is not None and sid:
        _current_sio.server.leave_room(sid, room, namespace=namespace)


def emit(event: str, data: Any = None, **kwargs):
    """Module-level ``emit`` — sends to the **current client** by default
    (mirrors ``flask_socketio.emit``)."""
    sid = _current_sid.get("")
    if _current_sio is None:
        return
    if sid and "to" not in kwargs and "room" not in kwargs:
        kwargs["room"] = sid
    _current_sio.emit(event, data, **kwargs)
