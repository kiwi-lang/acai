"""Unit tests for acai/tools/skills.py — skill management tool functions."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import acai.tools.skills as skills_mod
from acai.tools.skills import (
    _configure,
    _get_store,
    create_skill,
    get_skill,
    list_skills,
    update_skill_code,
    update_skill_definition,
    update_skill_readme,
)


@pytest.fixture(autouse=True)
def _reset_store():
    """Ensure global _store is reset between tests."""
    old = skills_mod._store
    yield
    skills_mod._store = old


@pytest.fixture()
def mock_store():
    """Provide a mock SkillStore bound via _configure."""
    store = MagicMock()
    _configure(store)
    return store


def _skill(namespace="data", name="parser", description="Parse things"):
    s = MagicMock()
    s.namespace = namespace
    s.name = name
    s.description = description
    return s


# ------------------------------------------------------------------
# _configure / _get_store
# ------------------------------------------------------------------


class TestConfigure:
    def test_sets_store(self):
        store = MagicMock()
        _configure(store)
        assert skills_mod._store is store

    def test_get_store_returns_configured(self):
        store = MagicMock()
        _configure(store)
        assert _get_store() is store

    def test_get_store_raises_when_none(self):
        skills_mod._store = None
        with pytest.raises(RuntimeError, match="skill store not configured"):
            _get_store()


# ------------------------------------------------------------------
# list_skills
# ------------------------------------------------------------------


class TestListSkills:
    def test_empty(self, mock_store):
        mock_store.all_skills.return_value = []
        result = json.loads(list_skills())
        assert result == {"skills": [], "count": 0}

    def test_returns_all(self, mock_store):
        mock_store.all_skills.return_value = [
            _skill("ns1", "tool_a", "desc a"),
            _skill("ns2", "tool_b", "desc b"),
        ]
        result = json.loads(list_skills())
        assert result["count"] == 2
        assert result["skills"][0]["qualified_name"] == "skills.ns1.tool_a"
        assert result["skills"][1]["namespace"] == "ns2"

    def test_filter_by_namespace(self, mock_store):
        mock_store.all_skills.return_value = [
            _skill("alpha", "a", "d"),
            _skill("beta", "b", "d"),
            _skill("alpha", "c", "d"),
        ]
        result = json.loads(list_skills(namespace="alpha"))
        assert result["count"] == 2
        names = [s["name"] for s in result["skills"]]
        assert "a" in names and "c" in names

    def test_description_truncated_at_500(self, mock_store):
        long_desc = "x" * 1000
        mock_store.all_skills.return_value = [_skill("ns", "t", long_desc)]
        result = json.loads(list_skills())
        assert len(result["skills"][0]["description"]) == 500

    def test_none_description(self, mock_store):
        s = _skill("ns", "t", None)
        s.description = None
        mock_store.all_skills.return_value = [s]
        result = json.loads(list_skills())
        assert result["skills"][0]["description"] == ""


# ------------------------------------------------------------------
# get_skill
# ------------------------------------------------------------------


class TestGetSkill:
    def test_not_found(self, mock_store):
        mock_store.read_file.return_value = None
        result = json.loads(get_skill("ns", "missing"))
        assert "error" in result
        assert "not found" in result["error"]

    def test_success(self, mock_store):
        tool_json = json.dumps({"description": "do stuff", "parameters": {}})
        mock_store.read_file.side_effect = lambda ns, name, f: {
            "tool.json": tool_json,
            "run.py": "print('hi')",
            "README.md": "# Hello",
            "requirements.txt": "numpy",
        }.get(f)

        result = json.loads(get_skill("ns", "mytool"))
        assert result["qualified_name"] == "skills.ns.mytool"
        assert result["definition"]["description"] == "do stuff"
        assert result["code"] == "print('hi')"
        assert result["readme"] == "# Hello"
        assert result["requirements"] == "numpy"

    def test_malformed_tool_json(self, mock_store):
        mock_store.read_file.side_effect = lambda ns, name, f: {
            "tool.json": "not valid json{{{",
            "run.py": None,
            "README.md": None,
            "requirements.txt": None,
        }.get(f)

        result = json.loads(get_skill("ns", "broken"))
        assert result["definition"]["raw"] == "not valid json{{{"

    def test_optional_files_missing(self, mock_store):
        mock_store.read_file.side_effect = lambda ns, name, f: {
            "tool.json": "{}",
            "run.py": None,
            "README.md": None,
            "requirements.txt": None,
        }.get(f)

        result = json.loads(get_skill("ns", "minimal"))
        assert result["code"] == ""
        assert result["readme"] == ""
        assert result["requirements"] == ""


# ------------------------------------------------------------------
# create_skill
# ------------------------------------------------------------------


class TestCreateSkill:
    def test_success_minimal(self, mock_store):
        mock_store.scaffold.return_value = "/skills/ns/tool"
        with patch("acai.tools.skills._auto_register"):
            result = json.loads(create_skill("ns", "tool", "A tool"))
        assert result["created"] is True
        assert result["path"] == "/skills/ns/tool"
        assert result["qualified_name"] == "skills.ns.tool"

    def test_success_with_parameters(self, mock_store):
        mock_store.scaffold.return_value = "/p"
        params = json.dumps({"properties": {"x": {"type": "string"}}})
        with patch("acai.tools.skills._auto_register"):
            result = json.loads(create_skill("ns", "t", "d", parameters=params))
        assert result["created"] is True
        call_kwargs = mock_store.scaffold.call_args[1]
        assert call_kwargs["parameters"] == {"properties": {"x": {"type": "string"}}}

    def test_invalid_parameters_json(self, mock_store):
        result = json.loads(create_skill("ns", "t", "d", parameters="not json"))
        assert "error" in result
        assert "invalid parameters JSON" in result["error"]
        mock_store.scaffold.assert_not_called()

    def test_passes_all_args_to_scaffold(self, mock_store):
        mock_store.scaffold.return_value = "/p"
        with patch("acai.tools.skills._auto_register"):
            create_skill(
                "data", "csv", "Parse CSV",
                parameters='{"properties":{}}',
                code="import csv",
                readme="# CSV",
                requirements="pandas",
            )
        mock_store.scaffold.assert_called_once_with(
            namespace="data",
            name="csv",
            description="Parse CSV",
            parameters={"properties": {}},
            code="import csv",
            readme="# CSV",
            requirements="pandas",
        )

    def test_auto_register_called(self, mock_store):
        mock_store.scaffold.return_value = "/p"
        with patch("acai.tools.skills._auto_register") as mock_reg:
            create_skill("ns", "t", "d")
            mock_reg.assert_called_once_with(mock_store, "ns", "t")


# ------------------------------------------------------------------
# update_skill_code
# ------------------------------------------------------------------


class TestUpdateSkillCode:
    def test_not_found(self, mock_store):
        mock_store.read_file.return_value = None
        result = json.loads(update_skill_code("ns", "missing", "code"))
        assert "error" in result
        assert "not found" in result["error"]
        mock_store.write_file.assert_not_called()

    def test_success(self, mock_store):
        mock_store.read_file.return_value = "{}"
        mock_store.write_file.return_value = "/skills/ns/t/run.py"
        result = json.loads(update_skill_code("ns", "t", "new code"))
        assert result["updated"] is True
        assert result["path"] == "/skills/ns/t/run.py"
        mock_store.write_file.assert_called_once_with("ns", "t", "run.py", "new code")


# ------------------------------------------------------------------
# update_skill_definition
# ------------------------------------------------------------------


class TestUpdateSkillDefinition:
    def test_not_found(self, mock_store):
        mock_store.read_file.return_value = None
        result = json.loads(update_skill_definition("ns", "missing"))
        assert "error" in result
        assert "not found" in result["error"]

    def test_update_description_only(self, mock_store):
        mock_store.read_file.return_value = json.dumps({"description": "old"})
        with patch("acai.tools.skills._auto_register"):
            result = json.loads(
                update_skill_definition("ns", "t", description="new desc")
            )
        assert result["updated"] is True
        written = json.loads(mock_store.write_file.call_args[0][3])
        assert written["description"] == "new desc"

    def test_update_parameters_only(self, mock_store):
        mock_store.read_file.return_value = json.dumps({"description": "keep"})
        params = json.dumps({"properties": {"x": {"type": "int"}}})
        with patch("acai.tools.skills._auto_register"):
            result = json.loads(
                update_skill_definition("ns", "t", parameters=params)
            )
        assert result["updated"] is True
        written = json.loads(mock_store.write_file.call_args[0][3])
        assert written["description"] == "keep"
        assert written["parameters"] == {"properties": {"x": {"type": "int"}}}

    def test_invalid_parameters_json(self, mock_store):
        mock_store.read_file.return_value = "{}"
        result = json.loads(
            update_skill_definition("ns", "t", parameters="{{bad")
        )
        assert "error" in result
        assert "invalid parameters JSON" in result["error"]
        mock_store.write_file.assert_not_called()

    def test_malformed_existing_tool_json(self, mock_store):
        mock_store.read_file.return_value = "not json!!!"
        with patch("acai.tools.skills._auto_register"):
            result = json.loads(
                update_skill_definition("ns", "t", description="fresh")
            )
        assert result["updated"] is True
        written = json.loads(mock_store.write_file.call_args[0][3])
        assert written["description"] == "fresh"

    def test_auto_register_called(self, mock_store):
        mock_store.read_file.return_value = "{}"
        with patch("acai.tools.skills._auto_register") as mock_reg:
            update_skill_definition("ns", "t", description="x")
            mock_reg.assert_called_once_with(mock_store, "ns", "t")


# ------------------------------------------------------------------
# update_skill_readme
# ------------------------------------------------------------------


class TestUpdateSkillReadme:
    def test_not_found(self, mock_store):
        mock_store.read_file.return_value = None
        result = json.loads(update_skill_readme("ns", "missing", "readme"))
        assert "error" in result
        assert "not found" in result["error"]
        mock_store.write_file.assert_not_called()

    def test_success(self, mock_store):
        mock_store.read_file.return_value = "{}"
        mock_store.write_file.return_value = "/skills/ns/t/README.md"
        result = json.loads(update_skill_readme("ns", "t", "# New readme"))
        assert result["updated"] is True
        assert result["path"] == "/skills/ns/t/README.md"
        mock_store.write_file.assert_called_once_with(
            "ns", "t", "README.md", "# New readme"
        )


# ------------------------------------------------------------------
# _auto_register
# ------------------------------------------------------------------


class TestAutoRegister:
    def test_calls_register_all_when_registry_available(self, mock_store):
        mock_registry = MagicMock()
        with patch("acai.tools.skills._auto_register.__module__", "acai.tools.skills"):
            with patch("acai.tools.meta._registry", mock_registry):
                from acai.tools.skills import _auto_register
                _auto_register(mock_store, "ns", "t")
                mock_store.register_all.assert_called_once_with(mock_registry)

    def test_no_error_when_import_fails(self, mock_store):
        with patch.dict("sys.modules", {"acai.tools.meta": None}):
            from acai.tools.skills import _auto_register
            _auto_register(mock_store, "ns", "t")

    def test_no_error_when_registry_is_none(self, mock_store):
        with patch("acai.tools.meta._registry", None):
            from acai.tools.skills import _auto_register
            _auto_register(mock_store, "ns", "t")
