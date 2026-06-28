"""Start the dev spawner — manages frontend, backend, and LLM services.

Usage::

    acai dev serve                # start all dev services (reads acai.yaml or defaults)
    acai dev serve --port 5055    # override spawner port
"""

from __future__ import annotations

import os
import signal
import socket
import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from acai.cli import CommonArguments, setup


def _get_local_ip() -> str:
    """Get the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


BASE_PORT = 5100

def _package_dir() -> str:
    """Return the acai package source directory (where acai/ lives)."""
    import acai
    return os.path.dirname(os.path.dirname(os.path.abspath(acai.__file__)))


def _default_services() -> list[dict]:
    root = _package_dir()
    return [
        {"name": "frontend", "command": "npm install && npm run dev -- --port {port}", "cwd": os.path.join(root, "acai", "ui"), "auto_start": True},
        {"name": "backend", "command": "acai uber --extern-llm 1 --debug 1 --port {port}", "cwd": root, "auto_start": True},
        {"name": "vllm", "command": "acai serve --port {port}", "cwd": root, "auto_start": False},
    ]


@dataclass
class ServeArguments(CommonArguments):
    host: str = argument(default="0.0.0.0", help="bind address for spawner API")
    port: int = argument(default=0, help="base port (0 = from config or 5060); spawner=base, services=base+1,+2,...")


class Serve(Command):
    """Start the dev spawner — manage frontend, backend, and LLM services."""

    name = "serve"

    Arguments = ServeArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)

        from acai.devserver.manager import ProcessManager, ServiceSpec
        from acai.devserver.app import create_dev_app

        dev_cfg = config.dev
        base_port = args.port if args.port else dev_cfg.port or BASE_PORT

        if dev_cfg.services:
            services_raw = dev_cfg.services
        else:
            from acai.orchestrator.config import DevServiceConfig
            services_raw = [DevServiceConfig(**s) for s in _default_services()]

        # Port assignment: spawner = base_port, services = base_port+1, +2, +3...
        base = os.getcwd()
        specs = []
        service_ports: dict[str, int] = {}
        for i, s in enumerate(services_raw, start=1):
            svc_port = base_port + i
            service_ports[s.name] = svc_port
            command = s.command.format(port=svc_port)
            cwd = s.cwd if os.path.isabs(s.cwd) else os.path.abspath(os.path.join(base, s.cwd))
            specs.append(ServiceSpec(
                name=s.name,
                command=command,
                cwd=cwd,
                env=s.env,
                auto_start=s.auto_start,
            ))

        log_dir = os.path.join(config.workspace, "dev")
        manager = ProcessManager(specs, log_dir=log_dir)

        def _shutdown(sig, frame):
            print(f"\n[dev] shutting down all services...")
            manager.stop_all()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        manager.start_all()

        import time
        time.sleep(0.5)

        app = create_dev_app(manager)

        ip = _get_local_ip()
        print(f"\n  Dev Spawner on http://{ip}:{base_port}")
        print(f"  Services:")
        for s in specs:
            info = manager.status(s.name)
            status = info["status"] if info else "unknown"
            pid_str = f" pid={info['pid']}" if info and info.get("pid") else ""
            if status == "crashed":
                print(f"    \033[31m✗ {s.name}\033[0m: {s.command}")
                logs = manager.logs(s.name, tail=3) or []
                for line in logs:
                    print(f"        {line}")
            elif status == "running":
                print(f"    \033[32m✓ {s.name}\033[0m [{status}{pid_str}]: {s.command}")
            else:
                print(f"    - {s.name} [{status}]: {s.command}")
        print()
        print(f"  Endpoints:")
        for s in specs:
            print(f"    {s.name:12s} http://{ip}:{service_ports[s.name]}")
        print(f"    {'spawner':12s} http://{ip}:{base_port}/dev/services")
        print(f"  Logs:        {log_dir}/")
        print()

        import uvicorn
        uvicorn.run(app, host=args.host, port=base_port, log_level="warning")

        manager.stop_all()
        return 0


COMMANDS = Serve
