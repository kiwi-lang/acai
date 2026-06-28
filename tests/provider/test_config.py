"""Tests for acai.provider.config — ModelSet/ModelSetEntry serialization."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from acai.provider.config import (
    ModelSetEntry,
    ModelSet,
    ProviderConfig,
    ModelConfig,
    load_model_sets,
    save_model_sets,
    _model_set_entry_to_dict,
    _model_set_to_dict,
)


class TestModelSetEntry:

    def test_from_dict_full(self):
        data = {
            "provider": "vllm",
            "model": "qwen3",
            "price_input": 1.5,
            "price_output": 2.0,
            "complexity_min": "high",
        }
        entry = ModelSetEntry.from_dict(data)
        assert entry.provider == "vllm"
        assert entry.model == "qwen3"
        assert entry.price_input == 1.5
        assert entry.price_output == 2.0
        assert entry.complexity_min == "high"

    def test_from_dict_defaults(self):
        entry = ModelSetEntry.from_dict({"provider": "p", "model": "m"})
        assert entry.price_input == 0.0
        assert entry.price_output == 0.0
        assert entry.complexity_min == "low"

    def test_to_dict_roundtrip(self):
        entry = ModelSetEntry(
            provider="openai", model="gpt-4", price_input=30.0,
            price_output=60.0, complexity_min="medium",
        )
        d = _model_set_entry_to_dict(entry)
        restored = ModelSetEntry.from_dict(d)
        assert restored.provider == entry.provider
        assert restored.model == entry.model
        assert restored.price_input == entry.price_input
        assert restored.price_output == entry.price_output
        assert restored.complexity_min == entry.complexity_min


class TestModelSet:

    def test_from_dict(self):
        data = {
            "name": "budget",
            "default": True,
            "entries": [
                {"provider": "vllm", "model": "small", "price_input": 0.1, "price_output": 0.1},
            ],
        }
        ms = ModelSet.from_dict(data)
        assert ms.name == "budget"
        assert ms.default is True
        assert len(ms.entries) == 1
        assert ms.entries[0].model == "small"

    def test_from_dict_empty_entries(self):
        ms = ModelSet.from_dict({"name": "empty"})
        assert ms.name == "empty"
        assert ms.default is False
        assert ms.entries == []

    def test_to_dict_roundtrip(self):
        ms = ModelSet(
            name="test",
            default=False,
            entries=[
                ModelSetEntry(provider="a", model="b", price_input=1.0, price_output=2.0),
            ],
        )
        d = _model_set_to_dict(ms)
        restored = ModelSet.from_dict(d)
        assert restored.name == ms.name
        assert restored.default == ms.default
        assert len(restored.entries) == 1
        assert restored.entries[0].provider == "a"


class TestLoadSaveModelSets:

    def test_save_and_load(self, tmp_path):
        workspace = str(tmp_path / "ws")
        os.makedirs(workspace)
        sets = [
            ModelSet(name="primary", default=True, entries=[
                ModelSetEntry(provider="vllm", model="m1", price_input=0, price_output=0),
            ]),
            ModelSet(name="secondary", default=False, entries=[]),
        ]

        save_model_sets(workspace, sets)
        loaded = load_model_sets(workspace)

        assert len(loaded) == 2
        assert loaded[0].name == "primary"
        assert loaded[0].default is True
        assert loaded[1].name == "secondary"

    def test_load_missing_file_returns_empty(self, tmp_path):
        result = load_model_sets(str(tmp_path / "nonexistent"))
        assert result == []

    def test_save_preserves_other_yaml_keys(self, tmp_path):
        import yaml

        workspace = str(tmp_path / "ws")
        os.makedirs(workspace)
        yaml_path = os.path.join(workspace, "acai.yaml")
        with open(yaml_path, "w") as f:
            yaml.dump({"providers": [{"name": "test"}], "other_key": "value"}, f)

        sets = [ModelSet(name="x", default=True, entries=[])]
        save_model_sets(workspace, sets)

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        assert data["other_key"] == "value"
        assert "model_sets" in data


class TestProviderConfig:

    def test_provider_config_basics(self):
        pc = ProviderConfig(name="test", backend="openai", endpoint="http://x")
        assert pc.name == "test"
        assert pc.backend == "openai"

    def test_model_config_has_expected_fields(self):
        mc = ModelConfig(name="M", slug="m")
        assert mc.name == "M"
        assert mc.slug == "m"
        assert mc.max_tokens == 0
        assert mc.context_window == 0
        assert isinstance(mc.smart_weight, int)
        assert isinstance(mc.cost_weight, int)

    def test_default_endpoint_from_port(self):
        pc = ProviderConfig(name="p", server_port=9200)
        assert pc.endpoint == "http://127.0.0.1:9200"

    def test_explicit_endpoint_preserved(self):
        pc = ProviderConfig(name="p", endpoint="http://custom:8080", server_port=9200)
        assert pc.endpoint == "http://custom:8080"

    def test_default_model(self):
        m = ModelConfig(name="Model1", slug="model1")
        pc = ProviderConfig(name="p", models=[m])
        assert pc.default_model is m
        assert pc.model_slug == "model1"
        assert pc.model == "Model1"
        assert pc.slug == "model1"

    def test_no_models(self):
        pc = ProviderConfig(name="p")
        assert pc.default_model is None
        assert pc.model_slug == ""
        assert pc.model == ""

    def test_get_model(self):
        m1 = ModelConfig(name="A", slug="a")
        m2 = ModelConfig(name="B", slug="b")
        pc = ProviderConfig(name="p", models=[m1, m2])
        assert pc.get_model("b") is m2
        assert pc.get_model("missing") is None

    def test_resolve_model_fills_zeros(self):
        pc = ProviderConfig(name="p", max_tokens=8192, context_window=64000)
        m = ModelConfig(name="M", slug="m", max_tokens=0, context_window=0)
        resolved = pc.resolve_model(m)
        assert resolved.max_tokens == 8192
        assert resolved.context_window == 64000

    def test_resolve_model_keeps_nonzero(self):
        pc = ProviderConfig(name="p", max_tokens=8192, context_window=64000)
        m = ModelConfig(name="M", slug="m", max_tokens=16384, context_window=128000)
        resolved = pc.resolve_model(m)
        assert resolved.max_tokens == 16384
        assert resolved.context_window == 128000

    def test_build_command_vllm(self):
        m = ModelConfig(name="Qwen/Qwen3-Coder", slug="qwen3-coder")
        pc = ProviderConfig(name="p", backend="vllm", models=[m], server_port=9200)
        cmd = pc.build_command()
        assert "vllm serve" in cmd
        assert "--tool-call-parser" in cmd
        assert "9200" in cmd

    def test_build_command_llamacpp(self):
        m = ModelConfig(name="model.gguf", slug="model")
        pc = ProviderConfig(name="p", backend="llamacpp", models=[m])
        cmd = pc.build_command()
        assert "llama-server" in cmd

    def test_build_command_custom_template(self):
        m = ModelConfig(name="my-model", slug="mm")
        pc = ProviderConfig(
            name="p", backend="custom",
            launch_template="run-server --model {model} --port {server_port}",
            models=[m], server_port=8000,
        )
        cmd = pc.build_command()
        assert cmd == "run-server --model my-model --port 8000"

    def test_build_command_unmanaged(self):
        pc = ProviderConfig(name="p", backend="openai", endpoint="http://api.openai.com")
        assert pc.build_command() == ""
        assert pc.managed is False

    def test_managed_property(self):
        m = ModelConfig(name="m", slug="m")
        pc = ProviderConfig(name="p", backend="vllm", models=[m])
        assert pc.managed is True

    def test_supports_thinking_qwen3(self):
        m = ModelConfig(name="Qwen/Qwen3-Chat", slug="qwen3-chat")
        pc = ProviderConfig(name="p", models=[m])
        assert pc.supports_thinking is True

    def test_supports_thinking_coder_excluded(self):
        m = ModelConfig(name="Qwen/Qwen3-Coder-Next", slug="qwen3-coder-next")
        pc = ProviderConfig(name="p", models=[m])
        assert pc.supports_thinking is False

    def test_supports_thinking_no_model(self):
        pc = ProviderConfig(name="p")
        assert pc.supports_thinking is False

    def test_from_dict_with_models(self):
        data = {
            "name": "vllm-local",
            "backend": "vllm",
            "server_port": 9300,
            "models": [
                {"name": "ModelA", "slug": "model-a", "max_tokens": 16384},
                {"name": "ModelB", "slug": "model-b"},
            ],
        }
        pc = ProviderConfig.from_dict(data)
        assert pc.name == "vllm-local"
        assert len(pc.models) == 2
        assert pc.models[0].slug == "model-a"
        assert pc.models[0].max_tokens == 16384

    def test_from_dict_legacy_model_field(self):
        data = {"name": "old", "model": "Org/Model-Name", "slug": "model-name"}
        pc = ProviderConfig.from_dict(data)
        assert len(pc.models) == 1
        assert pc.models[0].name == "Org/Model-Name"
        assert pc.models[0].slug == "model-name"

    def test_from_dict_legacy_model_no_slug(self):
        data = {"name": "old", "model": "Org/My-Model"}
        pc = ProviderConfig.from_dict(data)
        assert pc.models[0].slug == "my-model"

    def test_from_dict_no_models(self):
        data = {"name": "empty"}
        pc = ProviderConfig.from_dict(data)
        assert pc.models == []


class TestToolParserGuess:
    """Tests for _guess_tool_parser and _guess_reasoning_parser."""

    def test_qwen3_coder(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("Qwen/Qwen3-Coder-Next-FP8") == "qwen3_coder"

    def test_qwen3_chat(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("Qwen/Qwen3-32B") == "hermes"

    def test_llama4(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("meta/Llama-4-Scout") == "llama4_pythonic"

    def test_llama3(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("Meta-Llama-3.1-70B") == "llama3_json"

    def test_deepseek_v3(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("deepseek-ai/DeepSeek-V3") == "deepseek_v3"

    def test_unknown_model(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("UnknownModel") == "hermes"

    def test_gemma4(self):
        from acai.provider.config import _guess_tool_parser
        assert _guess_tool_parser("google/gemma-4-27b") == "gemma4"

    def test_reasoning_deepseek_r1(self):
        from acai.provider.config import _guess_reasoning_parser
        assert _guess_reasoning_parser("DeepSeek-R1") == "deepseek_r1"

    def test_reasoning_qwen3_coder_excluded(self):
        from acai.provider.config import _guess_reasoning_parser
        assert _guess_reasoning_parser("Qwen3-Coder-Next") is None

    def test_reasoning_qwen3_chat(self):
        from acai.provider.config import _guess_reasoning_parser
        assert _guess_reasoning_parser("Qwen3-32B") == "qwen3"

    def test_reasoning_unknown(self):
        from acai.provider.config import _guess_reasoning_parser
        assert _guess_reasoning_parser("UnknownModel") is None


class TestLoadSaveProviders:
    def test_save_and_load(self, tmp_path):
        from acai.provider.config import load_providers, save_providers

        workspace = str(tmp_path / "ws")
        os.makedirs(workspace)
        providers = [
            ProviderConfig(name="p1", backend="vllm", server_port=9123,
                          models=[ModelConfig(name="M1", slug="m1")]),
            ProviderConfig(name="p2", backend="openai", endpoint="http://api"),
        ]
        save_providers(workspace, providers)
        loaded = load_providers(workspace)
        assert len(loaded) == 2
        assert loaded[0].name == "p1"
        assert loaded[0].models[0].slug == "m1"
        assert loaded[1].name == "p2"

    def test_load_missing_file(self, tmp_path):
        from acai.provider.config import load_providers
        assert load_providers(str(tmp_path / "nope")) == []

    def test_save_preserves_other_keys(self, tmp_path):
        import yaml
        from acai.provider.config import save_providers

        workspace = str(tmp_path / "ws")
        os.makedirs(workspace)
        yaml_path = os.path.join(workspace, "acai.yaml")
        with open(yaml_path, "w") as f:
            yaml.dump({"model_sets": [{"name": "ms"}], "custom": 42}, f)

        save_providers(workspace, [ProviderConfig(name="new")])

        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data["custom"] == 42
        assert data["model_sets"] == [{"name": "ms"}]
        assert len(data["providers"]) == 1


class TestModelToSlug:
    def test_basic(self):
        from acai.provider.config import _model_to_slug
        assert _model_to_slug("Qwen/Qwen3-Coder-Next-FP8") == "qwen3-coder-next-fp8"

    def test_underscores(self):
        from acai.provider.config import _model_to_slug
        assert _model_to_slug("org/My_Model_V2") == "my-model-v2"

    def test_no_slash(self):
        from acai.provider.config import _model_to_slug
        assert _model_to_slug("simple-model") == "simple-model"
