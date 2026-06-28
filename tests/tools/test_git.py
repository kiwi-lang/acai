"""Unit tests for acai/tools/git.py."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from acai.tools.git import status, diff, commit, push, log, worktree_list, worktree_add, worktree_remove


class TestStatus:
    def test_returns_branch_and_files(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=" M file.py\n", returncode=0),
                MagicMock(stdout="main\n", returncode=0),
            ]
            result = json.loads(status(cwd="/tmp/repo"))
            assert result["branch"] == "main"
            assert "file.py" in result["files"]
            assert result["clean"] is False

    def test_clean_repo(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="", returncode=0),
                MagicMock(stdout="feature\n", returncode=0),
            ]
            result = json.loads(status())
            assert result["clean"] is True
            assert result["branch"] == "feature"

    def test_exception_returns_error(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("no git")):
            result = json.loads(status())
            assert "error" in result
            assert "no git" in result["error"]


class TestDiff:
    def test_diff_no_ref(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="+added line\n", returncode=0)
            result = diff(cwd="/tmp/repo")
            assert "+added line" in result
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "diff"]

    def test_diff_with_ref(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="diff output\n", returncode=0)
            diff(ref="HEAD~1")
            cmd = mock_run.call_args[0][0]
            assert "HEAD~1" in cmd

    def test_no_changes(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = diff()
            assert result == "(no changes)"

    def test_truncation(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="x" * 9000, returncode=0)
            result = diff()
            assert len(result) < 9000
            assert "truncated" in result

    def test_exception_returns_error(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(diff())
            assert "error" in result


class TestCommit:
    def test_commit_all(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add -A
                MagicMock(stdout="[main abc1234] test msg\n", returncode=0),
            ]
            result = json.loads(commit("test msg", cwd="/tmp/repo"))
            assert result["ok"] is True
            add_cmd = mock_run.call_args_list[0][0][0]
            assert add_cmd == ["git", "add", "-A"]

    def test_commit_specific_files(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add file1.py
                MagicMock(returncode=0),  # git add file2.py
                MagicMock(stdout="[main abc] commit\n", returncode=0),
            ]
            result = json.loads(commit("msg", files="file1.py file2.py"))
            assert result["ok"] is True
            assert mock_run.call_count == 3

    def test_nothing_to_commit(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add -A
                MagicMock(stdout="nothing to commit, working tree clean", returncode=1),
            ]
            result = json.loads(commit("msg"))
            assert result["ok"] is False
            assert "nothing to commit" in result["message"]

    def test_commit_error(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0),  # git add
                MagicMock(stdout="", stderr="error: something wrong\n", returncode=1),
            ]
            result = json.loads(commit("msg"))
            assert "error" in result

    def test_exception(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(commit("msg"))
            assert "error" in result


class TestPush:
    def test_push_success(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="feature-branch\n", returncode=0),
                MagicMock(stdout="", returncode=0),
            ]
            result = json.loads(push(cwd="/tmp/repo"))
            assert result["ok"] is True
            assert result["branch"] == "feature-branch"
            assert result["remote"] == "origin"

    def test_push_custom_remote(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="main\n", returncode=0),
                MagicMock(stdout="", returncode=0),
            ]
            result = json.loads(push(remote="upstream"))
            assert result["remote"] == "upstream"
            push_cmd = mock_run.call_args_list[1][0][0]
            assert "upstream" in push_cmd

    def test_push_failure(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="main\n", returncode=0),
                MagicMock(stderr="rejected\n", returncode=1),
            ]
            result = json.loads(push())
            assert "error" in result
            assert "rejected" in result["error"]

    def test_exception(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(push())
            assert "error" in result


class TestLog:
    def test_log_returns_output(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc1234 first commit\ndef5678 second\n", returncode=0)
            result = log(count=5)
            assert "abc1234" in result
            cmd = mock_run.call_args[0][0]
            assert "--max-count=5" in cmd

    def test_log_no_commits(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = log()
            assert result == "(no commits)"

    def test_exception(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(log())
            assert "error" in result


class TestWorktreeList:
    def test_success(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="worktree /tmp/main\nHEAD abc123\nbranch refs/heads/main\n",
                returncode=0,
            )
            result = json.loads(worktree_list())
            assert "listing" in result

    def test_failure(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="not a repo\n", returncode=128)
            result = json.loads(worktree_list())
            assert "error" in result

    def test_exception(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(worktree_list())
            assert "error" in result


class TestWorktreeAdd:
    def test_add_with_branch(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Preparing worktree\n", returncode=0)
            result = json.loads(worktree_add("/tmp/wt", branch="feature"))
            assert result["ok"] is True
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "worktree", "add", "/tmp/wt", "feature"]

    def test_add_without_branch(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = json.loads(worktree_add("/tmp/wt"))
            assert result["ok"] is True
            cmd = mock_run.call_args[0][0]
            assert cmd == ["git", "worktree", "add", "/tmp/wt"]

    def test_failure(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="fatal\n", returncode=128)
            result = json.loads(worktree_add("/tmp/wt"))
            assert "error" in result

    def test_exception(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(worktree_add("/tmp/wt"))
            assert "error" in result


class TestWorktreeRemove:
    def test_remove(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = json.loads(worktree_remove("/tmp/wt"))
            assert result["ok"] is True
            cmd = mock_run.call_args[0][0]
            assert "--force" not in cmd

    def test_remove_force(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = json.loads(worktree_remove("/tmp/wt", force=True))
            assert result["ok"] is True
            cmd = mock_run.call_args[0][0]
            assert "--force" in cmd

    def test_failure(self):
        with patch("acai.tools.git.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", stderr="error\n", returncode=1)
            result = json.loads(worktree_remove("/tmp/wt"))
            assert "error" in result

    def test_exception(self):
        with patch("acai.tools.git.subprocess.run", side_effect=OSError("fail")):
            result = json.loads(worktree_remove("/tmp/wt"))
            assert "error" in result
