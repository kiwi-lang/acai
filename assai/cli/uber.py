"""Orchestrator + worker in one process (Spark GB10 mode).

Registers both the orchestrator (``/agent``) and worker (``/worker``)
blueprints into a single Flask app with a shared SocketIO instance.
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
    debug: bool = argument(default=False, help="enable Flask debug mode")


class Uber(Command):
    """Orchestrator + worker + HTTP API in one process (Spark GB10 mode)."""

    name = "uber"

    Arguments = UberArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        from flask import Flask
        from assai.agents.server import routes
        from assai.agents.worker import (
            WorkerPoller,
            _setup_telemetry,
            create_worker_blueprint,
        )

        app = Flask(__name__)
        app, socketio, queue, events, chat, config = routes(
            app, config, prefix=args.prefix,
        )

        bp, llm_server, registry = create_worker_blueprint(
            config, socketio=socketio, prefix="/worker",
        )
        tool_bp = registry.blueprint(url_prefix="/tools")
        app.register_blueprint(bp)
        app.register_blueprint(tool_bp)

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

        print(f"Uber server on http://{args.host}:{args.port}")
        socketio.run(app, host=args.host, port=args.port, debug=args.debug)
        return 0


COMMANDS = Uber
