"""Tests for acai.provider.router — ModelRouter selection logic."""

from __future__ import annotations

import pytest

from acai.provider.config import (
    ModelSetEntry,
    ModelSet,
    ProviderConfig,
    ModelConfig,
)
from acai.provider.router import ModelRouter


def _provider(name="vllm", models=None):
    """Build a minimal ProviderConfig."""
    return ProviderConfig(
        name=name,
        backend="vllm",
        endpoint="http://localhost:8000",
        models=models or [
            ModelConfig(name="Model-A", slug="model-a", smart_weight=10),
            ModelConfig(name="Model-B", slug="model-b", smart_weight=5),
        ],
    )


def _model_set(entries, name="default", default=True):
    return ModelSet(name=name, default=default, entries=entries)


class TestModelRouterSelect:

    def test_selects_highest_smart_weight(self):
        prov = _provider("p1", models=[
            ModelConfig(name="Smart", slug="smart", smart_weight=100),
            ModelConfig(name="Dumb", slug="dumb", smart_weight=1),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="p1", model="smart", price_input=1.0, price_output=1.0),
            ModelSetEntry(provider="p1", model="dumb", price_input=0.1, price_output=0.1),
        ])

        result = router.select(ms)
        assert result is not None
        _, model, entry = result
        assert entry.model == "smart"

    def test_respects_complexity_minimum(self):
        prov = _provider("p1", models=[
            ModelConfig(name="Big", slug="big", smart_weight=100),
            ModelConfig(name="Small", slug="small", smart_weight=10),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="p1", model="big", price_input=10.0, price_output=10.0,
                         complexity_min="high"),
            ModelSetEntry(provider="p1", model="small", price_input=0.5, price_output=0.5,
                         complexity_min="low"),
        ])

        result = router.select(ms, complexity="low")
        assert result is not None
        _, _, entry = result
        assert entry.model == "small"

    def test_budget_filters_expensive_models(self):
        prov = _provider("p1", models=[
            ModelConfig(name="Expensive", slug="expensive", smart_weight=100),
            ModelConfig(name="Cheap", slug="cheap", smart_weight=10),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="p1", model="expensive", price_input=50.0, price_output=50.0),
            ModelSetEntry(provider="p1", model="cheap", price_input=0.01, price_output=0.01),
        ])

        result = router.select(ms, remaining_budget=0.001)
        assert result is not None
        _, _, entry = result
        assert entry.model == "cheap"

    def test_returns_none_when_no_candidates(self):
        prov = _provider("p1", models=[
            ModelConfig(name="M", slug="m", smart_weight=10),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="p1", model="m", price_input=100.0, price_output=100.0),
        ])

        result = router.select(ms, remaining_budget=0.0001)
        assert result is None

    def test_returns_none_for_empty_model_set(self):
        router = ModelRouter([_provider()])
        ms = _model_set([])
        assert router.select(ms) is None

    def test_unresolvable_provider_skipped(self):
        prov = _provider("real", models=[
            ModelConfig(name="M", slug="m", smart_weight=10),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="ghost", model="x", price_input=0, price_output=0),
            ModelSetEntry(provider="real", model="m", price_input=0, price_output=0),
        ])

        result = router.select(ms)
        assert result is not None
        _, _, entry = result
        assert entry.provider == "real"

    def test_medium_complexity_excludes_high_min(self):
        prov = _provider("p", models=[
            ModelConfig(name="A", slug="a", smart_weight=50),
            ModelConfig(name="B", slug="b", smart_weight=40),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="p", model="a", price_input=1.0, price_output=1.0,
                         complexity_min="high"),
            ModelSetEntry(provider="p", model="b", price_input=1.0, price_output=1.0,
                         complexity_min="low"),
        ])

        result = router.select(ms, complexity="medium")
        assert result is not None
        _, _, entry = result
        assert entry.model == "b"

    def test_high_complexity_allows_all(self):
        prov = _provider("p", models=[
            ModelConfig(name="A", slug="a", smart_weight=100),
            ModelConfig(name="B", slug="b", smart_weight=10),
        ])
        router = ModelRouter([prov])
        ms = _model_set([
            ModelSetEntry(provider="p", model="a", price_input=1.0, price_output=1.0,
                         complexity_min="high"),
            ModelSetEntry(provider="p", model="b", price_input=0.1, price_output=0.1,
                         complexity_min="low"),
        ])

        result = router.select(ms, complexity="high")
        assert result is not None
        _, _, entry = result
        assert entry.model == "a"
