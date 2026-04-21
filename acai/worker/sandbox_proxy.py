"""Worker-side sandbox proxy.

The :class:`SandboxProxy` owns the sandbox lifecycle and transparently
proxies tool calls marked with ``sandbox=True`` to the sandbox's
``acai mcp`` endpoint.

The sandbox is started **lazily** — the first qualifying tool call
(where both the tool requires sandboxing *and* the agent has
``uses_sandbox=True``) triggers sandbox startup.  The orchestrator
only passes ``context["uses_sandbox"] = True``; the actual
:class:`SandboxConfig` is a system-wide setting held by the worker.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Callable

import aiohttp
from starlette.responses import StreamingResponse

if TYPE_CHECKING:
    from acai.orchestrator.config import SandboxConfig
    from acai.worker.sandbox.base import Sandbox

log = logging.getLogger(__name__)


class SandboxProxy:
    """Manages a sandbox and proxies qualifying tool calls to it.

    Parameters
    ----------
    default_config:
        System-wide :class:`SandboxConfig` (from ``AcaiConfig.sandbox``).
    sandbox_predicate:
        Callable that returns ``True`` when a tool name requires
        sandbox execution.  Typically ``ToolRegistry.is_sandboxed``.
    """

    def __init__(
        self,
        default_config: SandboxConfig,
        sandbox_predicate: Callable[[str], bool] | None = None,
    ):
        self._default_config = default_config
        self._sandbox_predicate = sandbox_predicate
        self._sandbox: Sandbox | None = None
        self._effective_config: SandboxConfig | None = None

    @property
    def running(self) -> bool:
        return self._sandbox is not None and self._sandbox.running

    @property
    def endpoint(self) -> str | None:
        if self._sandbox is not None and self._sandbox.running:
            return self._sandbox.endpoint
        return None

    def _ensure_started(self, ctx: dict) -> None:
        """Start the sandbox if not already running, using context from the tool call."""
        if self.running:
            return

        from acai.worker.sandbox import create_sandbox

        if self._default_config.type == "none":
            return

        self._effective_config = self._default_config
        self._sandbox = create_sandbox(self._default_config)

        workspace = os.environ.get("ACAI_WORKSPACE", os.getcwd())
        session_id = ctx.get("conversation", "default")
        agent_name = ctx.get("agent_name", "")

        log.info(
            "lazy-starting sandbox  backend=%s  agent=%s  session=%s",
            self._default_config.type, agent_name, session_id,
        )
        self._sandbox.start(
            workspace,
            sandbox_config=self._default_config,
            session_id=session_id,
            agent_name=agent_name,
        )

    def stop(self) -> None:
        if self._sandbox is not None:
            self._sandbox.stop()
            self._sandbox = None
            self._effective_config = None

    def should_proxy(self, tool_name: str, ctx: dict | None = None) -> bool:
        """True when the tool should be forwarded to a sandbox.

        A tool is proxied when **all** of:
        1. A sandbox backend is configured (type != "none").
        2. The tool is annotated ``sandbox=True`` (checked via the predicate).
        3. The agent has ``uses_sandbox=True`` (signalled in ctx), **or**
           the sandbox is already running for this session.
        """
        if self._default_config.type == "none":
            return False

        if self._sandbox_predicate is not None:
            if not self._sandbox_predicate(tool_name):
                return False
        else:
            return False

        if self.running:
            return True
        if ctx and ctx.get("uses_sandbox"):
            return True
        return False

    async def proxy_call(
        self,
        tool_name: str,
        args: dict,
        context: dict | None = None,
    ) -> StreamingResponse:
        """Forward a tool call to the sandbox, starting it lazily if needed."""
        try:
            self._ensure_started(context or {})
        except Exception as exc:
            log.error("sandbox startup failed: %s", exc)
            async def _startup_error():
                yield f"event: error\ndata: {json.dumps({'tool': tool_name, 'error': f'Sandbox startup failed: {exc}'})}\n\n"
                yield "event: done\ndata: {}\n\n"
            return StreamingResponse(_startup_error(), media_type="text/event-stream")

        ep = self.endpoint
        if ep is None:
            async def _not_running():
                yield f"event: error\ndata: {json.dumps({'tool': tool_name, 'error': 'Sandbox is not running after startup attempt'})}\n\n"
                yield "event: done\ndata: {}\n\n"
            return StreamingResponse(_not_running(), media_type="text/event-stream")

        url = f"{ep}/tools/call"
        payload: dict = {"tool": tool_name, "args": args}
        if context:
            fwd_ctx = {k: v for k, v in context.items() if k != "uses_sandbox"}
            if fwd_ctx:
                payload["context"] = fwd_ctx

        log.info("proxying %s → sandbox at %s", tool_name, ep)

        async def _relay():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            log.error("sandbox returned %s for %s: %s", resp.status, tool_name, body[:500])
                            yield f"event: error\ndata: {json.dumps({'tool': tool_name, 'error': f'Sandbox HTTP {resp.status}: {body[:300]}'})}\n\n"
                            yield "event: done\ndata: {}\n\n"
                            return
                        async for chunk in resp.content.iter_any():
                            yield chunk.decode("utf-8", errors="replace")
            except Exception as exc:
                log.error("sandbox proxy error for %s: %s", tool_name, exc)
                yield f"event: error\ndata: {json.dumps({'tool': tool_name, 'error': str(exc)})}\n\n"
                yield "event: done\ndata: {}\n\n"

        return StreamingResponse(_relay(), media_type="text/event-stream")
