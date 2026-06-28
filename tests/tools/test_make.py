"""Unit tests for acai/tools/make.py."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from acai.tools.make import list_targets, run_target, _parse_targets


class TestParseTargets:
    def test_simple_target(self):
        content = "all:\n\techo hello\n"
        targets = _parse_targets(content)
        assert len(targets) == 1
        assert targets[0]["name"] == "all"
        assert targets[0]["recipe"] == ["echo hello"]

    def test_target_with_deps(self):
        content = "build: src/*.py\n\tpython setup.py build\n"
        targets = _parse_targets(content)
        assert targets[0]["deps"] == "src/*.py"

    def test_target_with_description(self):
        content = "# Run the test suite\ntest:\n\tpytest\n"
        targets = _parse_targets(content)
        assert targets[0]["description"] == "Run the test suite"

    def test_multi_line_recipe(self):
        content = "deploy:\n\tgit pull\n\tmake build\n\tpython run.py\n"
        targets = _parse_targets(content)
        assert len(targets[0]["recipe"]) == 3

    def test_skips_variable_assignments(self):
        content = "CC := gcc\nall:\n\t$(CC) main.c\n"
        targets = _parse_targets(content)
        assert len(targets) == 1
        assert targets[0]["name"] == "all"

    def test_skips_variable_expansions_in_name(self):
        content = "$(TARGETS):\n\techo hi\nclean:\n\trm -rf build\n"
        targets = _parse_targets(content)
        assert len(targets) == 1
        assert targets[0]["name"] == "clean"

    def test_skips_dot_targets(self):
        content = ".PHONY: all test\nall:\n\techo all\n"
        targets = _parse_targets(content)
        assert len(targets) == 1
        assert targets[0]["name"] == "all"

    def test_multiple_targets(self):
        content = "test:\n\tpytest\nbuild:\n\tmake\nclean:\n\trm -rf dist\n"
        targets = _parse_targets(content)
        names = [t["name"] for t in targets]
        assert "test" in names
        assert "build" in names
        assert "clean" in names

    def test_empty_makefile(self):
        targets = _parse_targets("")
        assert targets == []

    def test_comments_only(self):
        content = "# This is a comment\n# Another comment\n"
        targets = _parse_targets(content)
        assert targets == []


class TestListTargets:
    def test_success(self, tmp_path):
        mf = tmp_path / "Makefile"
        mf.write_text("test:\n\tpytest\nbuild:\n\tpython setup.py build\n")

        result = json.loads(list_targets(cwd=str(tmp_path)))
        assert result["count"] == 2
        names = [t["name"] for t in result["targets"]]
        assert "test" in names
        assert "build" in names

    def test_no_makefile(self, tmp_path):
        result = json.loads(list_targets(cwd=str(tmp_path)))
        assert "error" in result
        assert "no Makefile" in result["error"]

    def test_custom_makefile_name(self, tmp_path):
        mf = tmp_path / "Makefile.ci"
        mf.write_text("lint:\n\truff check .\n")

        result = json.loads(list_targets(cwd=str(tmp_path), makefile="Makefile.ci"))
        assert result["count"] == 1

    def test_read_error(self, tmp_path):
        mf = tmp_path / "Makefile"
        mf.write_text("")
        with patch("builtins.open", side_effect=OSError("permission denied")):
            result = json.loads(list_targets(cwd=str(tmp_path)))
            assert "error" in result


class TestRunTarget:
    def test_run_success(self, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\techo pass\n")
        with patch("acai.tools.make.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="pass\n", stderr="", returncode=0)
            result = json.loads(run_target("test", cwd=str(tmp_path)))
            assert result["success"] is True
            assert result["target"] == "test"

    def test_run_failure(self, tmp_path):
        (tmp_path / "Makefile").write_text("fail:\n\texit 1\n")
        with patch("acai.tools.make.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="error\n", returncode=2)
            result = json.loads(run_target("fail", cwd=str(tmp_path)))
            assert result["success"] is False
            assert result["returncode"] == 2

    def test_no_makefile(self, tmp_path):
        result = json.loads(run_target("test", cwd=str(tmp_path)))
        assert "error" in result

    def test_with_variables(self, tmp_path):
        (tmp_path / "Makefile").write_text("run:\n\techo $(FILE)\n")
        with patch("acai.tools.make.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="foo.py\n", stderr="", returncode=0)
            run_target("run", cwd=str(tmp_path), variables="FILE=foo.py")
            cmd = mock_run.call_args[0][0]
            assert "FILE=foo.py" in cmd

    def test_timeout(self, tmp_path):
        (tmp_path / "Makefile").write_text("slow:\n\tsleep 999\n")
        with patch("acai.tools.make.subprocess.run", side_effect=subprocess.TimeoutExpired("make", 600)):
            result = json.loads(run_target("slow", cwd=str(tmp_path)))
            assert "error" in result
            assert "timed out" in result["error"]

    def test_oserror(self, tmp_path):
        (tmp_path / "Makefile").write_text("run:\n\techo hi\n")
        with patch("acai.tools.make.subprocess.run", side_effect=OSError("no make")):
            result = json.loads(run_target("run", cwd=str(tmp_path)))
            assert "error" in result

    def test_stdout_truncation(self, tmp_path):
        (tmp_path / "Makefile").write_text("big:\n\techo x\n")
        with patch("acai.tools.make.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="x" * 10000, stderr="", returncode=0)
            result = json.loads(run_target("big", cwd=str(tmp_path)))
            assert "truncated" in result["stdout"]
            assert len(result["stdout"]) < 10000

    def test_custom_makefile(self, tmp_path):
        (tmp_path / "build.mk").write_text("go:\n\techo go\n")
        with patch("acai.tools.make.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="go\n", stderr="", returncode=0)
            result = json.loads(run_target("go", cwd=str(tmp_path), makefile="build.mk"))
            assert result["success"] is True
            cmd = mock_run.call_args[0][0]
            assert "-f" in cmd
            assert "build.mk" in cmd
