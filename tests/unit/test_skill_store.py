"""Tests for the skill store — discovery, registration, execution, and scaffolding."""

from __future__ import annotations

import json
import os
import textwrap

import pytest

from acai.orchestrator.skill_store import SkillStore, execute_skill
from acai.orchestrator.tools import ToolRegistry


@pytest.fixture
def skills_dir(tmp_path):
    """Create a temporary skills directory with a sample skill."""
    ns_dir = tmp_path / "math" / "add_numbers"
    ns_dir.mkdir(parents=True)

    tool_def = {
        "name": "add_numbers",
        "description": "Add two numbers together.",
        "parameters": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number"},
                "b": {"type": "number", "description": "Second number"},
            },
            "required": ["a", "b"],
        },
    }
    (ns_dir / "tool.json").write_text(json.dumps(tool_def, indent=2))

    run_py = textwrap.dedent("""\
        import json, sys
        args = json.load(sys.stdin)
        result = {"sum": args["a"] + args["b"]}
        json.dump(result, sys.stdout)
    """)
    (ns_dir / "run.py").write_text(run_py)
    (ns_dir / "README.md").write_text("# add_numbers\n\nAdds two numbers.\n")

    return tmp_path


@pytest.fixture
def empty_skills_dir(tmp_path):
    return tmp_path / "empty_skills"


class TestSkillStoreDiscovery:
    def test_discover_finds_valid_skill(self, skills_dir):
        store = SkillStore(str(skills_dir))
        skills = store.discover()

        assert len(skills) == 1
        sd = skills[0]
        assert sd.namespace == "math"
        assert sd.name == "add_numbers"
        assert sd.description == "Add two numbers together."
        assert "a" in sd.parameters
        assert "b" in sd.parameters
        assert sd.required == ["a", "b"]

    def test_discover_empty_dir(self, empty_skills_dir):
        store = SkillStore(str(empty_skills_dir))
        skills = store.discover()
        assert skills == []

    def test_discover_skips_hidden_dirs(self, skills_dir):
        hidden = skills_dir / ".hidden" / "secret"
        hidden.mkdir(parents=True)
        (hidden / "tool.json").write_text('{"name":"x","description":"x","parameters":{}}')
        (hidden / "run.py").write_text("pass")

        store = SkillStore(str(skills_dir))
        skills = store.discover()
        assert len(skills) == 1

    def test_discover_skips_incomplete_skill(self, skills_dir):
        incomplete = skills_dir / "math" / "broken"
        incomplete.mkdir(parents=True)
        (incomplete / "tool.json").write_text('{"name":"broken"}')

        store = SkillStore(str(skills_dir))
        skills = store.discover()
        assert len(skills) == 1

    def test_discover_skips_invalid_json(self, skills_dir):
        bad = skills_dir / "math" / "bad_json"
        bad.mkdir(parents=True)
        (bad / "tool.json").write_text("{invalid json!")
        (bad / "run.py").write_text("pass")

        store = SkillStore(str(skills_dir))
        skills = store.discover()
        assert len(skills) == 1


class TestSkillStoreRegistration:
    def test_register_all(self, skills_dir):
        store = SkillStore(str(skills_dir))
        registry = ToolRegistry()
        count = store.register_all(registry)

        assert count == 1
        assert "skills.math" in registry.namespaces()

        td = registry.get("skills.math_add_numbers")
        assert td is not None
        assert td.description == "Add two numbers together."
        assert td.sandbox is True
        assert "execute" in td.permissions

    def test_registered_skill_shows_in_mcp_definitions(self, skills_dir):
        store = SkillStore(str(skills_dir))
        registry = ToolRegistry()
        store.register_all(registry)

        defs = registry.mcp_definitions(namespaces=["skills"])
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "skills.math_add_numbers"

    def test_prefix_matching_includes_skill_namespaces(self, skills_dir):
        store = SkillStore(str(skills_dir))
        registry = ToolRegistry()
        store.register_all(registry)

        defs_prefix = registry.mcp_definitions(namespaces=["skills"])
        defs_exact = registry.mcp_definitions(namespaces=["skills.math"])
        assert len(defs_prefix) == len(defs_exact) == 1

    def test_unrelated_namespace_excludes_skills(self, skills_dir):
        store = SkillStore(str(skills_dir))
        registry = ToolRegistry()
        store.register_all(registry)

        defs = registry.mcp_definitions(namespaces=["filesystem"])
        assert len(defs) == 0


class TestSkillExecution:
    def test_execute_skill_success(self, skills_dir):
        run_py = str(skills_dir / "math" / "add_numbers" / "run.py")
        cwd = str(skills_dir / "math" / "add_numbers")

        result = execute_skill(run_py, {"a": 3, "b": 7}, cwd)
        parsed = json.loads(result)
        assert parsed == {"sum": 10}

    def test_execute_skill_nonzero_exit(self, tmp_path):
        fail_py = tmp_path / "fail.py"
        fail_py.write_text("import sys; sys.exit(1)")

        result = execute_skill(str(fail_py), {}, str(tmp_path))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "code 1" in parsed["error"]

    def test_execute_skill_timeout(self, tmp_path):
        slow_py = tmp_path / "slow.py"
        slow_py.write_text("import time; time.sleep(60)")

        result = execute_skill(str(slow_py), {}, str(tmp_path), timeout=1)
        parsed = json.loads(result)
        assert "timed out" in parsed["error"]

    def test_execute_skill_non_json_output(self, tmp_path):
        text_py = tmp_path / "text.py"
        text_py.write_text("print('hello world')")

        result = execute_skill(str(text_py), {}, str(tmp_path))
        parsed = json.loads(result)
        assert parsed["output"] == "hello world"

    def test_registered_skill_callable(self, skills_dir):
        store = SkillStore(str(skills_dir))
        registry = ToolRegistry()
        store.register_all(registry)

        result = registry.call("skills.math_add_numbers", {"a": 10, "b": 5})
        parsed = json.loads(result)
        assert parsed == {"sum": 15}


class TestSkillScaffold:
    def test_scaffold_creates_files(self, tmp_path):
        store = SkillStore(str(tmp_path))
        path = store.scaffold(
            namespace="utils",
            name="formatter",
            description="Format text nicely.",
        )

        assert os.path.isdir(path)
        assert os.path.isfile(os.path.join(path, "tool.json"))
        assert os.path.isfile(os.path.join(path, "run.py"))
        assert os.path.isfile(os.path.join(path, "README.md"))

        with open(os.path.join(path, "tool.json")) as f:
            defn = json.load(f)
        assert defn["name"] == "formatter"
        assert defn["description"] == "Format text nicely."

    def test_scaffold_with_custom_code(self, tmp_path):
        store = SkillStore(str(tmp_path))
        code = "import json, sys\nprint(json.dumps({'ok': True}))"
        path = store.scaffold(
            namespace="test",
            name="custom",
            description="Custom skill.",
            code=code,
        )

        with open(os.path.join(path, "run.py")) as f:
            assert f.read() == code

    def test_scaffold_with_parameters(self, tmp_path):
        store = SkillStore(str(tmp_path))
        params = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        path = store.scaffold(
            namespace="search",
            name="web",
            description="Search the web.",
            parameters=params,
        )

        with open(os.path.join(path, "tool.json")) as f:
            defn = json.load(f)
        assert defn["parameters"]["properties"]["query"]["type"] == "string"

    def test_scaffolded_skill_is_discoverable(self, tmp_path):
        store = SkillStore(str(tmp_path))
        store.scaffold(
            namespace="demo",
            name="echo",
            description="Echo input back.",
        )

        skills = store.discover()
        assert len(skills) == 1
        assert skills[0].namespace == "demo"
        assert skills[0].name == "echo"


class TestSkillFileOps:
    def test_read_and_write_file(self, skills_dir):
        store = SkillStore(str(skills_dir))

        readme = store.read_file("math", "add_numbers", "README.md")
        assert readme is not None
        assert "add_numbers" in readme

        store.write_file("math", "add_numbers", "README.md", "# Updated\n")
        updated = store.read_file("math", "add_numbers", "README.md")
        assert updated == "# Updated\n"

    def test_read_nonexistent_file(self, skills_dir):
        store = SkillStore(str(skills_dir))
        result = store.read_file("math", "add_numbers", "nonexistent.txt")
        assert result is None


class TestSkillStoreScoped:
    """Verify scoped() temporarily adds and removes skill directories."""

    def _make_extra_skill(self, tmp_path, ns="extra", name="greet"):
        d = tmp_path / ns / name
        d.mkdir(parents=True)
        (d / "tool.json").write_text(json.dumps({
            "name": name,
            "description": f"A {name} skill.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }))
        (d / "run.py").write_text("import json, sys\njson.dump({'ok': True}, sys.stdout)\n")
        return str(tmp_path)

    def test_scoped_adds_and_removes(self, skills_dir, tmp_path):
        extra = self._make_extra_skill(tmp_path / "wf_skills")
        store = SkillStore(str(skills_dir))
        store.discover()

        assert store.get("skills.extra.greet") is None

        with store.scoped(extra):
            sd = store.get("skills.extra.greet")
            assert sd is not None
            assert sd.name == "greet"
            assert any(s.name == "greet" for s in store.all_skills())

        assert store.get("skills.extra.greet") is None
        assert not any(s.name == "greet" for s in store.all_skills())

    def test_scoped_cleans_up_on_exception(self, skills_dir, tmp_path):
        extra = self._make_extra_skill(tmp_path / "wf_skills")
        store = SkillStore(str(skills_dir))
        store.discover()

        try:
            with store.scoped(extra):
                assert store.get("skills.extra.greet") is not None
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert store.get("skills.extra.greet") is None
        assert extra not in store._extra_dirs

    def test_scoped_does_not_shadow_existing(self, skills_dir, tmp_path):
        store = SkillStore(str(skills_dir))
        store.discover()
        original = store.get("skills.math.add_numbers")
        assert original is not None

        # Create an extra dir with the same qualified name
        extra = self._make_extra_skill(tmp_path / "wf_skills", ns="math", name="add_numbers")

        with store.scoped(extra):
            sd = store.get("skills.math.add_numbers")
            assert sd is not None
            assert sd.path == original.path
