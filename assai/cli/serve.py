"""Launch the LLM server (vLLM / llama.cpp) as a standalone process.

Usage::

    assai serve                            # use defaults from config
    assai serve --model Qwen/Qwen3-Coder-Next-FP8 --port 8000
"""

from __future__ import annotations

import signal
import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup


@dataclass
class ServeArguments(CommonArguments):
    model: str = argument(default=None, help="override model name/path")
    backend: str = argument(default=None, help="override backend (vllm, llamacpp)")
    port: int = argument(default=None, help="override server port")
    server_command: str = argument(
        default=None,
        help="provide an explicit server_command instead of auto-generating one",
    )


class Serve(Command):
    """Launch the LLM server as a standalone process.

    The process runs in the foreground so that you can inspect logs
    directly.  Use ``assai uber --extern-llm`` in another terminal to
    run the agent stack against this instance.
    """

    name = "serve"

    Arguments = ServeArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        if args.model:
            config.llm.model = args.model
            from assai.core.config import _model_to_slug
            config.llm.slug = _model_to_slug(args.model)
        if args.backend:
            config.llm.backend = args.backend
        if args.port:
            config.llm.server_port = args.port
            config.llm.endpoint = f"http://127.0.0.1:{args.port}"
        if args.server_command:
            config.llm.server_command = args.server_command

        from assai.core.llm import LLMServer, LLMServerError

        server = LLMServer(config.llm, workspace=config.workspace)

        def _shutdown(sig, frame):
            print(f"\nReceived signal {sig}, shutting down LLM server...")
            server.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        print(f"Launching LLM server  model={config.llm.model}  backend={config.llm.backend}")
        print(f"Endpoint will be at {config.llm.endpoint}")

        try:
            server.start()
        except LLMServerError as exc:
            print(f"\nFailed to start LLM server:\n{exc}", file=sys.stderr)
            return 1

        print(f"LLM server healthy  pid={server.pid}")
        print(f"Logs: {server.latest_log_path()}")
        print("Press Ctrl+C to stop.\n")

        try:
            server.process.wait()
        except KeyboardInterrupt:
            _shutdown(signal.SIGINT, None)

        return server.process.returncode if server.process else 0


COMMANDS = Serve
