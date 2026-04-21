"""Pluggable sandbox backends for isolated tool execution.

Each backend exposes an ``acai mcp`` tool server (HTTP) running
inside a sandboxed environment.  The orchestrator routes tool calls
to the sandbox endpoint instead of the worker's in-process tools.

Supported backends:

* **container** — Docker or Podman (auto-detected).
* **bubblewrap** — ``bwrap`` Linux user-namespace sandbox.
* **nsjail** — Google nsjail process isolation.
* **firecracker** — AWS Firecracker microVM isolation.
* **none** — No sandbox; tools run in the worker process.

Usage::

    from acai.worker.sandbox import SandboxConfig, create_sandbox

    cfg = SandboxConfig(type="docker", image="acai-sandbox", mcp_port=9200)
    sandbox = create_sandbox(cfg)
    sandbox.start("/path/to/worktree")
    print(sandbox.endpoint)  # http://127.0.0.1:<port>
    ...
    sandbox.stop()
"""

from acai.worker.sandbox.base import (
    Sandbox,
    create_sandbox,
)
from acai.orchestrator.config import SandboxConfig

__all__ = [
    "Sandbox",
    "SandboxConfig",
    "create_sandbox",
]
