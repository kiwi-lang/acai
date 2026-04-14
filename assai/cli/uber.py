"""Orchestrator + worker in one process (Spark GB10 mode).

Registers both the orchestrator (``/agent``) and worker (``/worker``)
routers into a single FastAPI app with a shared SocketIO instance.

LLM streaming uses SSE end-to-end: the worker streams to the poller,
which relays to the orchestrator, which serves an SSE endpoint to the UI.
SocketIO is kept only for broadcast events (tasks, status, telemetry, etc.).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup


@dataclass
class UberArguments(CommonArguments):
    host: str   = argument(default="0.0.0.0", help="bind address")
    port: int   = argument(default=5050, help="listen port")
    prefix: str = argument(default="/agent", help="URL prefix for orchestrator routes")
    debug: bool = argument(default=False, help="enable debug mode")
    extern_llm: bool = argument(
        default=False,
        help="skip internal LLM management — use an externally started server (e.g. via `assai serve`)",
    )


class Uber(Command):
    """Orchestrator + worker + HTTP API in one process (Spark GB10 mode)."""

    name = "uber"

    Arguments = UberArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        from fastapi import FastAPI
        from assai.core.server import routes
        from assai.core.worker import (
            WorkerPoller,
            _setup_telemetry,
            create_worker_router,
        )

        if args.debug:
            config.dump_rendered_request = True

        app = FastAPI()
        app, socketio, queue, events, chat, config, stream_tracker = routes(
            app, config, prefix=args.prefix,
        )

        router, llm_server, registry = create_worker_router(
            config, prefix="/worker",
            extern_llm=args.extern_llm,
        )
        tool_router = registry.router(url_prefix="/tools")
        app.include_router(router)
        app.include_router(tool_router)

        _setup_telemetry(socketio)

        worker_url = f"http://127.0.0.1:{args.port}/worker"
        poller = WorkerPoller(
            config=config,
            orchestrator_url=f"http://127.0.0.1:{args.port}{args.prefix}",
            worker_url=worker_url,
            llm_server=llm_server,
            registry=registry,
        )
        threading.Thread(target=poller.run, daemon=True, name="poller").start()

        if args.extern_llm:
            active = config.active_provider()
            print(f"Uber server on http://{args.host}:{args.port} (external LLM at {active.endpoint})")
        else:
            print(f"Uber server on http://{args.host}:{args.port}")
        socketio.run(app, host=args.host, port=args.port, debug=args.debug)
        return 0


COMMANDS = Uber
