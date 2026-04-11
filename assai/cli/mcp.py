"""Standalone tool server for use inside sandbox containers.

Runs a minimal Flask app that exposes the tool registry over HTTP.
No LLM server, no orchestrator connection, no SocketIO -- just tools.

Usage::

    assai mcp                      # default port 9200
    assai mcp --port 9300          # custom port
    assai mcp --host 0.0.0.0      # bind to all interfaces
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments


@dataclass
class McpArguments(CommonArguments):
    host: str = argument(default="0.0.0.0", help="bind address")
    port: int = argument(default=9200, help="listen port")


class Mcp(Command):
    """Run the tool server (for sandbox containers).

    Starts a lightweight HTTP server that exposes code, git, shell,
    and filesystem tools via ``POST /tools/call`` and
    ``GET /tools/list``.  Designed to run inside a Podman/Docker
    container with the project worktree bind-mounted at ``/workspace``.
    """

    name = "mcp"

    Arguments = McpArguments

    @staticmethod
    def execute(args) -> int:
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
            datefmt="%H:%M:%S",
        )
        log = logging.getLogger(__name__)

        from flask import Flask

        from assai.core.tools import discover_tools

        registry = discover_tools()

        app = Flask(__name__)
        tool_bp = registry.blueprint(url_prefix="/tools")
        app.register_blueprint(tool_bp)

        @app.route("/health")
        def health():
            return {"ok": True, "tools": len(registry.all_tools())}

        tools = [t.qualified_name for t in registry.all_tools()]
        log.info(
            "mcp tool server starting  host=%s  port=%d  tools=%d",
            args.host, args.port, len(tools),
        )
        for t in sorted(tools):
            log.info("  %s", t)

        try:
            app.run(host=args.host, port=args.port, debug=False)
        except KeyboardInterrupt:
            pass

        return 0


COMMANDS = Mcp
