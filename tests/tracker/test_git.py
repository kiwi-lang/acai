"""Unit tests for acai/tracker/git.py."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, call, mock_open, patch

import pytest

from acai.tracker.git import GitTracker, Worktree


# ------------------------------------------------------------------
# Worktree dataclass
# ------------------------------------------------------------------


class TestWorktreeDataclass:
    def test_defaults(self):
        wt = Worktree(path="/tmp/wt", branch="main")
        assert wt.path == "/tmp/wt"
        assert wt.branch == "main"
        assert wt.head == ""

    def test_all_fields(self):
        wt = Worktree(path="/a", branch="b", head="abc123")
        assert wt.head == "abc123"


# ------------------------------------------------------------------
# GitTracker.__init__
# ------------------------------------------------------------------


class TestGitTrackerInit:
    def test_default_paths(self, tmp_path):
        gt = GitTracker(str(tmp_path))
        assert gt.repo == str(tmp_path)
        assert gt.worktree_dir == os.path.join(str(tmp_path), ".worktrees")

    def test_custom_worktree_dir(self, tmp_path):
        gt = GitTracker(str(tmp_path), worktree_dir="custom_wt")
        assert gt.worktree_dir == os.path.join(str(tmp_path), "custom_wt")

    def test_relative_path_resolved(self):
        gt = GitTracker(".")
        assert gt.repo == os.path.abspath(".")


# ------------------------------------------------------------------
# GitTracker._run
# ------------------------------------------------------------------


class TestRun:
    def setup_method(self):
        self.gt = GitTracker("/fake/repo")

    @patch("acai.tracker.git.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="  output  \n", stderr=""
        )
        result = self.gt._run("status")
        assert result == "output"
        mock_run.assert_called_once_with(
            ["git", "status"],
            cwd="/fake/repo",
            capture_output=True,
            text=True,
        )

    @patch("acai.tracker.git.subprocess.run")
    def test_custom_cwd(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
        self.gt._run("log", cwd="/other")
        mock_run.assert_called_once_with(
            ["git", "log"],
            cwd="/other",
            capture_output=True,
            text=True,
        )

    @patch("acai.tracker.git.subprocess.run")
    def test_failure_raises_runtime_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="fatal: not a git repo\n"
        )
        with pytest.raises(RuntimeError, match="git status failed"):
            self.gt._run("status")

    @patch("acai.tracker.git.subprocess.run")
    def test_error_message_includes_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="permission denied\n"
        )
        with pytest.raises(RuntimeError, match="permission denied"):
            self.gt._run("push")

    @patch("acai.tracker.git.subprocess.run")
    def test_check_false_no_raise(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="nothing to commit\n", stderr=""
        )
        result = self.gt._run("commit", "-m", "msg", check=False)
        assert result == "nothing to commit"

    @patch("acai.tracker.git.subprocess.run")
    def test_multiple_args(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        self.gt._run("worktree", "add", "-b", "feat", "/path")
        mock_run.assert_called_once_with(
            ["git", "worktree", "add", "-b", "feat", "/path"],
            cwd="/fake/repo",
            capture_output=True,
            text=True,
        )

    @patch("acai.tracker.git.subprocess.run")
    def test_git_not_installed(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'git'")
        with pytest.raises(FileNotFoundError, match="git"):
            self.gt._run("status")

    @patch("acai.tracker.git.subprocess.run")
    def test_permission_error(self, mock_run):
        mock_run.side_effect = PermissionError("Permission denied")
        with pytest.raises(PermissionError):
            self.gt._run("status")


# ------------------------------------------------------------------
# Worktree management
# ------------------------------------------------------------------


class TestCreateWorktree:
    def setup_method(self):
        self.gt = GitTracker("/fake/repo")

    @patch("acai.tracker.git.os.makedirs")
    @patch.object(GitTracker, "_run")
    def test_creates_worktree(self, mock_run, mock_makedirs):
        result = self.gt.create_worktree("task-1")

        expected_path = os.path.join("/fake/repo", ".worktrees", "task-1")
        assert result == expected_path
        mock_makedirs.assert_called_once_with(self.gt.worktree_dir, exist_ok=True)
        mock_run.assert_called_once_with(
            "worktree", "add", "-b", "agent/task-1", expected_path, "HEAD"
        )

    @patch("acai.tracker.git.os.makedirs")
    @patch.object(GitTracker, "_run")
    def test_custom_base_branch(self, mock_run, mock_makedirs):
        self.gt.create_worktree("task-2", base_branch="develop")
        expected_path = os.path.join("/fake/repo", ".worktrees", "task-2")
        mock_run.assert_called_once_with(
            "worktree", "add", "-b", "agent/task-2", expected_path, "develop"
        )

    @patch("acai.tracker.git.os.makedirs")
    @patch.object(GitTracker, "_run")
    def test_git_error_propagates(self, mock_run, mock_makedirs):
        mock_run.side_effect = RuntimeError("git worktree add failed:\nbranch exists")
        with pytest.raises(RuntimeError, match="branch exists"):
            self.gt.create_worktree("task-dup")


class TestRemoveWorktree:
    def setup_method(self):
        self.gt = GitTracker("/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_removes_worktree(self, mock_run):
        self.gt.remove_worktree("task-1")
        expected_path = os.path.join("/fake/repo", ".worktrees", "task-1")
        mock_run.assert_called_once_with(
            "worktree", "remove", "--force", expected_path
        )

    @patch.object(GitTracker, "_run")
    def test_remove_nonexistent_raises(self, mock_run):
        mock_run.side_effect = RuntimeError(
            "git worktree remove failed:\nfatal: '/tmp/x' is not a working tree"
        )
        with pytest.raises(RuntimeError, match="not a working tree"):
            self.gt.remove_worktree("ghost")


# ------------------------------------------------------------------
# list_worktrees
# ------------------------------------------------------------------


class TestListWorktrees:
    def setup_method(self):
        self.gt = GitTracker("/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_single_worktree(self, mock_run):
        mock_run.return_value = (
            "worktree /fake/repo\n"
            "HEAD abc1234\n"
            "branch refs/heads/main"
        )
        result = self.gt.list_worktrees()
        assert len(result) == 1
        assert result[0].path == "/fake/repo"
        assert result[0].head == "abc1234"
        assert result[0].branch == "refs/heads/main"

    @patch.object(GitTracker, "_run")
    def test_multiple_worktrees(self, mock_run):
        mock_run.return_value = (
            "worktree /fake/repo\n"
            "HEAD aaa\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /fake/repo/.worktrees/task-1\n"
            "HEAD bbb\n"
            "branch refs/heads/agent/task-1"
        )
        result = self.gt.list_worktrees()
        assert len(result) == 2
        assert result[0].path == "/fake/repo"
        assert result[0].branch == "refs/heads/main"
        assert result[1].path == "/fake/repo/.worktrees/task-1"
        assert result[1].branch == "refs/heads/agent/task-1"

    @patch.object(GitTracker, "_run")
    def test_empty_output(self, mock_run):
        mock_run.return_value = ""
        result = self.gt.list_worktrees()
        assert result == []

    @patch.object(GitTracker, "_run")
    def test_detached_head_no_branch(self, mock_run):
        mock_run.return_value = (
            "worktree /fake/repo\n"
            "HEAD deadbeef\n"
            "detached"
        )
        result = self.gt.list_worktrees()
        assert len(result) == 1
        assert result[0].path == "/fake/repo"
        assert result[0].head == "deadbeef"
        assert result[0].branch == ""

    @patch.object(GitTracker, "_run")
    def test_corrupt_output_missing_fields(self, mock_run):
        mock_run.return_value = "worktree /only/path"
        result = self.gt.list_worktrees()
        assert len(result) == 1
        assert result[0].path == "/only/path"
        assert result[0].head == ""
        assert result[0].branch == ""


# ------------------------------------------------------------------
# commit
# ------------------------------------------------------------------


class TestCommit:
    def setup_method(self):
        self.gt = GitTracker("/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_commit_all_files(self, mock_run):
        self.gt.commit("initial commit")
        assert mock_run.call_count == 2
        mock_run.assert_any_call("add", "-A", cwd="/fake/repo")
        mock_run.assert_any_call(
            "commit", "-m", "initial commit", cwd="/fake/repo", check=False
        )

    @patch.object(GitTracker, "_run")
    def test_commit_specific_files(self, mock_run):
        self.gt.commit("add files", files=["a.py", "b.py"])
        assert mock_run.call_count == 3
        mock_run.assert_any_call("add", "a.py", cwd="/fake/repo")
        mock_run.assert_any_call("add", "b.py", cwd="/fake/repo")
        mock_run.assert_any_call(
            "commit", "-m", "add files", cwd="/fake/repo", check=False
        )

    @patch.object(GitTracker, "_run")
    def test_commit_in_worktree(self, mock_run):
        self.gt.commit("wt commit", worktree="/wt/path")
        mock_run.assert_any_call("add", "-A", cwd="/wt/path")
        mock_run.assert_any_call(
            "commit", "-m", "wt commit", cwd="/wt/path", check=False
        )

    @patch.object(GitTracker, "_run")
    def test_commit_empty_files_list_stages_all(self, mock_run):
        self.gt.commit("msg", files=[])
        mock_run.assert_any_call("add", "-A", cwd="/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_commit_uses_check_false(self, mock_run):
        """commit uses check=False so 'nothing to commit' doesn't raise."""
        self.gt.commit("no-op")
        commit_call = [c for c in mock_run.call_args_list if c[0][0] == "commit"]
        assert len(commit_call) == 1
        assert commit_call[0].kwargs["check"] is False


# ------------------------------------------------------------------
# diff
# ------------------------------------------------------------------


class TestDiff:
    def setup_method(self):
        self.gt = GitTracker("/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_diff_default(self, mock_run):
        mock_run.return_value = "diff output"
        result = self.gt.diff()
        assert result == "diff output"
        mock_run.assert_called_once_with("diff", cwd="/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_diff_with_ref(self, mock_run):
        mock_run.return_value = "+new line"
        result = self.gt.diff(ref="HEAD~1")
        assert result == "+new line"
        mock_run.assert_called_once_with("diff", "HEAD~1", cwd="/fake/repo")

    @patch.object(GitTracker, "_run")
    def test_diff_in_worktree(self, mock_run):
        mock_run.return_value = ""
        self.gt.diff(worktree="/wt")
        mock_run.assert_called_once_with("diff", cwd="/wt")

    @patch.object(GitTracker, "_run")
    def test_diff_with_ref_and_worktree(self, mock_run):
        mock_run.return_value = "changes"
        result = self.gt.diff(worktree="/wt", ref="main")
        assert result == "changes"
        mock_run.assert_called_once_with("diff", "main", cwd="/wt")


# ------------------------------------------------------------------
# File helpers
# ------------------------------------------------------------------


class TestWriteFile:
    def test_writes_to_repo(self, tmp_path):
        gt = GitTracker(str(tmp_path))
        gt.write_file("hello.txt", "world")
        assert (tmp_path / "hello.txt").read_text() == "world"

    def test_creates_subdirectories(self, tmp_path):
        gt = GitTracker(str(tmp_path))
        gt.write_file("deep/nested/file.txt", "content")
        assert (tmp_path / "deep" / "nested" / "file.txt").read_text() == "content"

    def test_writes_to_worktree(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        gt = GitTracker(str(tmp_path))
        gt.write_file("out.txt", "data", worktree=str(wt))
        assert (wt / "out.txt").read_text() == "data"

    def test_overwrites_existing_file(self, tmp_path):
        gt = GitTracker(str(tmp_path))
        gt.write_file("f.txt", "v1")
        gt.write_file("f.txt", "v2")
        assert (tmp_path / "f.txt").read_text() == "v2"

    def test_permission_error(self, tmp_path):
        gt = GitTracker(str(tmp_path))
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError):
                gt.write_file("nope.txt", "x")


class TestReadFile:
    def test_reads_from_repo(self, tmp_path):
        (tmp_path / "data.txt").write_text("hello")
        gt = GitTracker(str(tmp_path))
        assert gt.read_file("data.txt") == "hello"

    def test_reads_from_worktree(self, tmp_path):
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / "note.txt").write_text("hi")
        gt = GitTracker(str(tmp_path))
        assert gt.read_file("note.txt", worktree=str(wt)) == "hi"

    def test_file_not_found(self, tmp_path):
        gt = GitTracker(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            gt.read_file("nonexistent.txt")

    def test_reads_empty_file(self, tmp_path):
        (tmp_path / "empty.txt").write_text("")
        gt = GitTracker(str(tmp_path))
        assert gt.read_file("empty.txt") == ""


# ------------------------------------------------------------------
# Thread-safety smoke test
# ------------------------------------------------------------------


class TestThreadSafety:
    @patch.object(GitTracker, "_run")
    def test_lock_is_used_in_create_worktree(self, mock_run):
        """Verify the lock is acquired (smoke test via mock)."""
        gt = GitTracker("/fake/repo")
        gt._lock = MagicMock()
        gt._lock.__enter__ = MagicMock(return_value=None)
        gt._lock.__exit__ = MagicMock(return_value=False)

        with patch("acai.tracker.git.os.makedirs"):
            gt.create_worktree("t")

        gt._lock.__enter__.assert_called()

    @patch.object(GitTracker, "_run")
    def test_lock_is_used_in_commit(self, mock_run):
        gt = GitTracker("/fake/repo")
        gt._lock = MagicMock()
        gt._lock.__enter__ = MagicMock(return_value=None)
        gt._lock.__exit__ = MagicMock(return_value=False)
        gt.commit("msg")
        gt._lock.__enter__.assert_called()
