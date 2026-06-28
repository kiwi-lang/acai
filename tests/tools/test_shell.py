"""Tests for acai.tools.shell — command execution tool."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from acai.tools.shell import run


class TestShellRun:

    def test_simple_command(self, tmp_path):
        result = json.loads(run("echo hello", cwd=str(tmp_path)))
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]

    def test_captures_stderr(self, tmp_path):
        result = json.loads(run("echo err >&2", cwd=str(tmp_path)))
        assert result["returncode"] == 0
        assert "err" in result["stderr"]

    def test_nonzero_exit_code(self, tmp_path):
        result = json.loads(run("exit 42", cwd=str(tmp_path)))
        assert result["returncode"] == 42

    def test_timeout_returns_error(self):
        result = json.loads(run("sleep 10", timeout=1))
        assert result["error"] == "timeout"
        assert result["timeout"] == 1

    def test_cwd_is_respected(self, tmp_path):
        result = json.loads(run("pwd", cwd=str(tmp_path)))
        assert result["returncode"] == 0
        assert str(tmp_path) in result["stdout"]

    def test_multiline_output(self, tmp_path):
        result = json.loads(run("printf 'a\\nb\\nc'", cwd=str(tmp_path)))
        assert result["stdout"] == "a\nb\nc"

    def test_empty_command(self, tmp_path):
        result = json.loads(run("true", cwd=str(tmp_path)))
        assert result["returncode"] == 0
        assert result["stdout"] == ""
