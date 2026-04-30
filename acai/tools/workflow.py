"""Workflow builder tools — let the graph_builder agent interact with
the workflow editor, test conversation, and validation.

These tools are executed on the worker and call back to the
orchestrator API via :func:`current_client`.
"""

from __future__ import annotations

import json
import logging
import os

from acai.orchestrator.context import current_client, current_context
from acai.orchestrator.tools import tool

log = logging.getLogger(__name__)


# ── validation ────────────────────────────────────────────────────

@tool(permissions=("read",), resources=("workflows:validate",))
def validate(workflow_spec: str) -> str:
    """Run the type checker / validator on a workflow spec and return diagnostics.

    Args:
        workflow_spec: The full workflow JSON (nodes + edges) as a string.
    """
    client = current_client()
    if client is None:
        return json.dumps({"error": "orchestrator client not available"})

    try:
        spec = json.loads(workflow_spec)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "invalid JSON in workflow_spec"})

    result = client.post("/workflows/validate", spec, timeout=15)
    return json.dumps(result, indent=2)


@tool(permissions=("read",), resources=("workflows:validate",), scope="project:workflow_id")
def get_diagnostics(workflow_id: str) -> str:
    """Get validation diagnostics for a saved workflow.

    Args:
        workflow_id: The workflow identifier.
    """
    client = current_client()
    if client is None:
        return json.dumps({"error": "orchestrator client not available"})

    result = client.post(f"/workflows/{workflow_id}/validate", {}, timeout=15)
    return json.dumps(result, indent=2)


# ── workflow CRUD ─────────────────────────────────────────────────

@tool(permissions=("write",), resources=("workflows:update",), scope="project:workflow_id")
def update(workflow_id: str, workflow_spec: str) -> str:
    """Save an updated workflow spec.  The spec must be the full JSON.

    Args:
        workflow_id: The workflow identifier (e.g. "new-workflow").
        workflow_spec: The complete workflow JSON as a string.
    """
    client = current_client()
    if client is None:
        return json.dumps({"error": "orchestrator client not available"})

    try:
        spec = json.loads(workflow_spec)
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "invalid JSON in workflow_spec"})

    result = client.put(f"/workflows/{workflow_id}", spec, timeout=15)
    return json.dumps(result)


# ── test conversation ─────────────────────────────────────────────

@tool(permissions=("read",), resources=("conversations:read",))
def read_test_conversation(conversation_id: str) -> str:
    """Read the messages from the test conversation.

    Args:
        conversation_id: The test conversation ID.
    """
    client = current_client()
    if client is None:
        return json.dumps({"error": "orchestrator client not available"})

    result = client.get(f"/conversations/{conversation_id}/history", timeout=15)
    messages = result.get("messages", []) if isinstance(result, dict) else []
    return json.dumps(messages, indent=2)


# @tool(permissions=("write",))
# def send_test_message(workflow_id: str, message: str) -> str:
#     """Send a message through the workflow and return the assistant response.

#     This runs the workflow end-to-end and collects the streamed output.

#     Args:
#         workflow_id: The workflow identifier.
#         message: The user message to send.
#     """
#     client = current_client()
#     if client is None:
#         return json.dumps({"error": "orchestrator client not available"})

#     response = client.post_sse(
#         f"/workflows/{workflow_id}/run",
#         {"message": message, "test": True},
#         timeout=120,
#     )
#     return response or "(no response)"


# ── agent management (inside workflow) ────────────────────────────

def _workflow_dir(workflow_id: str) -> str:
    """Resolve the workflow directory path."""
    ctx = current_context()
    workspace = ""
    if ctx and ctx.extra:
        workspace = ctx.extra.get("workspace", "")
    if not workspace:
        workspace = os.environ.get("ACAI_WORKSPACE", "workspace")
    return os.path.join(workspace, "workflows", workflow_id)


@tool(permissions=("write",), resources=("agents:create",), scope="project:workflow_id")
def create_agent(
    workflow_id: str,
    agent_name: str,
    description: str = "",
    system_prompt: str = "",
    provider: str = "auto",
    output_format: str = "text",
    tools: str = "[]",
) -> str:
    """Create or update an agent bundled inside a workflow.

    The agent will be saved to ``<workflow>/agents/<agent_name>/``.
    When the workflow runs, agents in its directory take precedence
    over global agents.

    Args:
        workflow_id: The workflow identifier.
        agent_name: Agent name (must match the name used in agent_call nodes).
        description: Short description of what the agent does.
        system_prompt: The Jinja2 system prompt template content.
        provider: LLM provider to use (default "auto").
        output_format: Either "text" or "messages".
        tools: JSON array of tool namespace strings (e.g. '["workflow"]').
    """
    wf_dir = _workflow_dir(workflow_id)
    agent_dir = os.path.join(wf_dir, "agents", agent_name)
    os.makedirs(agent_dir, exist_ok=True)

    try:
        tools_list = json.loads(tools) if tools else []
    except (json.JSONDecodeError, TypeError):
        tools_list = []

    definition = {
        "name": agent_name,
        "description": description,
        "role": "system",
        "provider": provider,
        "output_format": output_format,
    }
    if tools_list:
        definition["tools"] = tools_list
        definition["tool_permissions"] = ["read", "write"]

    def_path = os.path.join(agent_dir, "definition.json")
    with open(def_path, "w") as f:
        json.dump(definition, f, indent=2)

    if system_prompt:
        tpl_path = os.path.join(agent_dir, "system.j2")
        with open(tpl_path, "w") as f:
            f.write(system_prompt)

    return json.dumps({
        "created": True,
        "agent": agent_name,
        "path": agent_dir,
    })


@tool(permissions=("write",), resources=("agents:update",), scope="project:workflow_id")
def update_agent(
    workflow_id: str,
    agent_name: str,
    description: str = "",
    system_prompt: str = "",
    provider: str = "",
    output_format: str = "",
    tools: str = "",
) -> str:
    """Update specific fields of an existing agent in a workflow.

    Only non-empty arguments are written — everything else is preserved.

    Args:
        workflow_id: The workflow identifier.
        agent_name: The agent name to update.
        description: New description (leave empty to keep current).
        system_prompt: New Jinja2 system prompt (leave empty to keep current).
        provider: New provider (leave empty to keep current).
        output_format: New output format (leave empty to keep current).
        tools: New tools JSON array (leave empty to keep current).
    """
    wf_dir = _workflow_dir(workflow_id)
    agent_dir = os.path.join(wf_dir, "agents", agent_name)
    def_path = os.path.join(agent_dir, "definition.json")

    if not os.path.isfile(def_path):
        return json.dumps({"error": f"agent '{agent_name}' not found in workflow"})

    with open(def_path) as f:
        definition = json.load(f)

    if description:
        definition["description"] = description
    if provider:
        definition["provider"] = provider
    if output_format:
        definition["output_format"] = output_format
    if tools:
        try:
            definition["tools"] = json.loads(tools)
            definition["tool_permissions"] = ["read", "write"]
        except (json.JSONDecodeError, TypeError):
            pass

    with open(def_path, "w") as f:
        json.dump(definition, f, indent=2)

    if system_prompt:
        tpl_path = os.path.join(agent_dir, "system.j2")
        with open(tpl_path, "w") as f:
            f.write(system_prompt)

    return json.dumps({"updated": True, "agent": agent_name})


@tool(permissions=("read",), resources=("agents:read",), scope="project:workflow_id")
def read_agent(workflow_id: str, agent_name: str) -> str:
    """Read an agent definition and system prompt from a workflow.

    Args:
        workflow_id: The workflow identifier.
        agent_name: The agent name.
    """
    wf_dir = _workflow_dir(workflow_id)
    agent_dir = os.path.join(wf_dir, "agents", agent_name)

    result: dict = {"agent": agent_name}

    def_path = os.path.join(agent_dir, "definition.json")
    if os.path.isfile(def_path):
        with open(def_path) as f:
            result["definition"] = json.load(f)
    else:
        result["error"] = "agent not found in workflow"
        return json.dumps(result)

    tpl_path = os.path.join(agent_dir, "system.j2")
    if os.path.isfile(tpl_path):
        with open(tpl_path) as f:
            result["system_prompt"] = f.read()

    return json.dumps(result, indent=2)


# ── skill management (inside workflow) ────────────────────────────

@tool(permissions=("write",), resources=("skills:create",), scope="project:workflow_id")
def create_skill(
    workflow_id: str,
    namespace: str,
    name: str,
    description: str = "",
    parameters: str = "{}",
    code: str = "",
) -> str:
    """Create or update a skill bundled inside a workflow.

    The skill will be saved to ``<workflow>/skills/<namespace>/<name>/``.

    Args:
        workflow_id: The workflow identifier.
        namespace: Skill namespace (e.g. "data", "analysis").
        name: Skill name (e.g. "summarize").
        description: What the skill does.
        parameters: JSON schema for parameters as a string.
        code: Python code for run.py.
    """
    wf_dir = _workflow_dir(workflow_id)
    skill_dir = os.path.join(wf_dir, "skills", namespace, name)
    os.makedirs(skill_dir, exist_ok=True)

    try:
        params = json.loads(parameters) if parameters else {}
    except (json.JSONDecodeError, TypeError):
        params = {}

    if not params:
        params = {"type": "object", "properties": {}, "required": []}

    tool_def = {
        "name": name,
        "description": description,
        "parameters": params,
    }
    with open(os.path.join(skill_dir, "tool.json"), "w") as f:
        json.dump(tool_def, f, indent=2)

    if not code:
        code = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n\n"
            "def main():\n"
            '    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}\n'
            '    result = {"status": "ok"}\n'
            "    print(json.dumps(result))\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        )

    with open(os.path.join(skill_dir, "run.py"), "w") as f:
        f.write(code)

    with open(os.path.join(skill_dir, "README.md"), "w") as f:
        f.write(f"# {namespace}.{name}\n\n{description}\n")

    return json.dumps({
        "created": True,
        "skill": f"{namespace}.{name}",
        "path": skill_dir,
    })


@tool(permissions=("write",), resources=("skills:update",), scope="project:workflow_id")
def update_skill(
    workflow_id: str,
    namespace: str,
    name: str,
    description: str = "",
    parameters: str = "",
    code: str = "",
) -> str:
    """Update specific fields of an existing skill in a workflow.

    Only non-empty arguments are written — everything else is preserved.

    Args:
        workflow_id: The workflow identifier.
        namespace: Skill namespace.
        name: Skill name.
        description: New description (leave empty to keep current).
        parameters: New JSON schema string (leave empty to keep current).
        code: New Python code for run.py (leave empty to keep current).
    """
    wf_dir = _workflow_dir(workflow_id)
    skill_dir = os.path.join(wf_dir, "skills", namespace, name)
    tool_path = os.path.join(skill_dir, "tool.json")

    if not os.path.isfile(tool_path):
        return json.dumps({"error": f"skill '{namespace}.{name}' not found in workflow"})

    with open(tool_path) as f:
        tool_def = json.load(f)

    if description:
        tool_def["description"] = description
    if parameters:
        try:
            tool_def["parameters"] = json.loads(parameters)
        except (json.JSONDecodeError, TypeError):
            pass

    with open(tool_path, "w") as f:
        json.dump(tool_def, f, indent=2)

    if code:
        with open(os.path.join(skill_dir, "run.py"), "w") as f:
            f.write(code)

    return json.dumps({"updated": True, "skill": f"{namespace}.{name}"})


@tool(permissions=("read",), resources=("skills:read",), scope="project:workflow_id")
def read_skill(workflow_id: str, namespace: str, name: str) -> str:
    """Read a skill's definition and code from a workflow.

    Args:
        workflow_id: The workflow identifier.
        namespace: Skill namespace.
        name: Skill name.
    """
    wf_dir = _workflow_dir(workflow_id)
    skill_dir = os.path.join(wf_dir, "skills", namespace, name)

    result: dict = {"skill": f"{namespace}.{name}"}

    tool_path = os.path.join(skill_dir, "tool.json")
    if os.path.isfile(tool_path):
        with open(tool_path) as f:
            result["definition"] = json.load(f)
    else:
        result["error"] = "skill not found in workflow"
        return json.dumps(result)

    code_path = os.path.join(skill_dir, "run.py")
    if os.path.isfile(code_path):
        with open(code_path) as f:
            result["code"] = f.read()

    return json.dumps(result, indent=2)
