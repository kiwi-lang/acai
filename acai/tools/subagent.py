"""Sub-agent tools — spawn child agents and background tasks.

These tools allow an agent to delegate work to other agents or run
background operations, then collect their results.

Like interaction tools, the ``spawn_agent`` (blocking mode) tool is
intercepted by the orchestrator's tool loop and runs a nested graph
inline.  The async variants and ``run_task`` are dispatched normally
but interact with the ``TaskRunner`` instance.
"""

from __future__ import annotations

import json
import logging

from acai.orchestrator.tools import tool

log = logging.getLogger(__name__)

# Tools intercepted orchestrator-side for inline nested execution
SUBAGENT_TOOLS = frozenset({
    "subagent_spawn_agent",
})


@tool(permissions=("execute",), resources=("agents:run",))
def spawn_agent(
    agent: str,
    message: str,
    context: str = "",
    max_iterations: int = 10,
) -> str:
    """Spawn a sub-agent and wait for its response (blocking).

    Runs another agent with the given message as a separate conversation.
    The current agent pauses until the sub-agent finishes.
    Use this for delegation — e.g. asking a specialized agent to
    perform a focused task and return the result.

    Args:
        agent: Name of the agent to spawn (must exist in agent store).
        message: The user message to send to the sub-agent.
        context: Optional context to prepend to the message.
        max_iterations: Maximum tool-call rounds for the sub-agent.

    Returns:
        The sub-agent's final text response.
    """
    # Intercepted by graph — body never runs
    return json.dumps({"error": "subagent tools must be handled by the orchestrator"})


@tool(permissions=("execute",), resources=("agents:run",))
def spawn_agent_async(
    agent: str,
    message: str,
    context: str = "",
    max_iterations: int = 10,
) -> str:
    """Start a sub-agent in the background (non-blocking).

    Immediately returns a task_id that can be used with
    ``check_task`` or ``await_task`` to get the result later.

    Args:
        agent: Name of the agent to spawn.
        message: The user message to send to the sub-agent.
        context: Optional context to prepend.
        max_iterations: Maximum tool-call rounds.

    Returns:
        JSON with {"task_id": "...", "status": "running"}.
    """
    return json.dumps({"error": "subagent tools must be handled by the orchestrator"})


@tool(permissions=("read",), resources=("tasks:read",))
def await_task(
    task_id: str,
    timeout: float = 300.0,
) -> str:
    """Wait for a background task or async sub-agent to complete.

    Blocks until the task finishes or timeout expires.

    Args:
        task_id: The task ID returned by spawn_agent_async or run_task.
        timeout: Maximum seconds to wait (default 5 minutes).

    Returns:
        JSON with {"status": "completed", "result": "..."} or
        {"status": "failed", "error": "..."}.
    """
    return json.dumps({"error": "subagent tools must be handled by the orchestrator"})


@tool(permissions=("read",), resources=("tasks:read",))
def check_task(task_id: str) -> str:
    """Check the status of a background task (non-blocking).

    Does not wait — returns immediately with the current status.

    Args:
        task_id: The task ID to check.

    Returns:
        JSON with {"status": "running|completed|failed", "result": "...", "error": "..."}.
    """
    return json.dumps({"error": "subagent tools must be handled by the orchestrator"})


@tool(permissions=("execute",), resources=("tasks:run",))
def run_task(
    name: str,
    params: str = "{}",
) -> str:
    """Run a registered background task (non-blocking).

    Background tasks are lightweight Python coroutines registered
    with the orchestrator (e.g. "index_knowledge", "sync_vectors",
    "run_tests").

    Args:
        name: Registered task name.
        params: JSON object of parameters to pass to the task.

    Returns:
        JSON with {"task_id": "...", "status": "running"}.
    """
    return json.dumps({"error": "subagent tools must be handled by the orchestrator"})
