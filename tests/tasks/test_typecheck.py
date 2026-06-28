"""Tests for acai.tasks.typecheck — workflow graph type-checker."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from acai.tasks.nodes import Pin
from acai.tasks.typecheck import (
    _all_pins,
    _compatible,
    _find_pin,
    _node_label,
    _pin_label,
    _pin_name,
    _resolve_pin_type,
    typecheck,
)


# ── helpers ──────────────────────────────────────────────────────


def _mk_node(nid: str, ntype: str = "agent_call", **data_kw: Any) -> dict:
    node: dict = {"id": nid, "type": ntype}
    if data_kw:
        node["data"] = data_kw
    return node


def _mk_edge(
    eid: str,
    src: str,
    tgt: str,
    etype: str = "data",
    src_handle: str = "",
    tgt_handle: str = "",
) -> dict:
    return {
        "id": eid,
        "source": src,
        "target": tgt,
        "type": etype,
        "sourceHandle": src_handle,
        "targetHandle": tgt_handle,
    }


@dataclass
class _FakeNodeType:
    """Minimal stand-in for a registered NodeType."""

    pins: list[Pin]

    def dynamic_pins(
        self, data: dict, spec: dict | None = None, **ctx: Any
    ) -> list[Pin]:
        return []


@dataclass
class _FakeNodeTypeDynamic:
    """Fake NodeType that returns dynamic pins."""

    pins: list[Pin]
    _dynamic: list[Pin]

    def dynamic_pins(
        self, data: dict, spec: dict | None = None, **ctx: Any
    ) -> list[Pin]:
        return list(self._dynamic)


# ── _pin_name ────────────────────────────────────────────────────


class TestPinName:
    def test_strips_data_prefix(self):
        assert _pin_name("data_foo") == "foo"

    def test_preserves_non_data_prefix(self):
        assert _pin_name("exec_in") == "exec_in"

    def test_empty_string(self):
        assert _pin_name("") == ""

    def test_data_only_prefix(self):
        assert _pin_name("data_") == ""

    def test_no_prefix(self):
        assert _pin_name("something") == "something"


# ── _node_label ──────────────────────────────────────────────────


class TestNodeLabel:
    def test_from_data_label(self):
        node = {"id": "n1", "data": {"label": "My Node"}}
        assert _node_label(node) == "My Node"

    def test_fallback_to_id(self):
        node = {"id": "n1"}
        assert _node_label(node) == "n1"

    def test_fallback_when_data_is_none(self):
        node = {"id": "n1", "data": None}
        assert _node_label(node) == "n1"

    def test_fallback_when_no_id(self):
        node: dict = {}
        assert _node_label(node) == "?"

    def test_empty_data(self):
        node = {"id": "n1", "data": {}}
        assert _node_label(node) == "n1"


# ── _compatible ──────────────────────────────────────────────────


class TestCompatible:
    def test_same_types(self):
        assert _compatible("string", "string") is True

    def test_different_types(self):
        assert _compatible("string", "int") is False

    def test_src_any(self):
        assert _compatible("any", "int") is True

    def test_tgt_any(self):
        assert _compatible("string", "any") is True

    def test_both_any(self):
        assert _compatible("any", "any") is True


# ── _all_pins ────────────────────────────────────────────────────


class TestAllPins:
    def test_static_only(self):
        pin_a = Pin("a", "A", "#fff", "left", "data")
        nt = _FakeNodeType(pins=[pin_a])
        node = {"id": "n1", "data": {}}
        result = _all_pins(nt, node, {})
        assert result == [pin_a]

    def test_static_plus_dynamic(self):
        pin_a = Pin("a", "A", "#fff", "left", "data")
        pin_b = Pin("b", "B", "#fff", "right", "data")
        nt = _FakeNodeTypeDynamic(pins=[pin_a], _dynamic=[pin_b])
        node = {"id": "n1", "data": {}}
        result = _all_pins(nt, node, {})
        assert result == [pin_a, pin_b]

    def test_no_data_key(self):
        nt = _FakeNodeType(pins=[])
        node = {"id": "n1"}
        result = _all_pins(nt, node, {})
        assert result == []

    def test_data_none(self):
        nt = _FakeNodeType(pins=[])
        node = {"id": "n1", "data": None}
        result = _all_pins(nt, node, {})
        assert result == []


# ── _find_pin ────────────────────────────────────────────────────


class TestFindPin:
    def test_finds_static_pin(self):
        pin_a = Pin("a", "A", "#fff", "left", "data")
        nt = _FakeNodeType(pins=[pin_a])
        node = {"id": "n1", "data": {}}
        assert _find_pin(nt, "a", node, {}) is pin_a

    def test_finds_dynamic_pin(self):
        pin_d = Pin("dyn", "Dynamic", "#fff", "right", "data")
        nt = _FakeNodeTypeDynamic(pins=[], _dynamic=[pin_d])
        node = {"id": "n1", "data": {}}
        assert _find_pin(nt, "dyn", node, {}) is pin_d

    def test_returns_none_when_not_found(self):
        nt = _FakeNodeType(pins=[])
        node = {"id": "n1", "data": {}}
        assert _find_pin(nt, "nope", node, {}) is None

    def test_static_takes_priority(self):
        pin_s = Pin("x", "Static", "#fff", "left", "data")
        pin_d = Pin("x", "Dynamic", "#fff", "right", "data")
        nt = _FakeNodeTypeDynamic(pins=[pin_s], _dynamic=[pin_d])
        node = {"id": "n1", "data": {}}
        assert _find_pin(nt, "x", node, {}) is pin_s

    def test_no_data_key(self):
        nt = _FakeNodeType(pins=[])
        node = {"id": "n1"}
        assert _find_pin(nt, "missing", node, {}) is None


# ── _resolve_pin_type ────────────────────────────────────────────


class TestResolvePinType:
    def test_unknown_node_returns_any(self):
        with patch("acai.tasks.typecheck.get_node_type", return_value=None):
            assert _resolve_pin_type("bad_type", "h", {}, {}) == "any"

    def test_known_node_known_pin(self):
        pin = Pin("h", "H", "#fff", "left", "data", pin_type="int")
        nt = _FakeNodeType(pins=[pin])
        with patch("acai.tasks.typecheck.get_node_type", return_value=nt):
            assert _resolve_pin_type("my", "h", {"id": "n1"}, {}) == "int"

    def test_known_node_unknown_pin_returns_any(self):
        nt = _FakeNodeType(pins=[])
        with patch("acai.tasks.typecheck.get_node_type", return_value=nt):
            assert _resolve_pin_type("my", "nope", {"id": "n1"}, {}) == "any"


# ── _pin_label ───────────────────────────────────────────────────


class TestPinLabel:
    def test_found_pin_with_label(self):
        pin = Pin("data_foo", "Foo Label", "#fff", "left", "data")
        nt = _FakeNodeType(pins=[pin])
        with patch("acai.tasks.typecheck.get_node_type", return_value=nt):
            assert _pin_label("my", "data_foo", {"id": "n1"}, {}) == "Foo Label"

    def test_found_pin_without_label(self):
        pin = Pin("data_bar", "", "#fff", "left", "data")
        nt = _FakeNodeType(pins=[pin])
        with patch("acai.tasks.typecheck.get_node_type", return_value=nt):
            assert _pin_label("my", "data_bar", {"id": "n1"}, {}) == "bar"

    def test_unknown_node_type(self):
        with patch("acai.tasks.typecheck.get_node_type", return_value=None):
            assert _pin_label("bad", "data_x", {}, {}) == "x"

    def test_pin_not_found(self):
        nt = _FakeNodeType(pins=[])
        with patch("acai.tasks.typecheck.get_node_type", return_value=nt):
            assert _pin_label("my", "data_y", {"id": "n1"}, {}) == "y"


# ── typecheck — integration-style tests using the real registry ──


class TestTypecheckUnknownNodeType:
    def test_unknown_node_type(self):
        spec = {"nodes": [_mk_node("n1", "totally_bogus")], "edges": []}
        diags = typecheck(spec)
        assert any(d["code"] == "unknown_node_type" for d in diags)
        d = next(d for d in diags if d["code"] == "unknown_node_type")
        assert d["node_id"] == "n1"
        assert d["severity"] == "error"
        assert "totally_bogus" in d["message"]

    def test_known_node_type_no_error(self):
        spec = {"nodes": [_mk_node("n1", "start")], "edges": []}
        diags = typecheck(spec)
        assert not any(d["code"] == "unknown_node_type" for d in diags)


class TestTypecheckDanglingEdge:
    def test_source_missing(self):
        spec = {
            "nodes": [_mk_node("n1", "start")],
            "edges": [_mk_edge("e1", "phantom", "n1")],
        }
        diags = typecheck(spec)
        dangling = [d for d in diags if d["code"] == "dangling_edge"]
        assert len(dangling) == 1
        assert dangling[0]["node_id"] == "phantom"
        assert dangling[0]["severity"] == "error"

    def test_target_missing(self):
        spec = {
            "nodes": [_mk_node("n1", "start")],
            "edges": [_mk_edge("e1", "n1", "phantom")],
        }
        diags = typecheck(spec)
        dangling = [d for d in diags if d["code"] == "dangling_edge"]
        assert len(dangling) == 1
        assert dangling[0]["node_id"] == "phantom"

    def test_both_missing(self):
        spec = {
            "nodes": [],
            "edges": [_mk_edge("e1", "a", "b")],
        }
        diags = typecheck(spec)
        dangling = [d for d in diags if d["code"] == "dangling_edge"]
        assert len(dangling) == 1
        assert dangling[0]["node_id"] == "a"

    def test_no_dangling_when_both_exist(self):
        spec = {
            "nodes": [_mk_node("a", "start"), _mk_node("b", "start")],
            "edges": [_mk_edge("e1", "a", "b")],
        }
        diags = typecheck(spec)
        assert not any(d["code"] == "dangling_edge" for d in diags)


class TestTypecheckTypeMismatch:
    def test_incompatible_data_edge(self):
        pin_out = Pin("out", "Out", "#fff", "right", "data", pin_type="int")
        pin_in = Pin("in", "In", "#fff", "left", "data", pin_type="string")
        nt_src = _FakeNodeType(pins=[pin_out])
        nt_tgt = _FakeNodeType(pins=[pin_in])

        spec = {
            "nodes": [
                _mk_node("s", "src_type"),
                _mk_node("t", "tgt_type"),
            ],
            "edges": [
                _mk_edge("e1", "s", "t", "data", "out", "in"),
            ],
        }

        def fake_get(name):
            return {"src_type": nt_src, "tgt_type": nt_tgt}.get(name)

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        mismatches = [d for d in diags if d["code"] == "type_mismatch"]
        assert len(mismatches) == 1
        d = mismatches[0]
        assert d["severity"] == "error"
        assert d["source_type"] == "int"
        assert d["target_type"] == "string"
        assert d["source_node"] == "s"
        assert d["target_node"] == "t"

    def test_compatible_any_skips(self):
        pin_out = Pin("out", "Out", "#fff", "right", "data", pin_type="any")
        pin_in = Pin("in", "In", "#fff", "left", "data", pin_type="string")
        nt_src = _FakeNodeType(pins=[pin_out])
        nt_tgt = _FakeNodeType(pins=[pin_in])

        spec = {
            "nodes": [_mk_node("s", "src_type"), _mk_node("t", "tgt_type")],
            "edges": [_mk_edge("e1", "s", "t", "data", "out", "in")],
        }

        def fake_get(name):
            return {"src_type": nt_src, "tgt_type": nt_tgt}.get(name)

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "type_mismatch" for d in diags)

    def test_same_type_ok(self):
        pin_out = Pin("out", "Out", "#fff", "right", "data", pin_type="json")
        pin_in = Pin("in", "In", "#fff", "left", "data", pin_type="json")
        nt_src = _FakeNodeType(pins=[pin_out])
        nt_tgt = _FakeNodeType(pins=[pin_in])

        spec = {
            "nodes": [_mk_node("s", "src_type"), _mk_node("t", "tgt_type")],
            "edges": [_mk_edge("e1", "s", "t", "data", "out", "in")],
        }

        def fake_get(name):
            return {"src_type": nt_src, "tgt_type": nt_tgt}.get(name)

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "type_mismatch" for d in diags)

    def test_skips_non_data_edges(self):
        spec = {
            "nodes": [_mk_node("s", "start"), _mk_node("t", "start")],
            "edges": [_mk_edge("e1", "s", "t", "exec", "exec_out", "exec_in")],
        }
        diags = typecheck(spec)
        assert not any(d["code"] == "type_mismatch" for d in diags)

    def test_skips_when_node_missing(self):
        spec = {
            "nodes": [_mk_node("s", "start")],
            "edges": [_mk_edge("e1", "s", "phantom", "data", "out", "in")],
        }
        diags = typecheck(spec)
        assert not any(d["code"] == "type_mismatch" for d in diags)


class TestTypecheckMissingInput:
    def test_required_input_not_connected(self):
        pin_in = Pin(
            "data_prompt", "prompt", "#fff", "left", "data",
            pin_type="string", optional=False,
        )
        nt = _FakeNodeType(pins=[Pin.exec_in(), pin_in])

        spec = {
            "nodes": [_mk_node("n1", "my_type")],
            "edges": [],
        }

        def fake_get(name):
            return nt if name == "my_type" else None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        missing = [d for d in diags if d["code"] == "missing_input"]
        assert len(missing) == 1
        assert missing[0]["node_id"] == "n1"
        assert missing[0]["target_pin"] == "prompt"
        assert missing[0]["severity"] == "error"

    def test_required_input_connected(self):
        pin_in = Pin(
            "data_prompt", "prompt", "#fff", "left", "data",
            pin_type="string", optional=False,
        )
        nt = _FakeNodeType(pins=[Pin.exec_in(), pin_in])

        spec = {
            "nodes": [
                _mk_node("s", "src"),
                _mk_node("n1", "my_type"),
            ],
            "edges": [
                _mk_edge("e1", "s", "n1", "data", "data_out", "data_prompt"),
            ],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "missing_input" for d in diags)

    def test_required_input_has_inline_value(self):
        pin_in = Pin(
            "data_prompt", "prompt", "#fff", "left", "data",
            pin_type="string", optional=False,
        )
        nt = _FakeNodeType(pins=[Pin.exec_in(), pin_in])

        spec = {
            "nodes": [_mk_node("n1", "my_type", prompt="Hello world")],
            "edges": [],
        }

        def fake_get(name):
            return nt if name == "my_type" else None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "missing_input" for d in diags)

    def test_optional_input_not_flagged(self):
        pin_in = Pin(
            "data_optional", "optional", "#fff", "left", "data",
            pin_type="string", optional=True,
        )
        nt = _FakeNodeType(pins=[pin_in])

        spec = {"nodes": [_mk_node("n1", "my_type")], "edges": []}

        def fake_get(name):
            return nt if name == "my_type" else None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "missing_input" for d in diags)

    def test_exec_and_right_side_pins_skipped(self):
        pin_exec = Pin("exec_in", "", "#fff", "left", "exec")
        pin_right = Pin(
            "data_out", "out", "#fff", "right", "data",
            pin_type="string", optional=False,
        )
        nt = _FakeNodeType(pins=[pin_exec, pin_right])

        spec = {"nodes": [_mk_node("n1", "my_type")], "edges": []}

        def fake_get(name):
            return nt if name == "my_type" else None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "missing_input" for d in diags)

    def test_required_input_with_falsy_inline_value(self):
        pin_in = Pin(
            "data_count", "count", "#fff", "left", "data",
            pin_type="string", optional=False,
        )
        nt = _FakeNodeType(pins=[pin_in])

        spec = {"nodes": [_mk_node("n1", "my_type", count="")], "edges": []}

        def fake_get(name):
            return nt if name == "my_type" else None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        missing = [d for d in diags if d["code"] == "missing_input"]
        assert len(missing) == 1


class TestTypecheckMissingExec:
    def test_node_with_exec_in_not_connected(self):
        pin_exec_in = Pin.exec_in()
        nt = _FakeNodeType(pins=[pin_exec_in])

        spec = {
            "nodes": [
                _mk_node("start1", "start"),
                _mk_node("n1", "my_type"),
            ],
            "edges": [],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        me = [d for d in diags if d["code"] == "missing_exec"]
        assert len(me) == 1
        assert me[0]["node_id"] == "n1"
        assert me[0]["severity"] == "warning"

    def test_exec_connected_no_warning(self):
        pin_exec_in = Pin.exec_in()
        nt = _FakeNodeType(pins=[pin_exec_in])

        spec = {
            "nodes": [
                _mk_node("start1", "start"),
                _mk_node("n1", "my_type"),
            ],
            "edges": [
                _mk_edge("e1", "start1", "n1", "exec", "exec_out", "exec_in"),
            ],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "missing_exec" for d in diags)

    def test_start_node_skipped(self):
        spec = {
            "nodes": [_mk_node("start1", "start")],
            "edges": [],
        }
        diags = typecheck(spec)
        assert not any(d["code"] == "missing_exec" for d in diags)

    def test_unknown_node_type_skipped(self):
        spec = {
            "nodes": [_mk_node("n1", "nonexistent_type_xyz")],
            "edges": [],
        }
        diags = typecheck(spec)
        assert not any(d["code"] == "missing_exec" for d in diags)


class TestTypecheckOrphanNode:
    def test_unreachable_node(self):
        pin_exec_in = Pin.exec_in()
        nt = _FakeNodeType(pins=[pin_exec_in])

        spec = {
            "nodes": [
                _mk_node("start1", "start"),
                _mk_node("orphan", "my_type"),
            ],
            "edges": [],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        orphans = [d for d in diags if d["code"] == "orphan_node"]
        assert len(orphans) == 1
        assert orphans[0]["node_id"] == "orphan"
        assert orphans[0]["severity"] == "warning"

    def test_reachable_node_not_orphan(self):
        pin_exec_in = Pin.exec_in()
        nt = _FakeNodeType(pins=[pin_exec_in])

        spec = {
            "nodes": [
                _mk_node("start1", "start"),
                _mk_node("n1", "my_type"),
            ],
            "edges": [
                _mk_edge("e1", "start1", "n1", "exec", "exec_out", "exec_in"),
            ],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "orphan_node" for d in diags)

    def test_transitive_reachability(self):
        pin_exec_in = Pin.exec_in()
        pin_exec_out = Pin.exec_out()
        nt = _FakeNodeType(pins=[pin_exec_in, pin_exec_out])

        spec = {
            "nodes": [
                _mk_node("start1", "start"),
                _mk_node("mid", "my_type"),
                _mk_node("end", "my_type"),
            ],
            "edges": [
                _mk_edge("e1", "start1", "mid", "exec", "exec_out", "exec_in"),
                _mk_edge("e2", "mid", "end", "exec", "exec_out", "exec_in"),
            ],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "orphan_node" for d in diags)

    def test_no_exec_in_pin_not_flagged(self):
        nt = _FakeNodeType(pins=[])

        spec = {
            "nodes": [
                _mk_node("start1", "start"),
                _mk_node("n1", "my_type"),
            ],
            "edges": [],
        }

        def fake_get(name):
            if name == "my_type":
                return nt
            return None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            diags = typecheck(spec)

        assert not any(d["code"] == "orphan_node" for d in diags)


class TestTypecheckMissingTemplate:
    def test_missing_template_warning(self):
        agent_store = MagicMock()
        agent_store.has_template.return_value = False

        spec = {
            "nodes": [_mk_node("n1", "agent_call", agent="my_agent")],
            "edges": [],
        }

        diags = typecheck(spec, agent_store=agent_store)
        tmpl = [d for d in diags if d["code"] == "missing_template"]
        assert len(tmpl) == 1
        assert tmpl[0]["node_id"] == "n1"
        assert tmpl[0]["severity"] == "warning"
        assert "my_agent" in tmpl[0]["message"]

    def test_template_exists_in_store(self):
        agent_store = MagicMock()
        agent_store.has_template.return_value = True

        spec = {
            "nodes": [_mk_node("n1", "agent_call", agent="my_agent")],
            "edges": [],
        }

        diags = typecheck(spec, agent_store=agent_store)
        assert not any(d["code"] == "missing_template" for d in diags)

    def test_template_exists_in_workflow_dir(self, tmp_path):
        agent_store = MagicMock()
        agent_store.has_template.return_value = False

        tpl_dir = tmp_path / "agents" / "local_agent"
        tpl_dir.mkdir(parents=True)
        (tpl_dir / "system.j2").write_text("template")

        spec = {
            "nodes": [_mk_node("n1", "agent_call", agent="local_agent")],
            "edges": [],
        }

        diags = typecheck(
            spec, agent_store=agent_store, workflow_dir=str(tmp_path)
        )
        assert not any(d["code"] == "missing_template" for d in diags)

    def test_no_agent_name_skipped(self):
        agent_store = MagicMock()
        spec = {
            "nodes": [_mk_node("n1", "agent_call", agent="")],
            "edges": [],
        }
        diags = typecheck(spec, agent_store=agent_store)
        assert not any(d["code"] == "missing_template" for d in diags)
        agent_store.has_template.assert_not_called()

    def test_no_agent_store_skips_check(self):
        spec = {
            "nodes": [_mk_node("n1", "agent_call", agent="my_agent")],
            "edges": [],
        }
        diags = typecheck(spec, agent_store=None)
        assert not any(d["code"] == "missing_template" for d in diags)

    def test_simple_agent_type_checked(self):
        agent_store = MagicMock()
        agent_store.has_template.return_value = False

        spec = {
            "nodes": [_mk_node("n1", "simple_agent", agent="sa")],
            "edges": [],
        }

        diags = typecheck(spec, agent_store=agent_store)
        tmpl = [d for d in diags if d["code"] == "missing_template"]
        assert len(tmpl) == 1

    def test_background_agent_type_checked(self):
        agent_store = MagicMock()
        agent_store.has_template.return_value = False

        spec = {
            "nodes": [_mk_node("n1", "background_agent", agent="ba")],
            "edges": [],
        }

        diags = typecheck(spec, agent_store=agent_store)
        tmpl = [d for d in diags if d["code"] == "missing_template"]
        assert len(tmpl) == 1

    def test_non_agent_node_type_not_checked(self):
        agent_store = MagicMock()
        spec = {
            "nodes": [_mk_node("n1", "start", agent="irrelevant")],
            "edges": [],
        }
        diags = typecheck(spec, agent_store=agent_store)
        assert not any(d["code"] == "missing_template" for d in diags)
        agent_store.has_template.assert_not_called()


class TestTypecheckToolDefs:
    def test_tool_defs_forwarded_to_dynamic_pins(self):
        received_ctx: dict = {}

        class _CapturingNodeType:
            pins: list[Pin] = []

            def dynamic_pins(self, data, spec=None, **ctx):
                received_ctx.update(ctx)
                return []

        nt = _CapturingNodeType()

        pin_in = Pin(
            "data_x", "x", "#fff", "left", "data",
            pin_type="string", optional=False,
        )
        nt.pins = [pin_in]

        spec = {
            "nodes": [_mk_node("n1", "cap")],
            "edges": [],
        }

        tool_defs = [{"name": "my_tool"}]

        def fake_get(name):
            return nt if name == "cap" else None

        with patch("acai.tasks.typecheck.get_node_type", side_effect=fake_get):
            typecheck(spec, tool_defs=tool_defs)

        assert received_ctx.get("tool_defs") == tool_defs


class TestTypecheckEmptySpec:
    def test_empty_spec(self):
        diags = typecheck({"nodes": [], "edges": []})
        assert diags == []

    def test_no_nodes_key(self):
        diags = typecheck({})
        assert diags == []

    def test_no_edges_key(self):
        diags = typecheck({"nodes": [_mk_node("n1", "start")]})
        assert diags == []


class TestTypecheckMultipleDiagnostics:
    def test_multiple_issues(self):
        spec = {
            "nodes": [
                _mk_node("n1", "unknown_type_xyz"),
            ],
            "edges": [
                _mk_edge("e1", "n1", "ghost"),
            ],
        }
        diags = typecheck(spec)
        codes = {d["code"] for d in diags}
        assert "unknown_node_type" in codes
        assert "dangling_edge" in codes
