"""Tests for acai.orchestrator.agent_store — AgentDef and AgentStore."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from acai.orchestrator.agent_store import (
    AgentDef,
    AgentStore,
    _content_len,
    compress_messages,
    hydrate_task,
    needs_compression,
    resolve_task,
)


@pytest.fixture
def workspace_agents(tmp_path):
    """Writable agents directory."""
    d = tmp_path / "agents"
    d.mkdir()
    return str(d)


@pytest.fixture
def builtin_agents(tmp_path):
    """Builtin agents directory with a 'default' agent."""
    d = tmp_path / "builtin_agents"
    default_dir = d / "default"
    default_dir.mkdir(parents=True)
    definition = {
        "name": "default",
        "description": "A helpful assistant",
        "tools": ["ui"],
        "tool_permissions": ["read", "write"],
    }
    (default_dir / "definition.json").write_text(json.dumps(definition))
    (default_dir / "system.j2").write_text(
        '[{"role": "system", "content": "You are {{ agent.name }}."}]'
    )
    return str(d)


@pytest.fixture
def store(workspace_agents, builtin_agents):
    return AgentStore(workspace_agents, builtin_dir=builtin_agents)


class TestAgentDef:

    def test_auto_id_generation(self):
        a = AgentDef(name="test")
        assert len(a.id) == 12

    def test_from_dict(self):
        d = {"name": "coder", "description": "writes code", "tools": ["shell"]}
        agent = AgentDef.from_dict(d)
        assert agent.name == "coder"
        assert agent.description == "writes code"
        assert agent.tools == ["shell"]

    def test_to_dict_roundtrip(self):
        agent = AgentDef(name="x", description="y", tools=["a", "b"])
        d = agent.to_dict()
        restored = AgentDef.from_dict(d)
        assert restored.name == agent.name
        assert restored.tools == agent.tools

    def test_is_provider_allowed_no_filters(self):
        a = AgentDef(name="test")
        assert a.is_provider_allowed("anything") is True

    def test_is_provider_allowed_forbid_list(self):
        a = AgentDef(name="test", provider_forbid=["bad_provider"])
        assert a.is_provider_allowed("bad_provider") is False
        assert a.is_provider_allowed("good_provider") is True

    def test_is_provider_allowed_allow_list(self):
        a = AgentDef(name="test", provider_allow=["vllm"])
        assert a.is_provider_allowed("vllm") is True
        assert a.is_provider_allowed("openai") is False

    def test_legacy_sandbox_migration(self):
        d = {"name": "old", "sandbox": {"type": "container"}}
        agent = AgentDef.from_dict(d)
        assert agent.uses_sandbox is True

    def test_legacy_sandbox_none(self):
        d = {"name": "old", "sandbox": {"type": "none"}}
        agent = AgentDef.from_dict(d)
        assert agent.uses_sandbox is False

    def test_complexity_default(self):
        a = AgentDef(name="test")
        assert a.complexity == "medium"


class TestAgentStoreGet:

    def test_get_builtin(self, store):
        agent = store.get("default")
        assert agent is not None
        assert agent.name == "default"
        assert agent.builtin is True

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent_agent_xyz") is None

    def test_workspace_shadows_builtin(self, store, workspace_agents):
        ws_dir = os.path.join(workspace_agents, "default")
        os.makedirs(ws_dir, exist_ok=True)
        definition = {
            "name": "default",
            "description": "customized default",
            "tools": ["shell"],
        }
        with open(os.path.join(ws_dir, "definition.json"), "w") as f:
            json.dump(definition, f)

        agent = store.get("default")
        assert agent is not None
        assert agent.description == "customized default"
        assert agent.builtin is False


class TestAgentStoreList:

    def test_list_includes_builtins(self, store):
        agents = store.list()
        names = [a.name for a in agents]
        assert "default" in names

    def test_list_includes_workspace_agents(self, store, workspace_agents):
        ws_dir = os.path.join(workspace_agents, "custom")
        os.makedirs(ws_dir)
        definition = {"name": "custom", "description": "custom agent"}
        with open(os.path.join(ws_dir, "definition.json"), "w") as f:
            json.dump(definition, f)

        agents = store.list()
        names = [a.name for a in agents]
        assert "custom" in names
        assert "default" in names


class TestAgentStoreCRUD:

    def test_create_agent(self, store, workspace_agents):
        agent = AgentDef(name="new-agent", description="brand new")
        store.save(agent)

        loaded = store.get("new-agent")
        assert loaded is not None
        assert loaded.name == "new-agent"
        assert loaded.description == "brand new"

    def test_delete_workspace_agent(self, store, workspace_agents):
        agent = AgentDef(name="temp", description="temporary")
        store.save(agent)
        assert store.get("temp") is not None

        store.delete("temp")
        assert store.get("temp") is None

    def test_delete_builtin_does_not_remove(self, store):
        # Deleting a builtin should not actually remove the builtin
        store.delete("default")
        assert store.get("default") is not None


class TestAgentStoreScoped:

    def test_scoped_adds_extra_dir(self, store, tmp_path):
        extra_dir = tmp_path / "extra_agents" / "special"
        extra_dir.mkdir(parents=True)
        definition = {"name": "special", "description": "from extra dir"}
        (extra_dir.parent / "special" / "definition.json").write_text(
            json.dumps(definition))
        # Need to point to the parent dir, not the agent dir
        (tmp_path / "extra_agents" / "special").mkdir(exist_ok=True)
        (tmp_path / "extra_agents" / "special" / "definition.json").write_text(
            json.dumps(definition))

        with store.scoped(str(tmp_path / "extra_agents")):
            agent = store.get("special")
            assert agent is not None
            assert agent.name == "special"

        # After scope exit, agent should not be findable
        assert store.get("special") is None


# ------------------------------------------------------------------
# AgentDef — additional edge cases
# ------------------------------------------------------------------


class TestAgentDefEdgeCases:

    def test_uses_sandbox_dict_migration_in_post_init(self):
        """Legacy dict ``uses_sandbox`` is coerced to bool in __post_init__."""
        a = AgentDef(name="x", uses_sandbox={"type": "container"})
        assert a.uses_sandbox is True

    def test_uses_sandbox_dict_none_type(self):
        a = AgentDef(name="x", uses_sandbox={"type": "none"})
        assert a.uses_sandbox is False

    def test_uses_sandbox_dict_missing_type_key(self):
        """Dict with no ``type`` key defaults to ``'none'`` → False."""
        a = AgentDef(name="x", uses_sandbox={})
        assert a.uses_sandbox is False

    def test_from_dict_sandbox_bool_migration(self):
        """``sandbox`` as a plain bool is migrated to ``uses_sandbox``."""
        d = {"name": "old", "sandbox": True}
        agent = AgentDef.from_dict(d)
        assert agent.uses_sandbox is True

    def test_from_dict_sandbox_false_bool(self):
        d = {"name": "old", "sandbox": False}
        agent = AgentDef.from_dict(d)
        assert agent.uses_sandbox is False

    def test_from_dict_drops_unknown_builtin_key(self):
        d = {"name": "x", "builtin": True}
        agent = AgentDef.from_dict(d)
        assert agent.builtin is False  # builtin is always popped

    def test_to_dict_preserves_builtin_flag(self):
        a = AgentDef(name="x")
        a.builtin = True
        d = a.to_dict()
        assert d["builtin"] is True

    def test_is_provider_allowed_both_lists(self):
        """Forbid takes priority over allow."""
        a = AgentDef(
            name="t",
            provider_allow=["openai", "bad"],
            provider_forbid=["bad"],
        )
        assert a.is_provider_allowed("bad") is False
        assert a.is_provider_allowed("openai") is True

    def test_explicit_id_not_overwritten(self):
        a = AgentDef(id="custom-id", name="test")
        assert a.id == "custom-id"

    def test_explicit_created_at_not_overwritten(self):
        a = AgentDef(name="test", created_at="2020-01-01T00:00:00+00:00")
        assert a.created_at == "2020-01-01T00:00:00+00:00"


# ------------------------------------------------------------------
# AgentStore — builtin_dir property & add_builtin_dir
# ------------------------------------------------------------------


class TestAgentStoreBuiltinManagement:

    def test_builtin_dir_property(self, store, builtin_agents):
        assert store.builtin_dir == builtin_agents

    def test_add_builtin_dir_valid(self, store, tmp_path):
        extra = tmp_path / "extra"
        extra.mkdir()
        store.add_builtin_dir(str(extra))
        assert str(extra) in store._builtin_dirs

    def test_add_builtin_dir_nonexistent_ignored(self, store):
        store.add_builtin_dir("/nonexistent/path")
        assert "/nonexistent/path" not in store._builtin_dirs

    def test_add_builtin_dir_empty_string_ignored(self, store):
        before = len(store._builtin_dirs)
        store.add_builtin_dir("")
        assert len(store._builtin_dirs) == before

    def test_add_builtin_dir_duplicate_ignored(self, store, builtin_agents):
        before = len(store._builtin_dirs)
        store.add_builtin_dir(builtin_agents)
        assert len(store._builtin_dirs) == before


# ------------------------------------------------------------------
# AgentStore — scoped context manager edge cases
# ------------------------------------------------------------------


class TestAgentStoreScopedEdgeCases:

    def test_scoped_with_nonexistent_dir(self, store):
        """Non-existent directories are silently skipped."""
        with store.scoped("/nonexistent"):
            pass  # should not raise

    def test_scoped_with_empty_string(self, store):
        before = list(store._builtin_dirs)
        with store.scoped(""):
            assert store._builtin_dirs == before
        assert store._builtin_dirs == before

    def test_scoped_cleanup_after_exception(self, store, tmp_path):
        extra = tmp_path / "extra_exc"
        extra.mkdir()
        (extra / "myagent").mkdir()
        (extra / "myagent" / "definition.json").write_text(
            json.dumps({"name": "myagent"})
        )

        with pytest.raises(RuntimeError):
            with store.scoped(str(extra)):
                assert store.get("myagent") is not None
                raise RuntimeError("boom")

        assert str(extra) not in store._builtin_dirs
        assert store.get("myagent") is None

    def test_scoped_cleanup_tolerates_manual_removal(self, store, tmp_path):
        """If someone else already removed the dir from the list, no error."""
        extra = tmp_path / "extra_manual"
        extra.mkdir()

        with store.scoped(str(extra)):
            store._builtin_dirs.remove(str(extra))
        # ValueError in finally is swallowed


# ------------------------------------------------------------------
# AgentStore — delete edge cases
# ------------------------------------------------------------------


class TestAgentStoreDeleteEdgeCases:

    def test_delete_nonexistent_returns_false(self, store):
        assert store.delete("does-not-exist") is False


# ------------------------------------------------------------------
# AgentStore — list edge cases
# ------------------------------------------------------------------


class TestAgentStoreListEdgeCases:

    def test_list_skips_nonexistent_builtin_dirs(self, workspace_agents, tmp_path):
        store = AgentStore(workspace_agents, builtin_dir=str(tmp_path / "gone"))
        agents = store.list()
        assert agents == []

    def test_list_skips_dirs_without_definition_json(self, store, builtin_agents):
        """Directories without definition.json are ignored."""
        os.makedirs(os.path.join(builtin_agents, "empty_dir"))
        agents = store.list()
        names = [a.name for a in agents]
        assert "empty_dir" not in names

    def test_list_workspace_overrides_builtin(self, store, workspace_agents):
        ws = os.path.join(workspace_agents, "default")
        os.makedirs(ws, exist_ok=True)
        with open(os.path.join(ws, "definition.json"), "w") as f:
            json.dump({"name": "default", "description": "ws-override"}, f)

        agents = store.list()
        default = [a for a in agents if a.name == "default"][0]
        assert default.builtin is False
        assert default.description == "ws-override"


# ------------------------------------------------------------------
# AgentStore — scaffold
# ------------------------------------------------------------------


class TestAgentStoreScaffold:

    def test_scaffold_creates_definition_and_template(self, store, workspace_agents):
        agent = AgentDef(name="scaffolded", description="new")
        store.scaffold(agent)

        loaded = store.get("scaffolded")
        assert loaded is not None
        assert loaded.name == "scaffolded"

        tpl_path = os.path.join(workspace_agents, "scaffolded", "system.j2")
        assert os.path.isfile(tpl_path)

    def test_scaffold_does_not_overwrite_existing_template(
        self, store, workspace_agents
    ):
        agent = AgentDef(name="keep-tpl", description="test")
        store.save(agent)

        tpl_dir = os.path.join(workspace_agents, "keep-tpl")
        os.makedirs(tpl_dir, exist_ok=True)
        tpl_path = os.path.join(tpl_dir, "system.j2")
        with open(tpl_path, "w") as f:
            f.write("CUSTOM TEMPLATE")

        store.scaffold(agent)
        with open(tpl_path) as f:
            assert f.read() == "CUSTOM TEMPLATE"

    def test_scaffold_no_default_builtin_raises_on_missing_default(self, tmp_path):
        """When no 'default' builtin exists, scaffold crashes with TypeError
        because _bi_dir returns None and _tpl_path passes it to os.path.join.
        This documents the failure mode for clients."""
        ws = tmp_path / "ws"
        bi = tmp_path / "bi"
        ws.mkdir()
        bi.mkdir()
        store = AgentStore(str(ws), builtin_dir=str(bi))

        agent = AgentDef(name="orphan", description="no default")
        with pytest.raises(TypeError):
            store.scaffold(agent)


# ------------------------------------------------------------------
# AgentStore — template I/O
# ------------------------------------------------------------------


class TestAgentStoreTemplates:

    def test_read_template_from_workspace(self, store, workspace_agents):
        d = os.path.join(workspace_agents, "ws-tpl")
        os.makedirs(d)
        with open(os.path.join(d, "system.j2"), "w") as f:
            f.write("WS_CONTENT")
        assert store.read_template("ws-tpl") == "WS_CONTENT"

    def test_read_template_from_builtin(self, store):
        assert "{{ agent.name }}" in store.read_template("default")

    def test_read_template_fallback_to_default_with_warning(
        self, store, builtin_agents, caplog
    ):
        """Agent with definition but no template falls back to default."""
        d = os.path.join(builtin_agents, "no-tpl")
        os.makedirs(d)
        with open(os.path.join(d, "definition.json"), "w") as f:
            json.dump({"name": "no-tpl"}, f)

        import logging

        with caplog.at_level(logging.WARNING):
            result = store.read_template("no-tpl")
        assert result != ""
        assert "MISSING TEMPLATE" in caplog.text

    def test_read_template_returns_empty_when_nothing_found(self, tmp_path):
        ws = tmp_path / "ws"
        bi = tmp_path / "bi"
        ws.mkdir()
        bi.mkdir()
        store = AgentStore(str(ws), builtin_dir=str(bi))
        assert store.read_template("ghost") == ""

    def test_has_template_workspace(self, store, workspace_agents):
        d = os.path.join(workspace_agents, "has-tpl")
        os.makedirs(d)
        with open(os.path.join(d, "system.j2"), "w") as f:
            f.write("ok")
        assert store.has_template("has-tpl") is True

    def test_has_template_builtin(self, store):
        assert store.has_template("default") is True

    def test_has_template_missing(self, store):
        assert store.has_template("no-such-agent") is False

    def test_save_template(self, store, workspace_agents):
        store.save_template("saved-tpl", "SAVED CONTENT")
        path = os.path.join(workspace_agents, "saved-tpl", "system.j2")
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "SAVED CONTENT"

    def test_template_path_workspace_first(self, store, workspace_agents):
        d = os.path.join(workspace_agents, "tp-ws")
        os.makedirs(d)
        with open(os.path.join(d, "system.j2"), "w") as f:
            f.write("ws")
        p = store.template_path("tp-ws")
        assert workspace_agents in p

    def test_template_path_falls_back_to_builtin(self, store, builtin_agents):
        p = store.template_path("default")
        assert builtin_agents in p

    def test_template_path_raises_when_agent_not_in_builtin(self, store):
        """When _bi_dir returns None, _tpl_path raises TypeError because
        os.path.join receives None. Documents the failure mode."""
        with pytest.raises(TypeError):
            store.template_path("ghost")


# ------------------------------------------------------------------
# AgentStore — template_inputs
# ------------------------------------------------------------------


class TestAgentStoreTemplateInputs:

    def test_template_inputs_extracts_custom_vars(self, store, workspace_agents):
        d = os.path.join(workspace_agents, "inputs-agent")
        os.makedirs(d)
        with open(os.path.join(d, "system.j2"), "w") as f:
            f.write("Hello {{ custom_var }} and {{ agent.name }}")
        with open(os.path.join(d, "definition.json"), "w") as f:
            json.dump({"name": "inputs-agent"}, f)

        result = store.template_inputs("inputs-agent")
        assert "custom_var" in result
        assert "agent" not in result  # standard var excluded

    def test_template_inputs_empty_template(self, tmp_path):
        ws = tmp_path / "ws"
        bi = tmp_path / "bi"
        ws.mkdir()
        bi.mkdir()
        store = AgentStore(str(ws), builtin_dir=str(bi))
        assert store.template_inputs("ghost") == []

    def test_template_inputs_invalid_jinja_returns_empty(
        self, store, workspace_agents
    ):
        d = os.path.join(workspace_agents, "bad-jinja")
        os.makedirs(d)
        with open(os.path.join(d, "system.j2"), "w") as f:
            f.write("{% if %}broken{% endif %}")
        assert store.template_inputs("bad-jinja") == []


# ------------------------------------------------------------------
# _content_len
# ------------------------------------------------------------------


class TestContentLen:

    def test_string_content(self):
        assert _content_len({"content": "hello"}) == 5

    def test_list_content(self):
        msg = {"content": [{"text": "ab"}, {"text": "cde"}]}
        assert _content_len(msg) == 5

    def test_list_with_non_dict_items(self):
        msg = {"content": ["not-a-dict", {"text": "ok"}]}
        assert _content_len(msg) == 2

    def test_none_content(self):
        assert _content_len({"content": None}) == 0

    def test_missing_content(self):
        assert _content_len({}) == 0

    def test_numeric_content(self):
        assert _content_len({"content": 42}) == 0


# ------------------------------------------------------------------
# needs_compression
# ------------------------------------------------------------------


class TestNeedsCompression:

    def test_under_threshold(self):
        msgs = [{"content": "short"}] * 3
        assert needs_compression(msgs, 100_000) is False

    def test_above_threshold_but_few_messages(self):
        """Even large content doesn't trigger if message count is low."""
        big = {"content": "x" * 100_000}
        msgs = [big] * 5
        assert needs_compression(msgs, 10_000, keep_recent=6) is False

    def test_above_threshold_and_enough_messages(self):
        big = {"content": "x" * 10_000}
        msgs = [big] * 20
        assert needs_compression(msgs, 10_000) is True

    def test_non_dict_messages_skipped(self):
        msgs = [None, "not-a-dict", {"content": "x" * 10_000}] * 10
        result = needs_compression(msgs, 5_000)
        assert isinstance(result, bool)

    def test_empty_messages(self):
        assert needs_compression([], 10_000) is False


# ------------------------------------------------------------------
# compress_messages
# ------------------------------------------------------------------


class TestCompressMessages:

    def _make_messages(self, n: int, char_len: int = 5000) -> list[dict]:
        msgs = [{"role": "system", "content": "System prompt."}]
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append({"role": role, "content": "x" * char_len})
        return msgs

    def test_no_compression_under_threshold(self):
        msgs = [{"role": "system", "content": "hi"}]
        llm = MagicMock()
        result, compressed = compress_messages(msgs, 100_000, llm)
        assert compressed is False
        assert result is msgs
        llm.complete.assert_not_called()

    def test_successful_compression(self, tmp_path):
        msgs = self._make_messages(20, char_len=5000)
        llm = MagicMock()
        llm.complete.return_value = "Summary of the conversation."

        compressor_dir = tmp_path / "compressor"
        compressor_dir.mkdir()
        (compressor_dir / "system.j2").write_text(
            "Summarize: {% for m in messages %}{{ m.content }}{% endfor %}"
        )

        with patch(
            "acai.orchestrator.agent_store._PACKAGE_AGENTS_DIR", str(tmp_path)
        ):
            result, was_compressed = compress_messages(msgs, 10_000, llm)

        assert was_compressed is True
        assert len(result) < len(msgs)
        assert result[0]["role"] == "system"
        assert "summary" in result[1]["content"].lower()

    def test_empty_summary_returns_original(self, tmp_path):
        msgs = self._make_messages(20, char_len=5000)
        llm = MagicMock()
        llm.complete.return_value = "   "

        compressor_dir = tmp_path / "compressor"
        compressor_dir.mkdir()
        (compressor_dir / "system.j2").write_text("Summarize: {{ messages }}")

        with patch(
            "acai.orchestrator.agent_store._PACKAGE_AGENTS_DIR", str(tmp_path)
        ):
            result, was_compressed = compress_messages(msgs, 10_000, llm)

        assert was_compressed is False
        assert result is msgs

    def test_llm_exception_returns_original(self, tmp_path):
        msgs = self._make_messages(20, char_len=5000)
        llm = MagicMock()
        llm.complete.side_effect = RuntimeError("LLM down")

        compressor_dir = tmp_path / "compressor"
        compressor_dir.mkdir()
        (compressor_dir / "system.j2").write_text("Summarize: {{ messages }}")

        with patch(
            "acai.orchestrator.agent_store._PACKAGE_AGENTS_DIR", str(tmp_path)
        ):
            result, was_compressed = compress_messages(msgs, 10_000, llm)

        assert was_compressed is False
        assert result is msgs

    def test_missing_compressor_template_returns_original(self, tmp_path):
        msgs = self._make_messages(20, char_len=5000)
        llm = MagicMock()

        with patch(
            "acai.orchestrator.agent_store._PACKAGE_AGENTS_DIR",
            str(tmp_path / "no_agents"),
        ):
            result, was_compressed = compress_messages(msgs, 10_000, llm)

        assert was_compressed is False
        llm.complete.assert_not_called()

    def test_compression_without_system_message(self, tmp_path):
        msgs = [
            {"role": "user", "content": "x" * 5000}
            for _ in range(20)
        ]
        llm = MagicMock()
        llm.complete.return_value = "Summary."

        compressor_dir = tmp_path / "compressor"
        compressor_dir.mkdir()
        (compressor_dir / "system.j2").write_text("Summarize: {{ messages }}")

        with patch(
            "acai.orchestrator.agent_store._PACKAGE_AGENTS_DIR", str(tmp_path)
        ):
            result, was_compressed = compress_messages(msgs, 10_000, llm)

        assert was_compressed is True
        assert result[0]["role"] == "system"
        assert "summary" in result[0]["content"].lower()

    def test_compression_too_few_old_messages(self, tmp_path):
        """If all messages are 'recent', no compression occurs."""
        msgs = [
            {"role": "system", "content": "x" * 40000},
            {"role": "user", "content": "x" * 40000},
        ]
        llm = MagicMock()
        result, was_compressed = compress_messages(
            msgs, 10_000, llm, keep_recent=6
        )
        assert was_compressed is False


# ------------------------------------------------------------------
# resolve_task
# ------------------------------------------------------------------


class TestResolveTask:

    def _make_task(self, **overrides):
        defaults = dict(
            id="t1",
            kind="code",
            title="Do something",
            description="desc",
            priority="normal",
            project="proj1",
            agent="coder",
            gpu=False,
            parent_task="",
            root_task="",
            spec="",
            spec_path="",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_basic_resolve(self):
        task = self._make_task()
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["id"] == "t1"
        assert result["title"] == "Do something"
        assert result["messages"] == []
        assert result["project_obj"] is None

    def test_spec_content_from_spec_field(self):
        task = self._make_task(spec="inline spec content")
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["spec_content"] == "inline spec content"

    def test_spec_content_from_file(self, tmp_path):
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("File spec content")
        task = self._make_task(spec_path=str(spec_file))
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["spec_content"] == "File spec content"

    def test_spec_content_file_read_error(self, tmp_path):
        task = self._make_task(spec_path="/nonexistent/spec.md")
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["spec_content"] == ""

    def test_conversation_json_parsing(self, tmp_path):
        conv_dir = tmp_path / "conv123"
        conv_dir.mkdir()
        conv_file = conv_dir / "conversation.json"
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool_call", "content": "fn()"},
            {"role": "tool_result", "content": "result"},
        ]
        conv_file.write_text(json.dumps(messages))

        task = self._make_task(spec_path=str(conv_file))
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["conversation"] == "conv123"
        roles = [m["role"] for m in result["messages"]]
        assert "tool_call" not in roles
        assert "tool_result" not in roles
        assert "user" in roles

    def test_conversation_json_invalid(self, tmp_path):
        conv_dir = tmp_path / "bad_conv"
        conv_dir.mkdir()
        conv_file = conv_dir / "conversation.json"
        conv_file.write_text("NOT VALID JSON{{{")

        task = self._make_task(spec_path=str(conv_file))
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["messages"] == []

    def test_project_obj_resolved(self):
        projects = MagicMock()
        projects.get.return_value = {"name": "proj1", "path": "/code"}
        task = self._make_task(project="proj1")
        result = resolve_task(task, SimpleNamespace(), None, projects)
        assert result["project_obj"] == {"name": "proj1", "path": "/code"}

    def test_project_spec_loaded(self, tmp_path):
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "spec.md").write_text("Project specification")

        config = SimpleNamespace(scribe=SimpleNamespace(specs_dir=str(specs_dir)))
        task = self._make_task()
        result = resolve_task(task, config, None, None)
        assert result["project_spec"] == "Project specification"

    def test_project_spec_missing_file(self, tmp_path):
        config = SimpleNamespace(
            scribe=SimpleNamespace(specs_dir=str(tmp_path / "no_specs"))
        )
        task = self._make_task()
        result = resolve_task(task, config, None, None)
        assert result["project_spec"] == ""

    def test_prior_work_from_task(self):
        task = self._make_task(prior_work=["commit abc", "commit def"])
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["prior_work"] == ["commit abc", "commit def"]

    def test_prior_work_defaults_to_empty(self):
        task = self._make_task()
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["prior_work"] == []

    def test_none_optional_fields(self):
        task = self._make_task(
            description=None,
            project=None,
            agent=None,
            parent_task=None,
            root_task=None,
            spec=None,
            spec_path=None,
        )
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["description"] == ""
        assert result["project"] == ""
        assert result["agent"] == ""

    def test_worktree_from_task(self):
        task = self._make_task(worktree="/tmp/wt")
        result = resolve_task(task, SimpleNamespace(), None, None)
        assert result["worktree"] == "/tmp/wt"


# ------------------------------------------------------------------
# hydrate_task
# ------------------------------------------------------------------


class TestHydrateTask:

    def _make_resolved(self, **overrides):
        defaults = {
            "id": "t1",
            "messages": [],
            "project_obj": None,
            "project_spec": "",
        }
        defaults.update(overrides)
        return defaults

    def test_text_output_format(self, store):
        agent = AgentDef(name="text-agent", output_format="text")
        store.save_template("text-agent", "You are a text agent.")
        resolved = self._make_resolved()

        with patch("acai.tasks.nodes.describe_registry", return_value=""):
            result = hydrate_task(agent, store, resolved)

        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a text agent."

    def test_messages_output_format_valid_json(self, store):
        agent = AgentDef(name="json-agent", output_format="messages")
        tpl = '[{"role": "system", "content": "Hello from {{ agent.name }}."}]'
        store.save_template("json-agent", tpl)
        resolved = self._make_resolved()

        with patch("acai.tasks.nodes.describe_registry", return_value=""):
            result = hydrate_task(agent, store, resolved)

        assert result[0]["role"] == "system"
        assert "json-agent" in result[0]["content"]

    def test_messages_output_format_invalid_json_fallback(self, store):
        """Non-JSON template output is gracefully wrapped as text."""
        agent = AgentDef(name="bad-json", output_format="messages")
        store.save_template("bad-json", "Not valid JSON {{ agent.name }}")
        resolved = self._make_resolved()

        with patch("acai.tasks.nodes.describe_registry", return_value=""):
            result = hydrate_task(agent, store, resolved)

        assert result[0]["role"] == "system"
        assert "bad-json" in result[0]["content"]

    def test_template_src_override(self, store):
        agent = AgentDef(name="override", output_format="text")
        resolved = self._make_resolved()

        with patch("acai.tasks.nodes.describe_registry", return_value=""):
            result = hydrate_task(
                agent,
                store,
                resolved,
                template_src="Custom prompt for {{ agent.name }}",
            )

        assert "override" in result[0]["content"]

    def test_extra_context_passed_to_template(self, store):
        agent = AgentDef(name="ctx-agent", output_format="text")
        store.save_template("ctx-agent", "Custom: {{ my_var }}")
        resolved = self._make_resolved()

        with patch("acai.tasks.nodes.describe_registry", return_value=""):
            result = hydrate_task(
                agent,
                store,
                resolved,
                extra_context={"my_var": "injected_value"},
            )

        assert "injected_value" in result[0]["content"]

    def test_text_format_appends_resolved_messages(self, store):
        agent = AgentDef(name="text-msgs", output_format="text")
        store.save_template("text-msgs", "System prompt.")
        resolved = self._make_resolved(
            messages=[{"role": "user", "content": "user msg"}]
        )

        with patch("acai.tasks.nodes.describe_registry", return_value=""):
            result = hydrate_task(agent, store, resolved)

        assert len(result) == 2
        assert result[1]["role"] == "user"


# ------------------------------------------------------------------
# Additional coverage for remaining branches
# ------------------------------------------------------------------


class TestAgentDefFromDictSandboxEdge:

    def test_from_dict_sandbox_non_dict_non_bool_ignored(self):
        """When sandbox is an unexpected type (e.g. str), uses_sandbox
        keeps its dataclass default."""
        d = {"name": "edge", "sandbox": "unexpected_string"}
        agent = AgentDef.from_dict(d)
        assert agent.uses_sandbox is True  # dataclass default


class TestAgentStorePrivateHelpers:

    def test_is_builtin(self, store):
        assert store._is_builtin("default") is True
        assert store._is_builtin("nonexistent") is False

    def test_has_workspace_override(self, store, workspace_agents):
        assert store._has_workspace_override("default") is False
        agent = AgentDef(name="ws-only", description="test")
        store.save(agent)
        assert store._has_workspace_override("ws-only") is True


class TestAgentStoreListMoreBranches:

    def test_list_workspace_dir_missing(self, tmp_path):
        """If workspace_dir doesn't exist after init, list() still works."""
        ws = str(tmp_path / "ws")
        bi = str(tmp_path / "bi")
        os.makedirs(bi)
        store = AgentStore(ws, builtin_dir=bi)
        import shutil
        shutil.rmtree(ws)
        agents = store.list()
        assert agents == []


class TestCompressMessagesMoreBranches:

    def test_no_old_messages_after_split(self, tmp_path):
        """When all non-system messages are in the recent window,
        old_messages is empty and compression is skipped."""
        msgs = [{"role": "system", "content": "x" * 50_000}]
        msgs += [{"role": "user", "content": "x" * 50_000} for _ in range(7)]
        llm = MagicMock()

        compressor_dir = tmp_path / "compressor"
        compressor_dir.mkdir()
        (compressor_dir / "system.j2").write_text("Summarize: {{ messages }}")

        with patch(
            "acai.orchestrator.agent_store._PACKAGE_AGENTS_DIR", str(tmp_path)
        ):
            result, was_compressed = compress_messages(
                msgs, 10_000, llm, keep_recent=8
            )
        assert was_compressed is False


class TestResolveTaskMoreBranches:

    def _make_task(self, **overrides):
        defaults = dict(
            id="t1",
            kind="code",
            title="Do something",
            description="desc",
            priority="normal",
            project="proj1",
            agent="coder",
            gpu=False,
            parent_task="",
            root_task="",
            spec="",
            spec_path="",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_spec_path_file_permission_error(self, tmp_path):
        """OSError reading spec_path is silently caught."""
        spec = tmp_path / "unreadable.md"
        spec.write_text("content")
        spec.chmod(0o000)
        task = self._make_task(spec_path=str(spec))
        try:
            result = resolve_task(task, SimpleNamespace(), None, None)
            assert result["spec_content"] == ""
        finally:
            spec.chmod(0o644)

    def test_project_spec_oserror(self, tmp_path):
        """OSError reading project spec.md is caught."""
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        spec_file = specs_dir / "spec.md"
        spec_file.write_text("content")
        spec_file.chmod(0o000)

        config = SimpleNamespace(
            scribe=SimpleNamespace(specs_dir=str(specs_dir))
        )
        task = self._make_task()
        try:
            result = resolve_task(task, config, None, None)
            assert result["project_spec"] == ""
        finally:
            spec_file.chmod(0o644)
