"""Orchestrator + worker in one process (Spark GB10 mode).

Registers both the orchestrator (``/agent``) and worker (``/worker``)
routers into a single FastAPI app with a shared SocketIO instance.

LLM streaming uses SSE end-to-end: the orchestrator dispatches work
directly to the local worker via HTTP and consumes the SSE stream.
SocketIO is kept only for broadcast events (tasks, status, telemetry, etc.).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from acai.cli import CommonArguments, setup

_ENV_KEY = "_ACAI_UBER_ARGS"


def create_app():
    """App factory called by uvicorn on each reload.

    Reads the uber CLI args from ``_ACAI_UBER_ARGS`` env var
    (set by ``Uber.execute`` before spawning uvicorn) and rebuilds the
    full ASGI application from scratch, picking up any code changes.
    """
    raw = os.environ.get(_ENV_KEY)
    if not raw:
        raise RuntimeError(
            "create_app() requires the _ACAI_UBER_ARGS env var. "
            "Run via `acai uber --debug 1`."
        )
    uber = json.loads(raw)

    from acai.cli import setup as _setup

    class _Args:
        config = uber.get("config")
        db = uber.get("db")
        verbose = uber.get("verbose", False)

    config, _ = _setup(_Args())
    if uber.get("debug"):
        config.dump_rendered_request = True

    from fastapi import FastAPI
    from acai.orchestrator.load_balancer import LoadBalancer
    from acai.orchestrator.server import routes
    from acai.worker.app import (
        _setup_telemetry,
        create_worker_router,
    )

    prefix = uber.get("prefix", "/agent")
    port = uber.get("port", 5050)
    extern_llm = uber.get("extern_llm", False)

    lb = LoadBalancer()

    app = FastAPI()
    app, socketio, queue, events, chat, config, stream_tracker, lb = routes(
        app, config, prefix=prefix, load_balancer=lb,
    )

    router, llm_server, registry, sandbox_proxy = create_worker_router(
        config, prefix="/worker",
        extern_llm=extern_llm,
    )
    tool_router = registry.router(url_prefix="/tools", sandbox_proxy=sandbox_proxy)
    app.include_router(router)
    app.include_router(tool_router)

    _setup_telemetry(socketio)

    worker_url = f"http://127.0.0.1:{port}/worker"
    active = config.active_provider()
    capabilities = {
        "tools": [td.qualified_name for td in registry.all_tools()],
        "namespaces": registry.namespaces(),
        "model": active.model,
        "backend": active.backend,
    }
    worker_id = lb.register(worker_url, capabilities)

    import threading
    def _local_heartbeat():
        """Keep the in-process worker alive in the load balancer."""
        while True:
            lb.heartbeat(worker_id)
            threading.Event().wait(10)

    threading.Thread(target=_local_heartbeat, daemon=True, name="uber-heartbeat").start()

    return socketio.make_asgi(app)


@dataclass
class UberArguments(CommonArguments):
    host: str   = argument(default="0.0.0.0", help="bind address")
    port: int   = argument(default=5050, help="listen port")
    prefix: str = argument(default="/agent", help="URL prefix for orchestrator routes")
    debug: bool = argument(default=False, help="enable debug mode")
    extern_llm: bool = argument(
        default=False,
        help="skip internal LLM management — use an externally started server (e.g. via `acai serve`)",
    )


class Uber(Command):
    """Orchestrator + worker + HTTP API in one process (Spark GB10 mode)."""

    name = "uber"

    Arguments = UberArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        if args.debug:
            config.dump_rendered_request = True

        import logging
        import uvicorn

        for _name in ("engineio", "engineio.server", "engineio.client",
                       "socketio", "socketio.server", "socketio.client"):
            logging.getLogger(_name).setLevel(logging.WARNING)

        if args.extern_llm:
            active = config.active_provider()
            print(f"Uber server on http://{args.host}:{args.port} (external LLM at {active.endpoint})")
        else:
            print(f"Uber server on http://{args.host}:{args.port}")

        if args.debug:
            os.environ[_ENV_KEY] = json.dumps({
                "host": args.host,
                "port": args.port,
                "prefix": args.prefix,
                "debug": True,
                "extern_llm": args.extern_llm,
                "config": getattr(args, "config", None),
                "db": getattr(args, "db", None),
                "verbose": getattr(args, "verbose", False),
            })

            src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            uvicorn.run(
                "acai.cli.uber:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                log_level="info",
                reload=True,
                reload_dirs=[src_dir],
            )
        else:
            os.environ[_ENV_KEY] = json.dumps({
                "host": args.host,
                "port": args.port,
                "prefix": args.prefix,
                "debug": False,
                "extern_llm": args.extern_llm,
                "config": getattr(args, "config", None),
                "db": getattr(args, "db", None),
                "verbose": getattr(args, "verbose", False),
            })
            combined = create_app()
            uvicorn.run(
                combined,
                host=args.host,
                port=args.port,
                log_level="info",
            )

        return 0


COMMANDS = Uber
