"""Tests for acai.provider.scheduler — ProviderScheduler."""

from __future__ import annotations

import pytest

from acai.provider.config import ProviderConfig
from acai.provider.scheduler import ProviderScheduler


def _prov(name: str, priority: int = 0) -> ProviderConfig:
    return ProviderConfig(name=name, backend="vllm", endpoint="http://x", priority=priority)


class TestProviderScheduler:
    def test_default_empty(self):
        s = ProviderScheduler([])
        assert s.default() is None

    def test_default_single(self):
        p = _prov("only", priority=5)
        s = ProviderScheduler([p])
        assert s.default() == p

    def test_default_picks_highest_priority(self):
        low = _prov("low", priority=1)
        high = _prov("high", priority=10)
        mid = _prov("mid", priority=5)
        s = ProviderScheduler([low, high, mid])
        assert s.default() == high

    def test_select_delegates_to_default(self):
        p = _prov("a", priority=1)
        s = ProviderScheduler([p])
        assert s.select("any-role") == s.default()

    def test_all_for_role_sorted(self):
        a = _prov("a", priority=3)
        b = _prov("b", priority=7)
        c = _prov("c", priority=1)
        s = ProviderScheduler([a, b, c])
        result = s.all_for_role()
        assert [p.name for p in result] == ["b", "a", "c"]

    def test_reload(self):
        old = _prov("old", priority=1)
        s = ProviderScheduler([old])
        assert s.default().name == "old"
        new = _prov("new", priority=5)
        s.reload([new])
        assert s.default().name == "new"
