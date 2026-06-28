"""Unit tests for acai/tools/project.py."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

from acai.tools.project import tree, summary, find, _is_ignored_dir, _load_gitignore


class TestIsIgnoredDir:
    def test_standard_dirs(self):
        assert _is_ignored_dir(".git") is True
        assert _is_ignored_dir("__pycache__") is True
        assert _is_ignored_dir("node_modules") is True
        assert _is_ignored_dir(".venv") is True

    def test_egg_info(self):
        assert _is_ignored_dir("mypackage.egg-info") is True

    def test_normal_dirs(self):
        assert _is_ignored_dir("src") is False
        assert _is_ignored_dir("tests") is False


class TestTree:
    def test_basic_tree(self, tmp_path):
        (tmp_path / "main.py").write_text("x")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.py").write_text("y")

        result = json.loads(tree(cwd=str(tmp_path)))
        assert result["count"] == 2
        assert "main.py" in result["files"]
        assert os.path.join("src", "app.py") in result["files"]

    def test_ignores_pycache(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-312.pyc").write_text("x")
        (tmp_path / "mod.py").write_text("y")

        result = json.loads(tree(cwd=str(tmp_path)))
        assert result["count"] == 1
        assert "mod.py" in result["files"]

    def test_ignores_binary_suffixes(self, tmp_path):
        (tmp_path / "lib.so").write_text("x")
        (tmp_path / "lib.py").write_text("y")

        result = json.loads(tree(cwd=str(tmp_path)))
        assert result["count"] == 1
        assert "lib.py" in result["files"]

    def test_max_depth(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "file.txt").write_text("x")
        (tmp_path / "root.txt").write_text("y")

        result = json.loads(tree(cwd=str(tmp_path), max_depth=1))
        assert "root.txt" in result["files"]
        files_str = " ".join(result["files"])
        assert "c" not in files_str

    def test_subpath(self, tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "mod.py").write_text("x")
        (tmp_path / "readme.md").write_text("y")

        result = json.loads(tree(cwd=str(tmp_path), subpath="pkg"))
        assert result["count"] == 1
        assert "mod.py" in result["files"]

    def test_nonexistent_path(self):
        result = json.loads(tree(cwd="/nonexistent"))
        assert "error" in result

    def test_truncation_at_5000(self, tmp_path):
        for i in range(5010):
            (tmp_path / f"file_{i:05d}.txt").write_text("x")

        result = json.loads(tree(cwd=str(tmp_path)))
        # The loop adds one "truncated" entry after exceeding 5000
        assert any("truncated" in f for f in result["files"])


class TestSummary:
    def test_basic_summary(self, tmp_path):
        (tmp_path / "main.py").write_text("x")
        (tmp_path / "utils.py").write_text("y")
        (tmp_path / "readme.md").write_text("z")

        result = json.loads(summary(cwd=str(tmp_path)))
        root_dir = next(d for d in result["directories"] if d["dir"] == ".")
        assert root_dir["files"] == 3
        assert ".py" in root_dir["by_ext"]
        assert ".md" in root_dir["by_ext"]

    def test_nested_directories(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.ts").write_text("x")

        result = json.loads(summary(cwd=str(tmp_path)))
        dirs = [d["dir"] for d in result["directories"]]
        assert "src" in dirs

    def test_nonexistent(self):
        result = json.loads(summary(cwd="/nonexistent"))
        assert "error" in result


class TestFind:
    def test_find_by_pattern(self, tmp_path):
        (tmp_path / "test_foo.py").write_text("x")
        (tmp_path / "test_bar.py").write_text("y")
        (tmp_path / "utils.py").write_text("z")

        result = json.loads(find("test_*", cwd=str(tmp_path)))
        assert result["count"] == 2
        assert all("test_" in m for m in result["matches"])

    def test_find_by_type(self, tmp_path):
        (tmp_path / "app.py").write_text("x")
        (tmp_path / "app.js").write_text("y")

        result = json.loads(find("*", cwd=str(tmp_path), file_type="py"))
        assert result["count"] == 1
        assert "app.py" in result["matches"]

    def test_max_results(self, tmp_path):
        for i in range(20):
            (tmp_path / f"file_{i}.txt").write_text("x")

        result = json.loads(find("file_*", cwd=str(tmp_path), max_results=5))
        assert result["count"] == 5
        assert result["truncated"] is True

    def test_recursive(self, tmp_path):
        sub = tmp_path / "nested"
        sub.mkdir()
        (sub / "deep.py").write_text("x")

        result = json.loads(find("*.py", cwd=str(tmp_path)))
        assert result["count"] == 1

    def test_nonexistent(self):
        result = json.loads(find("*", cwd="/nonexistent"))
        assert "error" in result

    def test_ignores_dotdirs(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("x")
        (tmp_path / "public.py").write_text("y")

        result = json.loads(find("*.py", cwd=str(tmp_path)))
        assert result["count"] == 1
        assert "public.py" in result["matches"]


class TestLoadGitignore:
    def test_no_gitignore(self, tmp_path):
        result = _load_gitignore(str(tmp_path))
        assert result == set()

    def test_with_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        with patch("acai.tools.project.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="dist/file.js\nbuild/out.o\n", returncode=0
            )
            result = _load_gitignore(str(tmp_path))
            assert "dist/file.js" in result
            assert "build/out.o" in result

    def test_git_failure(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        with patch("acai.tools.project.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=128)
            result = _load_gitignore(str(tmp_path))
            assert result == set()

    def test_oserror(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        with patch("acai.tools.project.subprocess.run", side_effect=OSError("no git")):
            result = _load_gitignore(str(tmp_path))
            assert result == set()
