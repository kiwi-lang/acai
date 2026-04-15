"""Run the orchestrator server (queue + project state)."""

from __future__ import annotations

from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup


@dataclass
class OrchestratorArguments(CommonArguments):
    host: str   = argument(default="0.0.0.0", help="bind address")
    port: int   = argument(default=5050, help="listen port")
    prefix: str = argument(default="/agent", help="URL prefix for routes")
    debug: bool = argument(default=False, help="enable debug mode")


class Orchestrator(Command):
    """Run the orchestrator server (queue + project state)."""

    name = "orchestrator"

    Arguments = OrchestratorArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        from fastapi import FastAPI
        from assai.orchestrator.server import routes

        if args.debug:
            config.dump_rendered_request = True

        app = FastAPI()
        app, socketio, queue, events, chat, config, tracker = routes(
            app, config, prefix=args.prefix,
        )

        print(f"Orchestrator on http://{args.host}:{args.port}{args.prefix}")
        socketio.run(app, host=args.host, port=args.port, debug=args.debug)
        return 0


COMMANDS = Orchestrator
