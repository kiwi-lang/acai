"""Tests for acai.orchestrator.config — AcaiConfig and helpers."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from acai.orchestrator.config import (
    AcaiConfig,
    apply_config,
    as_env_var,
    config_global,
    config_to_dict,
    defaultfield,
    DevServiceConfig,
    getenv,
    load_config,
    option,
    sandboxfield,
    SandboxConfig,
    save_config,
    select,
    show_config,
    CIConfig,
    _load_dev_services,
    _CONTAINER,
)
from acai.provider.config import ProviderConfig, ModelConfig, ModelSet, ModelSetEntry


@pytest.fixture
def config(tmp_path):
    """AcaiConfig with a temp workspace and no global state interference."""
    return AcaiConfig(
        workspace=str(tmp_path / "ws"),
        providers=[
            ProviderConfig(name="vllm", backend="vllm", endpoint="http://localhost:8000",
                           models=[ModelConfig(name="M1", slug="m1")]),
            ProviderConfig(name="openai", backend="openai", endpoint="https://api.openai.com"),
        ],
        model_sets=[
            ModelSet(name="default", default=True, entries=[
                ModelSetEntry(provider="vllm", model="m1", price_input=0, price_output=0),
            ]),
            ModelSet(name="expensive", default=False, entries=[
                ModelSetEntry(provider="openai", model="gpt4", price_input=30.0, price_output=60.0),
            ]),
        ],
    )


class TestAcaiConfigInit:

    def test_workspace_made_absolute(self, config):
        assert os.path.isabs(config.workspace)

    def test_workspace_dir_created(self, config):
        assert os.path.isdir(config.workspace)

    def test_default_provider_added_when_empty(self, tmp_path):
        cfg = AcaiConfig(workspace=str(tmp_path / "ws"), providers=[])
        assert len(cfg.providers) == 1


class TestActiveProvider:

    def test_returns_first_provider(self, config):
        prov = config.active_provider()
        assert prov.name == "vllm"

    def test_active_name_override(self, config):
        config._active_name = "openai"
        prov = config.active_provider()
        assert prov.name == "openai"

    def test_active_name_fallback_to_first(self, config):
        config._active_name = "nonexistent"
        prov = config.active_provider()
        assert prov.name == "vllm"


class TestGetProvider:

    def test_get_existing(self, config):
        prov = config.get_provider("vllm")
        assert prov is not None
        assert prov.backend == "vllm"

    def test_get_nonexistent(self, config):
        assert config.get_provider("ghost") is None


class TestModelSets:

    def test_default_model_set(self, config):
        ms = config.default_model_set()
        assert ms is not None
        assert ms.name == "default"
        assert ms.default is True

    def test_default_model_set_fallback_to_first(self, tmp_path):
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            model_sets=[
                ModelSet(name="only", default=False, entries=[]),
            ],
        )
        ms = cfg.default_model_set()
        assert ms.name == "only"

    def test_default_model_set_empty(self, tmp_path):
        cfg = AcaiConfig(workspace=str(tmp_path / "ws"), model_sets=[])
        assert cfg.default_model_set() is None

    def test_get_model_set_by_name(self, config):
        ms = config.get_model_set("expensive")
        assert ms is not None
        assert ms.name == "expensive"

    def test_get_model_set_nonexistent(self, config):
        assert config.get_model_set("ghost") is None


# ---------------------------------------------------------------------------
# getenv — type coercion from environment variables
# ---------------------------------------------------------------------------

class TestGetenv:

    def test_returns_none_when_var_unset(self):
        assert getenv("TOTALLY_MISSING_VAR_XYZ", int) is None

    @pytest.mark.parametrize("val,expected", [
        ("1", True), ("true", True), ("yes", True), ("TRUE", True),
        ("0", False), ("false", False), ("no", False),
    ])
    def test_bool_coercion(self, monkeypatch, val, expected):
        monkeypatch.setenv("TEST_BOOL_VAR", val)
        assert getenv("TEST_BOOL_VAR", bool) is expected

    def test_int_coercion(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "42")
        assert getenv("TEST_INT_VAR", int) == 42

    def test_int_coercion_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_INT_VAR", "not_a_number")
        assert getenv("TEST_INT_VAR", int) is None

    def test_float_coercion_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT_VAR", "abc")
        assert getenv("TEST_FLOAT_VAR", float) is None


# ---------------------------------------------------------------------------
# as_env_var — name → ACAI_NAME mapping
# ---------------------------------------------------------------------------

class TestAsEnvVar:

    def test_simple_name(self):
        assert as_env_var("workspace") == "ACAI_WORKSPACE"

    def test_dotted_name(self):
        assert as_env_var("sandbox.timeout") == "ACAI_SANDBOX_TIMEOUT"


# ---------------------------------------------------------------------------
# select — pick first truthy / first non-None
# ---------------------------------------------------------------------------

class TestSelect:

    def test_returns_first_truthy(self):
        assert select(None, 0, 42) == 42

    def test_returns_first_non_none_when_all_falsy(self):
        assert select(None, 0, "", None) == 0

    def test_all_none_returns_none(self):
        assert select(None, None) is None

    def test_empty_string_non_none_fallback(self):
        assert select(None, "") == ""


# ---------------------------------------------------------------------------
# option — env → config → default resolution
# ---------------------------------------------------------------------------

class TestOption:

    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ACAI_WORKER_TIMEOUT", "999")
        config_global.set({"worker": {"timeout": "50"}})
        try:
            assert option("worker.timeout", int, 300) == 999
        finally:
            config_global.set(None)

    def test_config_value_over_default(self):
        config_global.set({"worker": {"timeout": "77"}})
        try:
            assert option("worker.timeout", int, 300) == 77
        finally:
            config_global.set(None)

    def test_default_used_when_nothing_set(self):
        config_global.set(None)
        assert option("worker.timeout", int, 300) == 300

    def test_returns_none_when_all_none(self):
        config_global.set(None)
        assert option("missing.key", int) is None

    def test_unconvertible_final_value_returns_none(self):
        config_global.set({"bad": {"val": "not_an_int"}})
        try:
            assert option("bad.val", int) is None
        finally:
            config_global.set(None)

    def test_nested_config_non_dict_intermediate(self):
        config_global.set({"worker": "not_a_dict"})
        try:
            result = option("worker.timeout", int, 300)
            assert result == 300
        finally:
            config_global.set(None)


# ---------------------------------------------------------------------------
# apply_config — temporary config overlay
# ---------------------------------------------------------------------------

class TestApplyConfig:

    def test_overlay_visible_inside_context(self):
        config_global.set(None)
        with apply_config({"sandbox.timeout": 999}):
            cfg = config_global.get()
            assert cfg["sandbox"]["timeout"] == 999
        assert config_global.get() is None

    def test_existing_config_restored_after_context(self):
        config_global.set({"existing": "data"})
        try:
            with apply_config({"new_key": "value"}):
                cfg = config_global.get()
                assert cfg["new_key"] == "value"
            restored = config_global.get()
            assert "new_key" not in restored
            assert restored["existing"] == "data"
        finally:
            config_global.set(None)

    def test_nested_dot_keys(self):
        config_global.set(None)
        with apply_config({"a.b.c": 123}):
            cfg = config_global.get()
            assert cfg["a"]["b"]["c"] == 123


# ---------------------------------------------------------------------------
# load_config — YAML loading
# ---------------------------------------------------------------------------

class TestLoadConfig:

    def test_load_none_sets_empty_dict(self):
        old = config_global.get()
        try:
            result = load_config(None)
            assert result == {}
            assert config_global.get() == {}
        finally:
            config_global.set(old)

    def test_load_valid_yaml(self, tmp_path):
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("workspace: /tmp/test\nworker:\n  timeout: 999\n")
        old = config_global.get()
        try:
            result = load_config(str(yaml_file))
            assert result["workspace"] == "/tmp/test"
            assert result["worker"]["timeout"] == 999
        finally:
            config_global.set(old)

    def test_load_empty_yaml_returns_empty_dict(self, tmp_path):
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")
        old = config_global.get()
        try:
            result = load_config(str(yaml_file))
            assert result == {}
        finally:
            config_global.set(old)

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nonexistent.yaml"))


# ---------------------------------------------------------------------------
# show_config — debug display
# ---------------------------------------------------------------------------

class TestShowConfig:

    def test_non_dataclass_is_noop(self, capsys):
        show_config("not a dataclass")
        assert capsys.readouterr().out == ""

    def test_prints_fields(self, capsys, tmp_path):
        from acai.orchestrator.config import GitConfig
        gc = GitConfig()
        show_config(gc, depth=0)
        out = capsys.readouterr().out
        assert "repo_path" in out
        assert "worktree_dir" in out

    def test_nested_dataclass(self, capsys, tmp_path):
        cfg = AcaiConfig(workspace=str(tmp_path / "ws"))
        show_config(cfg)
        out = capsys.readouterr().out
        assert "scribe:" in out
        assert "trigger" in out


# ---------------------------------------------------------------------------
# SandboxConfig.from_dict — unknown keys silently ignored
# ---------------------------------------------------------------------------

class TestSandboxConfigFromDict:

    def test_known_keys_accepted(self):
        sc = SandboxConfig.from_dict({"timeout": 999, "network": False})
        assert sc.timeout == 999
        assert sc.network is False

    def test_unknown_keys_ignored(self):
        sc = SandboxConfig.from_dict({"nonexistent_field": "boom", "timeout": 10})
        assert sc.timeout == 10
        assert not hasattr(sc, "nonexistent_field")

    def test_empty_dict(self):
        sc = SandboxConfig.from_dict({})
        assert sc.type == "podman"


# ---------------------------------------------------------------------------
# SandboxConfig.fields_for_backend — backend-aware field filtering
# ---------------------------------------------------------------------------

class TestSandboxConfigFieldsForBackend:

    def test_container_fields_include_image(self):
        fields = SandboxConfig.fields_for_backend("container")
        assert "image" in fields
        assert "runtime" in fields

    def test_docker_alias_resolves_to_container(self):
        fields = SandboxConfig.fields_for_backend("docker")
        assert "image" in fields

    def test_unknown_backend_returns_empty(self):
        fields = SandboxConfig.fields_for_backend("alien_backend_xyz")
        assert fields == []


# ---------------------------------------------------------------------------
# CIConfig.from_dict
# ---------------------------------------------------------------------------

class TestCIConfigFromDict:

    def test_known_keys_accepted(self):
        ci = CIConfig.from_dict({"platform": "github", "token": "abc"})
        assert ci.platform == "github"
        assert ci.token == "abc"

    def test_unknown_keys_ignored(self):
        ci = CIConfig.from_dict({"platform": "gitlab", "mystery": "val"})
        assert ci.platform == "gitlab"

    def test_empty_dict_uses_field_defaults(self):
        ci = CIConfig.from_dict({})
        assert ci.platform == "auto"


# ---------------------------------------------------------------------------
# _load_dev_services — parsing service entries from config
# ---------------------------------------------------------------------------

class TestLoadDevServices:

    def test_empty_config(self):
        config_global.set(None)
        assert _load_dev_services() == []

    def test_services_loaded(self):
        config_global.set({
            "dev": {
                "services": [
                    {"name": "api", "command": "python app.py", "cwd": "/tmp"},
                    {"name": "worker", "command": "celery worker"},
                ]
            }
        })
        try:
            services = _load_dev_services()
            assert len(services) == 2
            assert services[0].name == "api"
            assert services[0].cwd == "/tmp"
            assert services[1].auto_start is True
        finally:
            config_global.set(None)

    def test_partial_entry_uses_defaults(self):
        config_global.set({"dev": {"services": [{"name": "svc"}]}})
        try:
            services = _load_dev_services()
            assert services[0].command == ""
            assert services[0].cwd == "."
            assert services[0].env == {}
        finally:
            config_global.set(None)


# ---------------------------------------------------------------------------
# AcaiConfig.__post_init__ — path resolution branches
# ---------------------------------------------------------------------------

class TestAcaiConfigPostInit:

    def test_relative_specs_dir_made_absolute(self, tmp_path):
        cfg = AcaiConfig(workspace=str(tmp_path / "ws"))
        assert os.path.isabs(cfg.scribe.specs_dir)
        assert cfg.scribe.specs_dir.startswith(cfg.workspace)

    def test_absolute_specs_dir_unchanged(self, tmp_path):
        from acai.orchestrator.config import ScribeConfig
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            scribe=ScribeConfig(trigger="event", specs_dir="/absolute/specs"),
        )
        assert cfg.scribe.specs_dir == "/absolute/specs"

    def test_absolute_worktree_dir_unchanged(self, tmp_path):
        from acai.orchestrator.config import GitConfig
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            git=GitConfig(repo_path=".", worktree_dir="/abs/wt", auto_commit=True),
        )
        assert cfg.git.worktree_dir == "/abs/wt"

    def test_absolute_tasks_dir_unchanged(self, tmp_path):
        from acai.orchestrator.config import WorkerConfig
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            worker=WorkerConfig(
                max_retries=3, timeout=300, tasks_dir="/abs/tasks",
                host="0.0.0.0", port=5051, orchestrator_url="http://localhost:5050/agent",
            ),
        )
        assert cfg.worker.tasks_dir == "/abs/tasks"

    def test_absolute_audit_dir_unchanged(self, tmp_path):
        from acai.orchestrator.config import AuditConfig
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            audit=AuditConfig(enabled=True, dir="/abs/audit"),
        )
        assert cfg.audit.dir == "/abs/audit"

    def test_sqlite_relative_path_expanded(self, tmp_path):
        from acai.orchestrator.config import QueueConfig
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            queue=QueueConfig(url="sqlite:///work.db", poll_interval=5, task_timeout=300),
        )
        assert cfg.queue.url.startswith("sqlite:///")
        assert os.path.isabs(cfg.queue.url[len("sqlite:///"):])

    def test_sqlite_absolute_path_unchanged(self, tmp_path):
        from acai.orchestrator.config import QueueConfig
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            queue=QueueConfig(url="sqlite:////abs/work.db", poll_interval=5, task_timeout=300),
        )
        assert cfg.queue.url == "sqlite:////abs/work.db"


# ---------------------------------------------------------------------------
# set_active / local_provider
# ---------------------------------------------------------------------------

class TestSetActiveAndLocalProvider:

    def test_set_active(self, config):
        config.set_active("openai")
        assert config._active_name == "openai"
        assert config.active_provider().name == "openai"

    def test_local_provider_returns_none_when_no_managed(self, tmp_path):
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            providers=[
                ProviderConfig(name="remote", backend="openai", endpoint="http://api.openai.com"),
            ],
        )
        assert cfg.local_provider() is None

    def test_local_provider_returns_highest_priority(self, tmp_path):
        cfg = AcaiConfig(
            workspace=str(tmp_path / "ws"),
            providers=[
                ProviderConfig(name="low", backend="vllm", endpoint="http://a",
                               launch_template="run-low", priority=1),
                ProviderConfig(name="high", backend="vllm", endpoint="http://b",
                               launch_template="run-high", priority=10),
                ProviderConfig(name="remote", backend="openai", endpoint="http://c"),
            ],
        )
        lp = cfg.local_provider()
        assert lp is not None
        assert lp.name == "high"

    def test_active_provider_with_empty_providers_list(self, tmp_path):
        cfg = AcaiConfig(workspace=str(tmp_path / "ws"), providers=[])
        prov = cfg.active_provider()
        assert prov is not None


# ---------------------------------------------------------------------------
# config_to_dict — serialisation of persistable sections
# ---------------------------------------------------------------------------

class TestConfigToDict:

    def test_includes_workspace(self, config):
        d = config_to_dict(config)
        assert "workspace" in d
        assert d["workspace"] == config.workspace

    def test_includes_persistable_sections(self, config):
        d = config_to_dict(config)
        for section in ("sandbox", "worker", "git", "queue", "audit", "ci", "tts"):
            assert section in d

    def test_excludes_providers(self, config):
        d = config_to_dict(config)
        assert "providers" not in d


# ---------------------------------------------------------------------------
# save_config — atomic YAML persistence
# ---------------------------------------------------------------------------

class TestSaveConfig:

    def test_creates_file(self, tmp_path):
        ws = str(tmp_path / "ws")
        cfg = AcaiConfig(workspace=ws)
        save_config(ws, cfg)
        import yaml
        path = os.path.join(os.path.abspath(ws), "acai.yaml")
        assert os.path.isfile(path)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "sandbox" in data
        assert "worker" in data

    def test_merges_with_existing_file(self, tmp_path):
        import yaml
        ws = str(tmp_path / "ws")
        os.makedirs(ws, exist_ok=True)
        path = os.path.join(ws, "acai.yaml")
        with open(path, "w") as f:
            yaml.safe_dump({"providers": [{"name": "keep_me"}]}, f)

        cfg = AcaiConfig(workspace=ws)
        save_config(ws, cfg)

        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["providers"] == [{"name": "keep_me"}]
        assert "sandbox" in data

    def test_handles_empty_existing_yaml(self, tmp_path):
        ws = str(tmp_path / "ws")
        os.makedirs(ws, exist_ok=True)
        path = os.path.join(ws, "acai.yaml")
        with open(path, "w") as f:
            f.write("")

        cfg = AcaiConfig(workspace=ws)
        save_config(ws, cfg)
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "sandbox" in data


# ---------------------------------------------------------------------------
# sandboxfield — metadata annotation
# ---------------------------------------------------------------------------

class TestSandboxField:

    def test_list_default_uses_factory(self):
        from dataclasses import fields as dc_fields
        for f in dc_fields(SandboxConfig):
            if f.name == "writable_paths":
                assert "backends" in f.metadata
                break

    def test_scalar_default(self):
        from dataclasses import fields as dc_fields
        for f in dc_fields(SandboxConfig):
            if f.name == "timeout":
                assert f.default == 120
                assert "backends" in f.metadata
                break
