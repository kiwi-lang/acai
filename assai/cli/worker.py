"""Run the worker Flask server (LLM + tools, polls orchestrator)."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup


@dataclass
class WorkerArguments(CommonArguments):
    host: str = argument(default=None, help="bind address (default from config)")
    port: int = argument(default=None, help="listen port (default from config)")
    orchestrator_url: str = argument(
        default=None, help="override orchestrator URL",
    )


class Worker(Command):
    """Run the worker Flask server (LLM + tools, polls orchestrator)."""

    name = "worker"

    Arguments = WorkerArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        if args.host:
            config.worker.host = args.host
        if args.port:
            config.worker.port = args.port
        if args.orchestrator_url:
            config.worker.orchestrator_url = args.orchestrator_url

        from assai.core.worker import create_worker_app

        app, socketio, poller, llm_server = create_worker_app(config)

        threading.Thread(target=poller.run, daemon=True, name="poller").start()

        print(
            f"Worker on http://{config.worker.host}:{config.worker.port} "
            f"→ orchestrator {config.worker.orchestrator_url}"
        )
        socketio.run(
            app, host=config.worker.host, port=config.worker.port,
        )
        return 0


COMMANDS = Worker
