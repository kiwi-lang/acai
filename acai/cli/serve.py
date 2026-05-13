"""Launch the LLM server (vLLM / llama.cpp) as a standalone process.

Usage::

    acai serve                            # use defaults from config
    acai serve --model Qwen/Qwen3-Coder-Next-FP8 --port 8000
    acai serve --host 127.0.0.1           # local only (default bind is 0.0.0.0)
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from acai.cli import CommonArguments, setup


@dataclass
class ServeArguments(CommonArguments):
    model: str = argument(default=None, help="override model name/path")
    backend: str = argument(default=None, help="override backend (vllm, llamacpp)")
    port: int = argument(default=None, help="override server port")
    host: str = argument(default=None, help="bind address for local server (default 0.0.0.0 = all interfaces)")
    launch_template: str = argument(
        default=None,
        help="provide an explicit launch command template instead of auto-generating one",
    )


def _resolve_model_source(model_name: str) -> str:
    """Figure out where the model weights live on disk (or will be downloaded)."""
    if os.path.isdir(model_name) or os.path.isfile(model_name):
        return os.path.abspath(model_name)

    try:
        from huggingface_hub import scan_cache_dir
        cache_info = scan_cache_dir()
        for repo in cache_info.repos:
            if repo.repo_id == model_name:
                revisions = sorted(repo.revisions, key=lambda r: r.last_modified, reverse=True)
                if revisions:
                    return str(revisions[0].snapshot_path)
    except Exception:
        pass

    hf_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    hub_dir = os.path.join(hf_home, "hub")
    slug = "models--" + model_name.replace("/", "--")
    candidate = os.path.join(hub_dir, slug)
    if os.path.isdir(candidate):
        snapshots = os.path.join(candidate, "snapshots")
        if os.path.isdir(snapshots):
            revs = sorted(os.listdir(snapshots))
            if revs:
                return os.path.join(snapshots, revs[-1])
        return candidate

    return f"{hub_dir}/{slug}  (will download)"


def _tail_log(path: str, file_pos: int = 0) -> int:
    """Print new lines from *path* since *file_pos*, return new position."""
    try:
        with open(path, errors="replace") as f:
            f.seek(file_pos)
            new = f.read()
            pos = f.tell()
    except OSError:
        return file_pos
    if new:
        for line in new.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Remove the vLLM "(APIServer pid=N) " prefix for readability
            if stripped.startswith("("):
                paren_end = stripped.find(") ")
                if paren_end != -1:
                    stripped = stripped[paren_end + 2:]
            print(f"  │ {stripped}", flush=True)
    return pos


class Serve(Command):
    """Launch the LLM server as a standalone process.

    The process runs in the foreground so that you can inspect logs
    directly.  Use ``acai uber --extern-llm`` in another terminal to
    run the agent stack against this instance.
    """

    name = "serve"

    Arguments = ServeArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        provider = config.local_provider() or config.active_provider()

        if args.model:
            from acai.provider import _model_to_slug, ModelConfig
            provider.models = [ModelConfig(name=args.model, slug=_model_to_slug(args.model))]
        if args.backend:
            provider.backend = args.backend
        if args.port:
            provider.server_port = args.port
            provider.endpoint = f"http://127.0.0.1:{args.port}"
        if args.host:
            provider.server_host = args.host
        if args.launch_template:
            provider.launch_template = args.launch_template

        from acai.provider import LLMServer, LLMServerError

        server = LLMServer(provider, workspace=config.workspace)

        def _shutdown(sig, frame):
            print(f"\n  Received signal {sig}, shutting down LLM server...")
            server.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        model_source = _resolve_model_source(provider.model)

        print(f"\n  ╭─ LLM Server ─────────────────────────────────")
        print(f"  │ Model:    {provider.model}")
        print(f"  │ Source:   {model_source}")
        print(f"  │ Backend:  {provider.backend}")
        print(f"  │ Listen:   {provider.server_host}:{provider.server_port}  (use this host's IP from other machines)")
        print(f"  │ Local:    http://127.0.0.1:{provider.server_port}")
        print(f"  ├─ Starting ...")

        try:
            server.start_process()
        except LLMServerError as exc:
            print(f"  │ FAILED: {exc}", file=sys.stderr)
            print(f"  ╰─────────────────────────────────────────────\n")
            return 1

        log_path = server.latest_log_path()
        print(f"  │ PID:      {server.pid}")
        print(f"  │ Log:      {log_path}")
        print(f"  ├─ Loading model (tailing log) ...", flush=True)

        file_pos = 0
        try:
            while not server.is_healthy():
                if server.process and server.process.poll() is not None:
                    if log_path:
                        file_pos = _tail_log(log_path, file_pos)
                    rc = server.process.returncode
                    print(f"  │ Process exited with code {rc}")
                    print(f"  ╰─────────────────────────────────────────────\n")
                    return rc or 1
                if log_path:
                    file_pos = _tail_log(log_path, file_pos)
                time.sleep(0.5)

            if log_path:
                _tail_log(log_path, file_pos)

        except LLMServerError as exc:
            if log_path:
                _tail_log(log_path, file_pos)
            print(f"  │ FAILED: {exc}", file=sys.stderr)
            print(f"  ╰─────────────────────────────────────────────\n")
            return 1

        print(f"  ├─ Server healthy ✓")
        print(f"  │ Press Ctrl+C to stop.")
        print(f"  ╰─────────────────────────────────────────────\n", flush=True)

        try:
            if server.process is not None:
                while server.process.poll() is None:
                    if log_path:
                        file_pos = _tail_log(log_path, file_pos)
                    time.sleep(1)
                if log_path:
                    _tail_log(log_path, file_pos)
            else:
                signal.pause()
        except KeyboardInterrupt:
            _shutdown(signal.SIGINT, None)

        return server.process.returncode if server.process else 0


COMMANDS = Serve
