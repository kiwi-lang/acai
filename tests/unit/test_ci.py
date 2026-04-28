"""Tests for CI tools — backend detection, dispatch, and GitHub backend."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from acai.tools import ci
from acai.tools.ci import (
    _GitHubBackend,
    _detect_platform,
    _backend_for,
    _MAX_LOG_CHARS,
)


# -- Platform detection ----------------------------------------------------


class TestDetectPlatform:
    def test_github_ssh(self, tmp_path):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="git@github.com:user/repo.git\n", returncode=0,
            )
            assert _detect_platform(str(tmp_path)) == "github"

    def test_github_https(self, tmp_path):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="https://github.com/user/repo.git\n", returncode=0,
            )
            assert _detect_platform(str(tmp_path)) == "github"

    def test_gitlab(self, tmp_path):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="git@gitlab.com:org/project.git\n", returncode=0,
            )
            assert _detect_platform(str(tmp_path)) == "gitlab"

    def test_codeberg(self, tmp_path):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="https://codeberg.org/user/repo.git\n", returncode=0,
            )
            assert _detect_platform(str(tmp_path)) == "codeberg"

    def test_unknown_host(self, tmp_path):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                stdout="https://myserver.com/repo.git\n", returncode=0,
            )
            assert _detect_platform(str(tmp_path)) == "unknown"

    def test_no_remote(self, tmp_path):
        with patch("subprocess.run", side_effect=Exception("no remote")):
            assert _detect_platform(str(tmp_path)) == "unknown"


class TestBackendFor:
    def test_explicit_platform(self):
        backend = _backend_for(".", "github")
        assert isinstance(backend, _GitHubBackend)

    def test_unsupported_platform_raises(self):
        with pytest.raises(ValueError, match="not yet supported"):
            _backend_for(".", "bitbucket")

    def test_auto_detect(self):
        with patch.object(ci, "_detect_platform", return_value="github"):
            backend = _backend_for(".", "")
            assert isinstance(backend, _GitHubBackend)


# -- GitHub backend --------------------------------------------------------


class TestGitHubBackend:
    @pytest.fixture
    def backend(self):
        return _GitHubBackend()

    def test_gh_success_json(self, backend):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout='[{"id": 1}]', stderr="",
            )
            result = backend._gh(["run", "list"], ".")
            assert result == [{"id": 1}]

    def test_gh_success_text(self, backend):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="plain text output", stderr="",
            )
            result = backend._gh(["run", "view", "123", "--log"], ".")
            assert result == "plain text output"

    def test_gh_failure_raises(self, backend):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=1, stdout="", stderr="not found",
            )
            with pytest.raises(RuntimeError, match="not found"):
                backend._gh(["run", "view", "999"], ".")

    def test_gh_empty_output(self, backend):
        with patch("subprocess.run") as mock:
            mock.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            result = backend._gh(["workflow", "run", "test.yml"], ".")
            assert result == {}

    def test_list_workflows(self, backend):
        workflows = [{"id": 1, "name": "CI", "state": "active", "path": ".github/workflows/ci.yml"}]
        with patch.object(backend, "_gh", return_value=workflows):
            result = backend.list_workflows(".")
            assert result == workflows

    def test_list_runs_with_filters(self, backend):
        runs = [{"databaseId": 42, "status": "completed"}]
        with patch.object(backend, "_gh", return_value=runs) as mock_gh:
            result = backend.list_runs(".", branch="main", workflow="ci.yml", status="failure", limit=5)
            assert result == runs
            args = mock_gh.call_args[0][0]
            assert "--branch" in args
            assert "main" in args
            assert "--workflow" in args
            assert "ci.yml" in args
            assert "--status" in args
            assert "failure" in args

    def test_get_run(self, backend):
        run_data = {"databaseId": 42, "status": "completed", "jobs": []}
        with patch.object(backend, "_gh", return_value=run_data):
            result = backend.get_run(".", "42")
            assert result["databaseId"] == 42

    def test_get_logs_failed_only(self, backend):
        with patch.object(backend, "_gh", return_value="Error in step X") as mock_gh:
            result = backend.get_logs(".", "42", failed_only=True)
            assert result == "Error in step X"
            args = mock_gh.call_args[0][0]
            assert "--log-failed" in args

    def test_get_logs_all(self, backend):
        with patch.object(backend, "_gh", return_value="full logs") as mock_gh:
            result = backend.get_logs(".", "42", failed_only=False)
            assert result == "full logs"
            args = mock_gh.call_args[0][0]
            assert "--log" in args

    def test_trigger(self, backend):
        with patch.object(backend, "_gh", return_value={}):
            result = backend.trigger(".", "ci.yml", "main", {"debug": "true"})
            assert result["ok"] is True
            assert result["workflow"] == "ci.yml"
            assert result["ref"] == "main"

    def test_trigger_no_ref(self, backend):
        with patch.object(backend, "_gh", return_value={}):
            result = backend.trigger(".", "ci.yml", "", {})
            assert result["ref"] == "(default)"

    def test_rerun_failed(self, backend):
        with patch.object(backend, "_gh", return_value={}) as mock_gh:
            result = backend.rerun(".", "42", failed_only=True)
            assert result["ok"] is True
            args = mock_gh.call_args[0][0]
            assert "--failed" in args

    def test_rerun_all(self, backend):
        with patch.object(backend, "_gh", return_value={}) as mock_gh:
            result = backend.rerun(".", "42", failed_only=False)
            assert result["ok"] is True
            args = mock_gh.call_args[0][0]
            assert "--failed" not in args

    def test_cancel(self, backend):
        with patch.object(backend, "_gh", return_value={}):
            result = backend.cancel(".", "42")
            assert result["ok"] is True
            assert result["run_id"] == "42"


# -- Public tool functions -------------------------------------------------


class TestPublicTools:
    """Test the top-level tool functions (JSON in, JSON out)."""

    def _mock_backend(self):
        return MagicMock(spec=_GitHubBackend)

    @pytest.fixture(autouse=True)
    def _patch_backend(self):
        mock = self._mock_backend()
        with patch.object(ci, "_backend_for", return_value=mock):
            self.mock_backend = mock
            yield

    def test_list_workflows(self):
        self.mock_backend.list_workflows.return_value = [
            {"id": 1, "name": "CI"},
        ]
        result = json.loads(ci.list_workflows())
        assert result["count"] == 1
        assert result["workflows"][0]["name"] == "CI"

    def test_list_runs(self):
        self.mock_backend.list_runs.return_value = [
            {"databaseId": 10, "status": "completed"},
        ]
        result = json.loads(ci.list_runs(branch="main"))
        assert result["count"] == 1

    def test_get_run(self):
        self.mock_backend.get_run.return_value = {
            "databaseId": 10, "status": "failure", "jobs": [],
        }
        result = json.loads(ci.get_run("10"))
        assert result["status"] == "failure"

    def test_get_logs_truncation(self):
        huge_log = "x" * (_MAX_LOG_CHARS + 5000)
        self.mock_backend.get_logs.return_value = huge_log
        result = json.loads(ci.get_logs("10"))
        assert "truncated" in result["logs"]
        assert len(result["logs"]) < len(huge_log)

    def test_get_logs_short(self):
        self.mock_backend.get_logs.return_value = "short log"
        result = json.loads(ci.get_logs("10"))
        assert result["logs"] == "short log"
        assert result["failed_only"] is True

    def test_trigger(self):
        self.mock_backend.trigger.return_value = {"ok": True, "workflow": "ci.yml", "ref": "main"}
        result = json.loads(ci.trigger("ci.yml", ref="main"))
        assert result["ok"] is True

    def test_trigger_with_inputs(self):
        self.mock_backend.trigger.return_value = {"ok": True, "workflow": "ci.yml", "ref": "main"}
        result = json.loads(ci.trigger("ci.yml", inputs='{"debug": "true"}'))
        assert result["ok"] is True
        call_args = self.mock_backend.trigger.call_args
        assert call_args[0][3] == {"debug": "true"}

    def test_trigger_invalid_inputs(self):
        result = json.loads(ci.trigger("ci.yml", inputs="not json"))
        assert "error" in result

    def test_rerun(self):
        self.mock_backend.rerun.return_value = {"ok": True, "run_id": "10", "failed_only": True}
        result = json.loads(ci.rerun("10"))
        assert result["ok"] is True

    def test_cancel(self):
        self.mock_backend.cancel.return_value = {"ok": True, "run_id": "10"}
        result = json.loads(ci.cancel("10"))
        assert result["ok"] is True

    def test_error_returns_json(self):
        self.mock_backend.list_workflows.side_effect = RuntimeError("gh not found")
        result = json.loads(ci.list_workflows())
        assert "error" in result
        assert "gh not found" in result["error"]
