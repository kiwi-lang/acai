"""Unit tests for acai/tools/search.py."""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

from acai.tools.search import glob_files, grep


class TestGlobFiles:
    def test_basic_glob(self, tmp_path):
        (tmp_path / "hello.py").write_text("x")
        (tmp_path / "world.py").write_text("y")
        (tmp_path / "readme.md").write_text("z")

        result = json.loads(glob_files("*.py", path=str(tmp_path)))
        assert result["count"] == 2
        assert "hello.py" in result["matches"]
        assert "world.py" in result["matches"]

    def test_recursive_glob(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.py").write_text("x")
        (tmp_path / "setup.py").write_text("y")

        result = json.loads(glob_files("**/*.py", path=str(tmp_path)))
        assert result["count"] == 2

    def test_not_a_directory(self):
        result = json.loads(glob_files("*.py", path="/nonexistent/path"))
        assert "error" in result
        assert "not a directory" in result["error"]

    def test_no_matches(self, tmp_path):
        (tmp_path / "file.txt").write_text("x")
        result = json.loads(glob_files("*.py", path=str(tmp_path)))
        assert result["count"] == 0
        assert result["matches"] == []

    def test_sorted_by_mtime(self, tmp_path):
        import time
        (tmp_path / "old.py").write_text("old")
        time.sleep(0.05)
        (tmp_path / "new.py").write_text("new")

        result = json.loads(glob_files("*.py", path=str(tmp_path)))
        assert result["matches"][0] == "new.py"


class TestGrep:
    def test_rg_available(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout="file.py:10:match here\n", returncode=0
                )
                result = json.loads(grep("match", path="/tmp"))
                assert result["backend"] == "rg"
                assert len(result["lines"]) == 1

    def test_rg_files_with_matches(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="file1.py\nfile2.py\n", returncode=0)
                result = json.loads(grep("pattern", output_mode="files_with_matches"))
                cmd = mock_run.call_args[0][0]
                assert "-l" in cmd

    def test_rg_count_mode(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="file.py:5\n", returncode=0)
                result = json.loads(grep("pattern", output_mode="count"))
                cmd = mock_run.call_args[0][0]
                assert "-c" in cmd

    def test_rg_case_insensitive(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="", returncode=1)
                grep("pattern", case_insensitive=True)
                cmd = mock_run.call_args[0][0]
                assert "-i" in cmd

    def test_rg_context_lines(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="", returncode=1)
                grep("pattern", context_lines=3)
                cmd = mock_run.call_args[0][0]
                assert "-C" in cmd
                assert "3" in cmd

    def test_rg_before_after_context(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="", returncode=1)
                grep("pattern", context_before=2, context_after=4)
                cmd = mock_run.call_args[0][0]
                assert "-B" in cmd
                assert "-A" in cmd

    def test_rg_glob_filter(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="", returncode=1)
                grep("pattern", glob_filter="*.py")
                cmd = mock_run.call_args[0][0]
                assert "--glob" in cmd
                assert "*.py" in cmd

    def test_rg_file_type(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="", returncode=1)
                grep("pattern", file_type="py")
                cmd = mock_run.call_args[0][0]
                assert "--type" in cmd
                assert "py" in cmd

    def test_grep_fallback(self):
        with patch("acai.tools.search.shutil.which", return_value=None):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="file.py:10:match\n", returncode=0)
                result = json.loads(grep("match", path="/tmp"))
                assert result["backend"] == "grep"
                cmd = mock_run.call_args[0][0]
                assert cmd[0] == "grep"

    def test_grep_fallback_files_with_matches(self):
        with patch("acai.tools.search.shutil.which", return_value=None):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="file.py\n", returncode=0)
                grep("pattern", output_mode="files_with_matches", glob_filter="*.py")
                cmd = mock_run.call_args[0][0]
                assert "-rl" in cmd
                assert "--include" in cmd

    def test_grep_fallback_count(self):
        with patch("acai.tools.search.shutil.which", return_value=None):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="file.py:3\n", returncode=0)
                grep("pattern", output_mode="count")
                cmd = mock_run.call_args[0][0]
                assert "-c" in cmd

    def test_truncation(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run") as mock_run:
                lines = "\n".join(f"file.py:{i}:match" for i in range(300))
                mock_run.return_value = MagicMock(stdout=lines, returncode=0)
                result = json.loads(grep("match", head_limit=50))
                assert len(result["lines"]) == 50
                assert result["truncated"] is True

    def test_timeout(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run", side_effect=subprocess.TimeoutExpired("rg", 60)):
                result = json.loads(grep("pattern"))
                assert "error" in result
                assert "timed out" in result["error"]

    def test_oserror(self):
        with patch("acai.tools.search.shutil.which", return_value="/usr/bin/rg"):
            with patch("acai.tools.search.subprocess.run", side_effect=OSError("bad")):
                result = json.loads(grep("pattern"))
                assert "error" in result
