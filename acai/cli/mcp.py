"""Standalone tool server for use inside sandbox containers.

Runs a minimal app that exposes the tool registry over HTTP.
No LLM server, no orchestrator connection, no SocketIO -- just tools.

Usage::

    acai mcp                      # default port 9200
    acai mcp --port 9300          # custom port
    acai mcp --host 0.0.0.0      # bind to all interfaces
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from acai.cli import CommonArguments


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

        import uvicorn
        from fastapi import FastAPI

        from acai.orchestrator.tools import discover_tools

        registry = discover_tools()
        from acai.tools.meta import _configure as configure_meta_tools

        configure_meta_tools(registry)

        app = FastAPI()
        tool_router = registry.router(url_prefix="/tools")
        app.include_router(tool_router)

        @app.get("/health")
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
            uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        except KeyboardInterrupt:
            pass

        return 0


COMMANDS = Mcp
