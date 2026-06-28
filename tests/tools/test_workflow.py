"""Unit tests for acai/tools/workflow.py."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from acai.tools.workflow import (
    validate,
    get_diagnostics,
    update,
    read_test_conversation,
    _workflow_dir,
    create_agent,
    update_agent,
    read_agent,
    create_skill,
    update_skill,
    read_skill,
)


# ── validate ──────────────────────────────────────────────────────


class TestValidate:
    def test_no_client(self):
        with patch("acai.tools.workflow.current_client", return_value=None):
            result = json.loads(validate('{"nodes": []}'))
        assert result == {"error": "orchestrator client not available"}

    def test_invalid_json(self):
        mock_client = MagicMock()
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(validate("not json at all"))
        assert result == {"error": "invalid JSON in workflow_spec"}

    def test_invalid_json_type_error(self):
        mock_client = MagicMock()
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(validate(None))
        assert result == {"error": "invalid JSON in workflow_spec"}

    def test_success(self):
        mock_client = MagicMock()
        mock_client.post.return_value = {"valid": True, "diagnostics": []}
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(validate('{"nodes": [], "edges": []}'))
        assert result == {"valid": True, "diagnostics": []}
        mock_client.post.assert_called_once_with(
            "/workflows/validate", {"nodes": [], "edges": []}, timeout=15
        )


# ── get_diagnostics ───────────────────────────────────────────────


class TestGetDiagnostics:
    def test_no_client(self):
        with patch("acai.tools.workflow.current_client", return_value=None):
            result = json.loads(get_diagnostics("wf-123"))
        assert result == {"error": "orchestrator client not available"}

    def test_success(self):
        mock_client = MagicMock()
        mock_client.post.return_value = {"diagnostics": ["warning: unused node"]}
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(get_diagnostics("wf-123"))
        assert result == {"diagnostics": ["warning: unused node"]}
        mock_client.post.assert_called_once_with(
            "/workflows/wf-123/validate", {}, timeout=15
        )


# ── update ────────────────────────────────────────────────────────


class TestUpdate:
    def test_no_client(self):
        with patch("acai.tools.workflow.current_client", return_value=None):
            result = json.loads(update("wf-1", '{"nodes": []}'))
        assert result == {"error": "orchestrator client not available"}

    def test_invalid_json(self):
        mock_client = MagicMock()
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(update("wf-1", "{bad json"))
        assert result == {"error": "invalid JSON in workflow_spec"}

    def test_success(self):
        mock_client = MagicMock()
        mock_client.put.return_value = {"saved": True}
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(update("wf-1", '{"nodes": [1]}'))
        assert result == {"saved": True}
        mock_client.put.assert_called_once_with(
            "/workflows/wf-1", {"nodes": [1]}, timeout=15
        )


# ── read_test_conversation ────────────────────────────────────────


class TestReadTestConversation:
    def test_no_client(self):
        with patch("acai.tools.workflow.current_client", return_value=None):
            result = json.loads(read_test_conversation("conv-1"))
        assert result == {"error": "orchestrator client not available"}

    def test_success_with_messages(self):
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "messages": [{"role": "user", "content": "hi"}]
        }
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(read_test_conversation("conv-1"))
        assert result == [{"role": "user", "content": "hi"}]
        mock_client.get.assert_called_once_with(
            "/conversations/conv-1/history", timeout=15
        )

    def test_success_no_messages_key(self):
        mock_client = MagicMock()
        mock_client.get.return_value = {"other": "data"}
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(read_test_conversation("conv-1"))
        assert result == []

    def test_non_dict_response(self):
        mock_client = MagicMock()
        mock_client.get.return_value = "unexpected"
        with patch("acai.tools.workflow.current_client", return_value=mock_client):
            result = json.loads(read_test_conversation("conv-1"))
        assert result == []


# ── _workflow_dir ─────────────────────────────────────────────────


class TestWorkflowDir:
    def test_uses_context_workspace(self):
        mock_ctx = MagicMock()
        mock_ctx.extra = {"workspace": "/my/workspace"}
        with patch("acai.tools.workflow.current_context", return_value=mock_ctx):
            result = _workflow_dir("wf-abc")
        assert result == os.path.join("/my/workspace", "workflows", "wf-abc")

    def test_context_no_workspace_falls_back_to_env(self):
        mock_ctx = MagicMock()
        mock_ctx.extra = {}
        with (
            patch("acai.tools.workflow.current_context", return_value=mock_ctx),
            patch.dict(os.environ, {"ACAI_WORKSPACE": "/env/ws"}),
        ):
            result = _workflow_dir("wf-x")
        assert result == os.path.join("/env/ws", "workflows", "wf-x")

    def test_context_none_falls_back_to_env(self):
        with (
            patch("acai.tools.workflow.current_context", return_value=None),
            patch.dict(os.environ, {"ACAI_WORKSPACE": "/from/env"}),
        ):
            result = _workflow_dir("wf-y")
        assert result == os.path.join("/from/env", "workflows", "wf-y")

    def test_no_context_no_env_defaults_to_workspace(self):
        with (
            patch("acai.tools.workflow.current_context", return_value=None),
            patch.dict(os.environ, {}, clear=True),
        ):
            # Remove ACAI_WORKSPACE if present
            os.environ.pop("ACAI_WORKSPACE", None)
            result = _workflow_dir("wf-z")
        assert result == os.path.join("workspace", "workflows", "wf-z")

    def test_context_extra_none(self):
        mock_ctx = MagicMock()
        mock_ctx.extra = None
        with (
            patch("acai.tools.workflow.current_context", return_value=mock_ctx),
            patch.dict(os.environ, {"ACAI_WORKSPACE": "/fallback"}),
        ):
            result = _workflow_dir("wf-q")
        assert result == os.path.join("/fallback", "workflows", "wf-q")


# ── create_agent ──────────────────────────────────────────────────


class TestCreateAgent:
    def test_creates_definition_file(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(
                create_agent(
                    workflow_id="wf-1",
                    agent_name="helper",
                    description="A helper agent",
                    system_prompt="You are helpful.",
                    provider="openai",
                    output_format="text",
                    tools='["workflow", "shell"]',
                )
            )

        assert result["created"] is True
        assert result["agent"] == "helper"

        agent_dir = tmp_path / "agents" / "helper"
        def_path = agent_dir / "definition.json"
        assert def_path.exists()
        definition = json.loads(def_path.read_text())
        assert definition["name"] == "helper"
        assert definition["description"] == "A helper agent"
        assert definition["provider"] == "openai"
        assert definition["output_format"] == "text"
        assert definition["tools"] == ["workflow", "shell"]
        assert definition["tool_permissions"] == ["read", "write"]

        tpl_path = agent_dir / "system.j2"
        assert tpl_path.exists()
        assert tpl_path.read_text() == "You are helpful."

    def test_no_system_prompt(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_agent(
                workflow_id="wf-1",
                agent_name="basic",
                description="Basic",
            )

        agent_dir = tmp_path / "agents" / "basic"
        assert not (agent_dir / "system.j2").exists()

    def test_no_tools(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_agent(
                workflow_id="wf-1",
                agent_name="notool",
                tools="[]",
            )

        definition = json.loads(
            (tmp_path / "agents" / "notool" / "definition.json").read_text()
        )
        assert "tools" not in definition
        assert "tool_permissions" not in definition

    def test_invalid_tools_json(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(
                create_agent(
                    workflow_id="wf-1",
                    agent_name="badtools",
                    tools="not json",
                )
            )

        assert result["created"] is True
        definition = json.loads(
            (tmp_path / "agents" / "badtools" / "definition.json").read_text()
        )
        assert "tools" not in definition

    def test_empty_tools_string(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_agent(
                workflow_id="wf-1",
                agent_name="empty",
                tools="",
            )

        definition = json.loads(
            (tmp_path / "agents" / "empty" / "definition.json").read_text()
        )
        assert "tools" not in definition


# ── update_agent ──────────────────────────────────────────────────


class TestUpdateAgent:
    def test_agent_not_found(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(update_agent("wf-1", "nonexistent"))
        assert result == {"error": "agent 'nonexistent' not found in workflow"}

    def test_updates_description(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text(
            json.dumps({"name": "helper", "description": "old", "provider": "auto"})
        )

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(
                update_agent("wf-1", "helper", description="new description")
            )

        assert result == {"updated": True, "agent": "helper"}
        definition = json.loads((agent_dir / "definition.json").read_text())
        assert definition["description"] == "new description"
        assert definition["provider"] == "auto"  # unchanged

    def test_updates_provider_and_output_format(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text(
            json.dumps({"name": "helper", "provider": "auto", "output_format": "text"})
        )

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_agent("wf-1", "helper", provider="anthropic", output_format="messages")

        definition = json.loads((agent_dir / "definition.json").read_text())
        assert definition["provider"] == "anthropic"
        assert definition["output_format"] == "messages"

    def test_updates_tools(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text(
            json.dumps({"name": "helper", "provider": "auto"})
        )

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_agent("wf-1", "helper", tools='["git"]')

        definition = json.loads((agent_dir / "definition.json").read_text())
        assert definition["tools"] == ["git"]
        assert definition["tool_permissions"] == ["read", "write"]

    def test_invalid_tools_json_ignored(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text(
            json.dumps({"name": "helper", "provider": "auto"})
        )

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_agent("wf-1", "helper", tools="not valid json")

        definition = json.loads((agent_dir / "definition.json").read_text())
        assert "tools" not in definition

    def test_updates_system_prompt(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        (agent_dir / "definition.json").write_text(
            json.dumps({"name": "helper", "provider": "auto"})
        )

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_agent("wf-1", "helper", system_prompt="New prompt!")

        assert (agent_dir / "system.j2").read_text() == "New prompt!"

    def test_empty_fields_not_updated(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        original = {"name": "helper", "description": "keep", "provider": "keep"}
        (agent_dir / "definition.json").write_text(json.dumps(original))

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_agent("wf-1", "helper")

        definition = json.loads((agent_dir / "definition.json").read_text())
        assert definition == original


# ── read_agent ────────────────────────────────────────────────────


class TestReadAgent:
    def test_agent_not_found(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(read_agent("wf-1", "missing"))
        assert result["agent"] == "missing"
        assert result["error"] == "agent not found in workflow"

    def test_reads_definition(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        definition = {"name": "helper", "description": "test", "provider": "auto"}
        (agent_dir / "definition.json").write_text(json.dumps(definition))

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(read_agent("wf-1", "helper"))

        assert result["agent"] == "helper"
        assert result["definition"] == definition
        assert "system_prompt" not in result

    def test_reads_definition_and_prompt(self, tmp_path):
        agent_dir = tmp_path / "agents" / "helper"
        agent_dir.mkdir(parents=True)
        definition = {"name": "helper", "provider": "auto"}
        (agent_dir / "definition.json").write_text(json.dumps(definition))
        (agent_dir / "system.j2").write_text("Hello {{ name }}")

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(read_agent("wf-1", "helper"))

        assert result["definition"] == definition
        assert result["system_prompt"] == "Hello {{ name }}"


# ── create_skill ──────────────────────────────────────────────────


class TestCreateSkill:
    def test_creates_full_skill(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(
                create_skill(
                    workflow_id="wf-1",
                    namespace="data",
                    name="summarize",
                    description="Summarize data",
                    parameters='{"type": "object", "properties": {"text": {"type": "string"}}}',
                    code="print('hello')",
                )
            )

        assert result["created"] is True
        assert result["skill"] == "data.summarize"

        skill_dir = tmp_path / "skills" / "data" / "summarize"
        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["name"] == "summarize"
        assert tool_def["description"] == "Summarize data"
        assert tool_def["parameters"]["properties"]["text"]["type"] == "string"

        assert (skill_dir / "run.py").read_text() == "print('hello')"
        assert "data.summarize" in (skill_dir / "README.md").read_text()

    def test_default_code_generated(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_skill(
                workflow_id="wf-1",
                namespace="ns",
                name="tool1",
            )

        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        code = (skill_dir / "run.py").read_text()
        assert "def main():" in code
        assert "json.dumps(result)" in code

    def test_empty_parameters_gets_default_schema(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_skill(
                workflow_id="wf-1",
                namespace="ns",
                name="tool2",
                parameters="{}",
            )

        skill_dir = tmp_path / "skills" / "ns" / "tool2"
        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def test_invalid_parameters_json(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_skill(
                workflow_id="wf-1",
                namespace="ns",
                name="tool3",
                parameters="not json",
            )

        skill_dir = tmp_path / "skills" / "ns" / "tool3"
        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def test_no_parameters_string(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            create_skill(
                workflow_id="wf-1",
                namespace="ns",
                name="tool4",
                parameters="",
            )

        skill_dir = tmp_path / "skills" / "ns" / "tool4"
        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }


# ── update_skill ──────────────────────────────────────────────────


class TestUpdateSkill:
    def test_skill_not_found(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(update_skill("wf-1", "ns", "missing"))
        assert result == {"error": "skill 'ns.missing' not found in workflow"}

    def test_updates_description(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "tool.json").write_text(
            json.dumps({"name": "tool1", "description": "old", "parameters": {}})
        )

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(
                update_skill("wf-1", "ns", "tool1", description="new desc")
            )

        assert result == {"updated": True, "skill": "ns.tool1"}
        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["description"] == "new desc"

    def test_updates_parameters(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "tool.json").write_text(
            json.dumps({"name": "tool1", "description": "d", "parameters": {}})
        )

        new_params = '{"type": "object", "properties": {"x": {"type": "number"}}}'
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_skill("wf-1", "ns", "tool1", parameters=new_params)

        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["parameters"]["properties"]["x"]["type"] == "number"

    def test_invalid_parameters_json_ignored(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        original = {"name": "tool1", "description": "d", "parameters": {"old": True}}
        (skill_dir / "tool.json").write_text(json.dumps(original))

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_skill("wf-1", "ns", "tool1", parameters="bad json!")

        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def["parameters"] == {"old": True}

    def test_updates_code(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        (skill_dir / "tool.json").write_text(
            json.dumps({"name": "tool1", "description": "d", "parameters": {}})
        )
        (skill_dir / "run.py").write_text("old code")

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_skill("wf-1", "ns", "tool1", code="new code")

        assert (skill_dir / "run.py").read_text() == "new code"

    def test_empty_fields_not_updated(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        original = {"name": "tool1", "description": "keep", "parameters": {"keep": 1}}
        (skill_dir / "tool.json").write_text(json.dumps(original))
        (skill_dir / "run.py").write_text("keep this")

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            update_skill("wf-1", "ns", "tool1")

        tool_def = json.loads((skill_dir / "tool.json").read_text())
        assert tool_def == original
        assert (skill_dir / "run.py").read_text() == "keep this"


# ── read_skill ────────────────────────────────────────────────────


class TestReadSkill:
    def test_skill_not_found(self, tmp_path):
        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(read_skill("wf-1", "ns", "missing"))
        assert result["skill"] == "ns.missing"
        assert result["error"] == "skill not found in workflow"

    def test_reads_definition_and_code(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        tool_def = {"name": "tool1", "description": "test", "parameters": {}}
        (skill_dir / "tool.json").write_text(json.dumps(tool_def))
        (skill_dir / "run.py").write_text("print('hi')")

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(read_skill("wf-1", "ns", "tool1"))

        assert result["skill"] == "ns.tool1"
        assert result["definition"] == tool_def
        assert result["code"] == "print('hi')"

    def test_reads_definition_without_code(self, tmp_path):
        skill_dir = tmp_path / "skills" / "ns" / "tool1"
        skill_dir.mkdir(parents=True)
        tool_def = {"name": "tool1", "description": "test", "parameters": {}}
        (skill_dir / "tool.json").write_text(json.dumps(tool_def))

        with patch("acai.tools.workflow._workflow_dir", return_value=str(tmp_path)):
            result = json.loads(read_skill("wf-1", "ns", "tool1"))

        assert result["skill"] == "ns.tool1"
        assert result["definition"] == tool_def
        assert "code" not in result
