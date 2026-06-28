"""Unit tests for acai/tools/code.py."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

from acai.tools.code import (
    file_outline,
    search,
    run_tests,
    lint,
    typecheck,
    build,
    _python_outline,
    _regex_outline,
    _run,
)


class TestPythonOutline:
    def test_functions(self):
        source = "def hello(name: str):\n    pass\n"
        symbols = _python_outline(source)
        assert len(symbols) == 1
        assert symbols[0]["type"] == "function"
        assert symbols[0]["name"] == "hello"
        assert "name" in symbols[0]["args"]

    def test_classes_with_methods(self):
        source = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        pass\n"
            "    async def baz(self, x):\n"
            "        pass\n"
        )
        symbols = _python_outline(source)
        assert len(symbols) == 1
        cls = symbols[0]
        assert cls["type"] == "class"
        assert cls["name"] == "Foo"
        assert len(cls["members"]) == 2
        assert cls["members"][0]["name"] == "bar"
        assert cls["members"][1]["name"] == "baz"

    def test_variables(self):
        source = "X = 42\ny: int = 10\n"
        symbols = _python_outline(source)
        names = [s["name"] for s in symbols]
        assert "X" in names
        assert "y" in names

    def test_imports(self):
        source = "import os\nfrom sys import argv\n"
        symbols = _python_outline(source)
        assert any(s["type"] == "import" for s in symbols)

    def test_syntax_error(self):
        source = "def broken(:\n"
        symbols = _python_outline(source)
        assert len(symbols) == 1
        assert "error" in symbols[0]

    def test_async_function(self):
        source = "async def fetch(url: str) -> str:\n    pass\n"
        symbols = _python_outline(source)
        assert symbols[0]["type"] == "function"
        assert symbols[0]["name"] == "fetch"


class TestRegexOutline:
    def test_javascript(self):
        source = "export function hello() {}\nclass Foo {}\nconst bar = 1;\n"
        symbols = _regex_outline(source, ".js")
        names = [s["name"] for s in symbols]
        assert "hello" in names
        assert "Foo" in names
        assert "bar" in names

    def test_go(self):
        source = "func main() {\n}\ntype Server struct {\n}\n"
        symbols = _regex_outline(source, ".go")
        names = [s["name"] for s in symbols]
        assert "main" in names
        assert "Server" in names

    def test_rust(self):
        source = "pub fn process() {}\npub struct Config {}\nenum State {}\n"
        symbols = _regex_outline(source, ".rs")
        names = [s["name"] for s in symbols]
        assert "process" in names
        assert "Config" in names
        assert "State" in names

    def test_unknown_extension(self):
        source = "some content"
        symbols = _regex_outline(source, ".xyz")
        assert symbols == []

    def test_typescript_uses_js_patterns(self):
        source = "export async function loadData() {}\n"
        symbols = _regex_outline(source, ".ts")
        assert symbols[0]["name"] == "loadData"


class TestFileOutline:
    def test_python_file(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("def greet(name):\n    return f'hi {name}'\n")
        result = json.loads(file_outline(str(f)))
        assert result["language"] == "py"
        assert result["total_lines"] == 2
        assert len(result["symbols"]) == 1

    def test_js_file(self, tmp_path):
        f = tmp_path / "app.js"
        f.write_text("function main() {\n  console.log('hi');\n}\n")
        result = json.loads(file_outline(str(f)))
        assert result["language"] == "js"
        assert any(s["name"] == "main" for s in result["symbols"])

    def test_nonexistent_file(self):
        result = json.loads(file_outline("/nonexistent/file.py"))
        assert "error" in result

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = json.loads(file_outline(str(f)))
        assert result["symbols"] == []


class TestCodeSearch:
    def test_search_basic(self):
        with patch("acai.tools.code.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="./foo.py:10:def hello():\n./bar.py:5:hello()\n",
                returncode=0,
            )
            result = json.loads(search("hello", cwd="/tmp/project"))
            assert result["count"] == 2
            assert result["truncated"] is False

    def test_search_with_glob(self):
        with patch("acai.tools.code.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=1)
            search("pattern", file_glob="*.py")
            cmd = mock_run.call_args[0][0]
            assert "--include" in cmd
            assert "*.py" in cmd

    def test_search_truncation(self):
        with patch("acai.tools.code.subprocess.run") as mock_run:
            lines = "\n".join(f"./f.py:{i}:match" for i in range(100))
            mock_run.return_value = MagicMock(stdout=lines, returncode=0)
            result = json.loads(search("match", max_results=10))
            assert result["count"] == 10
            assert result["truncated"] is True

    def test_search_timeout(self):
        with patch("acai.tools.code.subprocess.run", side_effect=subprocess.TimeoutExpired("grep", 30)):
            result = json.loads(search("pattern"))
            assert "error" in result
            assert "timed out" in result["error"]

    def test_search_oserror(self):
        with patch("acai.tools.code.subprocess.run", side_effect=OSError("not found")):
            result = json.loads(search("pattern"))
            assert "error" in result


class TestRunTests:
    def test_auto_detect_pytest(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            run_tests(cwd=str(tmp_path))
            mock_run.assert_called_once_with("python -m pytest -x --tb=short -q", str(tmp_path), 300)

    def test_auto_detect_npm(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            run_tests(cwd=str(tmp_path))
            mock_run.assert_called_once_with("npm test", str(tmp_path), 300)

    def test_auto_detect_makefile(self, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\techo ok\n")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            run_tests(cwd=str(tmp_path))
            mock_run.assert_called_once_with("make test", str(tmp_path), 300)

    def test_no_detection(self, tmp_path):
        result = json.loads(run_tests(cwd=str(tmp_path)))
        assert "error" in result
        assert "could not detect" in result["error"]

    def test_explicit_command(self, tmp_path):
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            run_tests(cwd=str(tmp_path), command="pytest -v")
            mock_run.assert_called_once_with("pytest -v", str(tmp_path), 300)


class TestLint:
    def test_auto_detect_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            lint(cwd=str(tmp_path))
            mock_run.assert_called_once_with("python -m ruff check .", str(tmp_path), 120)

    def test_auto_detect_eslint(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            lint(cwd=str(tmp_path))
            mock_run.assert_called_once_with("npx eslint .", str(tmp_path), 120)

    def test_no_detection(self, tmp_path):
        result = json.loads(lint(cwd=str(tmp_path)))
        assert "error" in result


class TestTypecheck:
    def test_auto_detect_tsc(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text("{}")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            typecheck(cwd=str(tmp_path))
            mock_run.assert_called_once_with("npx tsc --noEmit", str(tmp_path), 120)

    def test_auto_detect_mypy(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            typecheck(cwd=str(tmp_path))
            mock_run.assert_called_once_with("python -m mypy .", str(tmp_path), 120)

    def test_no_detection(self, tmp_path):
        result = json.loads(typecheck(cwd=str(tmp_path)))
        assert "error" in result


class TestBuild:
    def test_auto_detect_make(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:\n\techo done\n")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            build(cwd=str(tmp_path))
            mock_run.assert_called_once_with("make", str(tmp_path), 300)

    def test_auto_detect_npm(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        with patch("acai.tools.code._run") as mock_run:
            mock_run.return_value = json.dumps({"passed": True})
            build(cwd=str(tmp_path))
            mock_run.assert_called_once_with("npm run build", str(tmp_path), 300)

    def test_no_detection(self, tmp_path):
        result = json.loads(build(cwd=str(tmp_path)))
        assert "error" in result


class TestRun:
    def test_success(self):
        with patch("acai.tools.code.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ok\n", stderr="", returncode=0)
            result = json.loads(_run("echo ok", "/tmp", 60))
            assert result["passed"] is True
            assert result["returncode"] == 0
            assert result["command"] == "echo ok"

    def test_failure(self):
        with patch("acai.tools.code.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="err\n", returncode=1)
            result = json.loads(_run("false", "/tmp", 60))
            assert result["passed"] is False

    def test_timeout(self):
        with patch("acai.tools.code.subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 60)):
            result = json.loads(_run("long-cmd", "/tmp", 60))
            assert "error" in result
            assert "timed out" in result["error"]

    def test_stdout_truncation(self):
        with patch("acai.tools.code.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="x" * 5000, stderr="", returncode=0)
            result = json.loads(_run("cmd", "/tmp", 60))
            assert len(result["stdout"]) == 4000
