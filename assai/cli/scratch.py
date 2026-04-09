"""Run the legacy model-serving server."""

from __future__ import annotations

from dataclasses import dataclass

from argklass import argument
from argklass.command import Command


@dataclass
class ServerArguments:
    host: str   = argument(default="0.0.0.0", help="bind address")
    port: int   = argument(default=5001, help="listen port")
    debug: bool = argument(default=False, help="enable debug mode")


class Server(Command):
    """Run the legacy model-serving server."""

    name = "scratch"

    Arguments = ServerArguments

    @staticmethod
    def execute(args) -> int:
        from assai.server.run import ASSAI

        server = ASSAI()
        server.socketio.run(
            server.app,
            host=args.host,
            port=args.port,
            debug=args.debug,
        )
        return 0


COMMANDS = Server
