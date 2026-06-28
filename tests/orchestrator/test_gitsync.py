"""Unit tests for acai.orchestrator.gitsync module."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from acai.orchestrator import gitsync
from acai.orchestrator.gitsync import (
    SyncResult,
    _current_branch,
    _has_unpushed,
    _rewrite_remote_for_ssh_alias,
    _run,
    _write_ssh_config,
    ensure_sync_running,
    generate_ssh_key,
    get_last_sync,
    get_remote,
    get_ssh_key_path,
    get_ssh_public_key,
    get_status,
    git_init,
    git_sync,
    is_git_repo,
    notify_write,
    start_sync,
)


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------

class TestSyncResult:
    def test_defaults(self):
        r = SyncResult()
        assert r.commit is None
        assert r.pushed is False
        assert r.push_error is None
        assert r.error is None
        assert r.timestamp

    def test_to_dict(self):
        r = SyncResult(commit="abc1234", pushed=True, push_error=None, error=None)
        d = r.to_dict()
        assert d["commit"] == "abc1234"
        assert d["pushed"] is True
        assert d["push_error"] is None
        assert d["error"] is None
        assert "timestamp" in d

    def test_error_result(self):
        r = SyncResult(error="something went wrong")
        d = r.to_dict()
        assert d["error"] == "something went wrong"
        assert d["commit"] is None


# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------

class TestRun:
    @patch("subprocess.run")
    def test_success(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout="hello", stderr="")
        rc, out = _run(["git", "status"], Path("/tmp"))
        assert rc == 0
        assert out == "hello"
        mock_sub.assert_called_once()
        call_kwargs = mock_sub.call_args
        assert call_kwargs.kwargs["timeout"] == 30

    @patch("subprocess.run")
    def test_failure(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: not a repo")
        rc, out = _run(["git", "status"], Path("/tmp"))
        assert rc == 128
        assert "not a repo" in out

    @patch("subprocess.run")
    def test_combined_output(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout="out\n", stderr="err\n")
        rc, out = _run(["git", "status"], Path("/tmp"))
        assert out == "out\nerr"

    @patch("subprocess.run")
    def test_custom_env(self, mock_sub):
        mock_sub.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        _run(["git", "push"], Path("/tmp"), env={"GIT_SSH_COMMAND": "ssh -i key"})
        call_kwargs = mock_sub.call_args
        assert "GIT_SSH_COMMAND" in call_kwargs.kwargs["env"]

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30))
    def test_timeout_raises(self, mock_sub):
        with pytest.raises(subprocess.TimeoutExpired):
            _run(["git", "push"], Path("/tmp"))


# ---------------------------------------------------------------------------
# is_git_repo
# ---------------------------------------------------------------------------

class TestIsGitRepo:
    def test_true(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert is_git_repo(tmp_path) is True

    def test_false(self, tmp_path):
        assert is_git_repo(tmp_path) is False

    def test_file_not_dir(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: somewhere")
        assert is_git_repo(tmp_path) is False


# ---------------------------------------------------------------------------
# SSH key helpers
# ---------------------------------------------------------------------------

class TestGetSshKeyPath:
    def test_returns_expected_path(self):
        p = get_ssh_key_path()
        assert p == Path.home() / ".ssh" / "acai_ed25519"


class TestGetSshPublicKey:
    def test_key_exists(self, tmp_path):
        pub_path = tmp_path / "acai_ed25519.pub"
        pub_path.write_text("ssh-ed25519 AAAA test@host\n")
        with patch.object(gitsync, "SSH_KEY_DIR", tmp_path):
            result = get_ssh_public_key()
        assert result == "ssh-ed25519 AAAA test@host"

    def test_key_missing(self, tmp_path):
        with patch.object(gitsync, "SSH_KEY_DIR", tmp_path):
            result = get_ssh_public_key()
        assert result is None


class TestGenerateSshKey:
    def test_generates_key(self, tmp_path):
        key_dir = tmp_path / ".ssh"
        key_dir.mkdir()
        pub_path = key_dir / "acai_ed25519.pub"

        def fake_keygen(*args, **kwargs):
            pub_path.write_text("ssh-ed25519 AAAA generated@host\n")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_keygen), \
             patch.object(gitsync, "SSH_KEY_DIR", key_dir), \
             patch.object(gitsync, "SSH_KEY_NAME", "acai_ed25519"), \
             patch("acai.orchestrator.gitsync._write_ssh_config"):
            result = generate_ssh_key()

        assert result == "ssh-ed25519 AAAA generated@host"

    def test_removes_existing_keys(self, tmp_path):
        key_dir = tmp_path / ".ssh"
        key_dir.mkdir()
        key_path = key_dir / "acai_ed25519"
        pub_path = key_dir / "acai_ed25519.pub"
        key_path.write_text("old-private-key")
        pub_path.write_text("old-public-key\n")

        def fake_keygen(*args, **kwargs):
            pub_path.write_text("ssh-ed25519 AAAA new@host\n")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_keygen), \
             patch.object(gitsync, "SSH_KEY_DIR", key_dir), \
             patch("acai.orchestrator.gitsync._write_ssh_config"):
            result = generate_ssh_key()

        assert result == "ssh-ed25519 AAAA new@host"
        assert not key_path.exists()

    @patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "ssh-keygen"))
    def test_keygen_failure(self, mock_sub, tmp_path):
        key_dir = tmp_path / ".ssh"
        key_dir.mkdir()
        with patch.object(gitsync, "SSH_KEY_DIR", key_dir):
            with pytest.raises(subprocess.CalledProcessError):
                generate_ssh_key()


# ---------------------------------------------------------------------------
# _write_ssh_config
# ---------------------------------------------------------------------------

class TestWriteSshConfig:
    def test_creates_config(self, tmp_path):
        with patch.object(gitsync, "SSH_KEY_DIR", tmp_path):
            _write_ssh_config(tmp_path / "acai_ed25519")

        config = (tmp_path / "config").read_text()
        assert "Host github.com-acai" in config
        assert "IdentityFile" in config
        assert (tmp_path / "config").stat().st_mode & 0o777 == 0o600

    def test_replaces_existing_block(self, tmp_path):
        config_path = tmp_path / "config"
        config_path.write_text(
            "Host other\n  HostName other.com\n\n"
            "# acai-managed\n"
            "Host github.com-acai\n"
            "  HostName github.com\n"
            "  User git\n"
            "  IdentityFile /old/path\n"
            "  IdentitiesOnly yes\n"
        )
        with patch.object(gitsync, "SSH_KEY_DIR", tmp_path):
            _write_ssh_config(tmp_path / "new_key")

        config = config_path.read_text()
        assert "/old/path" not in config
        assert "new_key" in config
        assert "Host other" in config

    def test_no_existing_config_file(self, tmp_path):
        with patch.object(gitsync, "SSH_KEY_DIR", tmp_path):
            _write_ssh_config(tmp_path / "mykey")

        config = (tmp_path / "config").read_text()
        assert "Host github.com-acai" in config


# ---------------------------------------------------------------------------
# _rewrite_remote_for_ssh_alias
# ---------------------------------------------------------------------------

class TestRewriteRemote:
    def test_github_ssh(self):
        assert _rewrite_remote_for_ssh_alias("git@github.com:user/repo.git") == \
            "git@github.com-acai:user/repo.git"

    def test_https_untouched(self):
        url = "https://github.com/user/repo.git"
        assert _rewrite_remote_for_ssh_alias(url) == url

    def test_other_host_untouched(self):
        url = "git@gitlab.com:user/repo.git"
        assert _rewrite_remote_for_ssh_alias(url) == url

    def test_already_aliased(self):
        url = "git@github.com-acai:user/repo.git"
        assert _rewrite_remote_for_ssh_alias(url) == url


# ---------------------------------------------------------------------------
# _current_branch
# ---------------------------------------------------------------------------

class TestCurrentBranch:
    @patch("acai.orchestrator.gitsync._run")
    def test_returns_branch(self, mock_run):
        mock_run.return_value = (0, "feature-x")
        assert _current_branch(Path("/repo")) == "feature-x"

    @patch("acai.orchestrator.gitsync._run")
    def test_fallback_to_main(self, mock_run):
        mock_run.return_value = (128, "")
        assert _current_branch(Path("/repo")) == "main"

    @patch("acai.orchestrator.gitsync._run")
    def test_empty_output_fallback(self, mock_run):
        mock_run.return_value = (0, "")
        assert _current_branch(Path("/repo")) == "main"


# ---------------------------------------------------------------------------
# get_remote
# ---------------------------------------------------------------------------

class TestGetRemote:
    @patch("acai.orchestrator.gitsync._run")
    def test_returns_remote(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.return_value = (0, "git@github.com-acai:user/repo.git")
        result = get_remote(tmp_path)
        assert result == "git@github.com:user/repo.git"

    @patch("acai.orchestrator.gitsync._run")
    def test_no_remote(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.return_value = (1, "")
        assert get_remote(tmp_path) is None

    def test_not_a_repo(self, tmp_path):
        assert get_remote(tmp_path) is None


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    @patch("acai.orchestrator.gitsync._run")
    @patch("acai.orchestrator.gitsync.get_ssh_public_key")
    def test_full_status_with_repo(self, mock_key, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_key.return_value = "ssh-ed25519 AAAA key"
        mock_run.side_effect = [
            (0, "git@github.com:user/repo.git"),
            (0, "abc1234 msg1\ndef5678 msg2"),
            (0, "M file.txt"),
        ]
        status = get_status(tmp_path)
        assert status["initialized"] is True
        assert status["remote"] == "git@github.com:user/repo.git"
        assert status["ssh_key_exists"] is True
        assert status["dirty"] is True
        assert len(status["recent_commits"]) == 2

    @patch("acai.orchestrator.gitsync._run")
    @patch("acai.orchestrator.gitsync.get_ssh_public_key")
    def test_no_repo(self, mock_key, mock_run, tmp_path):
        mock_key.return_value = None
        status = get_status(tmp_path)
        assert status["initialized"] is False
        assert status["remote"] is None
        assert status["ssh_key_exists"] is False
        assert status["recent_commits"] == []
        assert status["dirty"] is False

    @patch("acai.orchestrator.gitsync._run")
    @patch("acai.orchestrator.gitsync.get_ssh_public_key")
    def test_clean_repo(self, mock_key, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_key.return_value = "ssh-ed25519 key"
        mock_run.side_effect = [
            (0, "git@github.com:user/repo.git"),
            (0, "abc1234 initial commit"),
            (0, ""),
        ]
        status = get_status(tmp_path)
        assert status["dirty"] is False

    @patch("acai.orchestrator.gitsync._last_sync", SyncResult(commit="abc", pushed=True))
    @patch("acai.orchestrator.gitsync._run")
    @patch("acai.orchestrator.gitsync.get_ssh_public_key")
    def test_includes_last_sync(self, mock_key, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_key.return_value = "key"
        mock_run.side_effect = [
            (0, "git@github.com:user/repo.git"),
            (0, "abc commit"),
            (0, ""),
        ]
        status = get_status(tmp_path)
        assert "last_sync" in status
        assert status["last_sync"]["commit"] == "abc"


# ---------------------------------------------------------------------------
# git_init
# ---------------------------------------------------------------------------

class TestGitInit:
    @patch("acai.orchestrator.gitsync._run")
    def test_init_new_repo(self, mock_run, tmp_path):
        mock_run.return_value = (0, "")
        git_init(tmp_path)
        assert mock_run.call_count >= 2
        assert (tmp_path / ".gitignore").exists()

    @patch("acai.orchestrator.gitsync._run")
    def test_existing_repo_skips_init(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.return_value = (0, "")
        git_init(tmp_path)
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert ["git", "init"] not in calls

    @patch("acai.orchestrator.gitsync._run")
    def test_adds_remote(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("existing")
        mock_run.side_effect = [
            (1, ""),
            (0, ""),
        ]
        git_init(tmp_path, remote="git@github.com:user/repo.git")
        add_call = mock_run.call_args_list[1][0][0]
        assert "remote" in add_call
        assert "add" in add_call

    @patch("acai.orchestrator.gitsync._run")
    def test_updates_remote(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("existing")
        mock_run.side_effect = [
            (0, "git@github.com-acai:old/repo.git"),
            (0, ""),
        ]
        git_init(tmp_path, remote="git@github.com:new/repo.git")
        set_call = mock_run.call_args_list[1][0][0]
        assert "set-url" in set_call

    @patch("acai.orchestrator.gitsync._run")
    def test_no_remote_update_if_same(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("existing")
        mock_run.side_effect = [
            (0, "git@github.com-acai:user/repo.git"),
        ]
        git_init(tmp_path, remote="git@github.com:user/repo.git")
        assert mock_run.call_count == 1

    @patch("acai.orchestrator.gitsync._run")
    def test_creates_directory(self, mock_run, tmp_path):
        new_dir = tmp_path / "sub" / "dir"
        mock_run.return_value = (0, "")
        git_init(new_dir)
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# _has_unpushed
# ---------------------------------------------------------------------------

class TestHasUnpushed:
    @patch("acai.orchestrator.gitsync._run")
    def test_has_unpushed(self, mock_run):
        mock_run.side_effect = [
            (0, "main"),
            (0, "3"),
        ]
        assert _has_unpushed(Path("/repo")) is True

    @patch("acai.orchestrator.gitsync._run")
    def test_no_unpushed(self, mock_run):
        mock_run.side_effect = [
            (0, "main"),
            (0, "0"),
        ]
        assert _has_unpushed(Path("/repo")) is False

    @patch("acai.orchestrator.gitsync._run")
    def test_no_remote_but_has_head(self, mock_run):
        mock_run.side_effect = [
            (0, "main"),
            (128, "fatal"),
            (0, "abc123"),
        ]
        assert _has_unpushed(Path("/repo")) is True

    @patch("acai.orchestrator.gitsync._run")
    def test_no_remote_no_head(self, mock_run):
        mock_run.side_effect = [
            (0, "main"),
            (128, "fatal"),
            (128, "fatal"),
        ]
        assert _has_unpushed(Path("/repo")) is False


# ---------------------------------------------------------------------------
# git_sync
# ---------------------------------------------------------------------------

class TestGitSync:
    @patch("acai.orchestrator.gitsync._run")
    def test_not_a_repo(self, mock_run, tmp_path):
        result = git_sync(tmp_path)
        assert result.error == "Not a git repository"

    @patch("acai.orchestrator.gitsync._run")
    def test_git_add_fails(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.return_value = (1, "error: permission denied")
        result = git_sync(tmp_path)
        assert "git add failed" in result.error
        assert "permission denied" in result.error

    @patch("acai.orchestrator.gitsync._run")
    def test_commit_fails(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            (0, ""),
            (1, ""),
            (1, "error: commit hook failed"),
        ]
        result = git_sync(tmp_path)
        assert "git commit failed" in result.error

    @patch("acai.orchestrator.gitsync._run")
    def test_nothing_to_commit_no_remote(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            (0, ""),
            (0, ""),
            (0, "abc123"),
            (1, ""),
        ]
        result = git_sync(tmp_path)
        assert result.error is None
        assert result.commit is None
        assert result.pushed is False

    @patch("acai.orchestrator.gitsync._run")
    def test_commit_and_push_success(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            (0, ""),
            (1, ""),
            (0, ""),
            (0, "abc1234"),
            (0, "git@github.com-acai:u/r.git"),
            (0, "main"),
            (0, ""),
        ]
        result = git_sync(tmp_path)
        assert result.commit == "abc1234"
        assert result.pushed is True
        assert result.error is None

    @patch("acai.orchestrator.gitsync._run")
    def test_commit_and_push_failure(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            (0, ""),
            (1, ""),
            (0, ""),
            (0, "abc1234"),
            (0, "git@github.com-acai:u/r.git"),
            (0, "main"),
            (1, "Permission denied (publickey)"),
        ]
        result = git_sync(tmp_path)
        assert result.commit == "abc1234"
        assert result.pushed is False
        assert "Permission denied" in result.push_error

    @patch("acai.orchestrator.gitsync._run")
    def test_push_unpushed_without_new_commit(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            (0, ""),
            (0, ""),
            (0, "abc1234"),
            (0, "git@github.com-acai:u/r.git"),
            (0, "main"),
            (0, "2"),
            (0, "main"),
            (0, ""),
        ]
        result = git_sync(tmp_path)
        assert result.commit is None
        assert result.pushed is True

    @patch("acai.orchestrator.gitsync._run")
    def test_updates_last_sync(self, mock_run, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_run.side_effect = [
            (0, ""),
            (0, ""),
            (0, "sha"),
            (1, ""),
        ]
        git_sync(tmp_path)
        assert gitsync._last_sync is not None


# ---------------------------------------------------------------------------
# get_last_sync
# ---------------------------------------------------------------------------

class TestGetLastSync:
    def test_initial_none(self):
        old = gitsync._last_sync
        try:
            gitsync._last_sync = None
            assert get_last_sync() is None
        finally:
            gitsync._last_sync = old

    def test_returns_last(self):
        old = gitsync._last_sync
        try:
            gitsync._last_sync = SyncResult(commit="xyz")
            assert get_last_sync().commit == "xyz"
        finally:
            gitsync._last_sync = old


# ---------------------------------------------------------------------------
# notify_write
# ---------------------------------------------------------------------------

class TestNotifyWrite:
    def test_sets_event(self):
        old = gitsync._pending
        try:
            gitsync._pending = asyncio.Event()
            notify_write()
            assert gitsync._pending.is_set()
        finally:
            gitsync._pending = old

    def test_no_op_when_none(self):
        old = gitsync._pending
        try:
            gitsync._pending = None
            notify_write()
        finally:
            gitsync._pending = old


# ---------------------------------------------------------------------------
# start_sync
# ---------------------------------------------------------------------------

class TestStartSync:
    @patch("asyncio.create_task")
    def test_starts_task(self, mock_create_task, tmp_path):
        (tmp_path / ".git").mkdir()
        old_pending = gitsync._pending
        old_task = gitsync._task
        try:
            mock_create_task.return_value = MagicMock()
            start_sync(tmp_path, debounce_s=2.0)
            assert gitsync._pending is not None
            mock_create_task.assert_called_once()
        finally:
            gitsync._pending = old_pending
            gitsync._task = old_task

    @patch("asyncio.create_task")
    def test_no_start_without_repo(self, mock_create_task, tmp_path):
        old_pending = gitsync._pending
        old_task = gitsync._task
        try:
            start_sync(tmp_path)
            mock_create_task.assert_not_called()
        finally:
            gitsync._pending = old_pending
            gitsync._task = old_task


# ---------------------------------------------------------------------------
# ensure_sync_running
# ---------------------------------------------------------------------------

class TestEnsureSyncRunning:
    @patch("asyncio.create_task")
    def test_starts_if_no_task(self, mock_create_task, tmp_path):
        (tmp_path / ".git").mkdir()
        old_pending = gitsync._pending
        old_task = gitsync._task
        try:
            gitsync._task = None
            mock_create_task.return_value = MagicMock()
            ensure_sync_running(tmp_path)
            mock_create_task.assert_called_once()
        finally:
            gitsync._pending = old_pending
            gitsync._task = old_task

    @patch("asyncio.create_task")
    def test_starts_if_task_done(self, mock_create_task, tmp_path):
        (tmp_path / ".git").mkdir()
        old_pending = gitsync._pending
        old_task = gitsync._task
        try:
            done_task = MagicMock()
            done_task.done.return_value = True
            gitsync._task = done_task
            mock_create_task.return_value = MagicMock()
            ensure_sync_running(tmp_path)
            mock_create_task.assert_called_once()
        finally:
            gitsync._pending = old_pending
            gitsync._task = old_task

    @patch("asyncio.create_task")
    def test_noop_if_running(self, mock_create_task, tmp_path):
        (tmp_path / ".git").mkdir()
        old_pending = gitsync._pending
        old_task = gitsync._task
        try:
            running_task = MagicMock()
            running_task.done.return_value = False
            gitsync._task = running_task
            ensure_sync_running(tmp_path)
            mock_create_task.assert_not_called()
        finally:
            gitsync._pending = old_pending
            gitsync._task = old_task

    @patch("asyncio.create_task")
    def test_noop_if_not_git_repo(self, mock_create_task, tmp_path):
        old_pending = gitsync._pending
        old_task = gitsync._task
        try:
            gitsync._task = None
            ensure_sync_running(tmp_path)
            mock_create_task.assert_not_called()
        finally:
            gitsync._pending = old_pending
            gitsync._task = old_task


# ---------------------------------------------------------------------------
# _sync_loop (async)
# ---------------------------------------------------------------------------

class TestSyncLoop:
    @pytest.mark.asyncio
    async def test_sync_loop_commits(self):
        old_pending = gitsync._pending
        old_last = gitsync._last_sync
        try:
            gitsync._pending = asyncio.Event()
            gitsync._pending.set()

            sync_result = SyncResult(commit="deadbeef", pushed=True)
            with patch("acai.orchestrator.gitsync.git_sync", return_value=sync_result):
                task = asyncio.create_task(
                    gitsync._sync_loop(Path("/fake"), debounce_s=0.01)
                )
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            gitsync._pending = old_pending
            gitsync._last_sync = old_last

    @pytest.mark.asyncio
    async def test_sync_loop_handles_exception(self):
        old_pending = gitsync._pending
        old_last = gitsync._last_sync
        try:
            gitsync._pending = asyncio.Event()
            gitsync._pending.set()

            with patch("acai.orchestrator.gitsync.git_sync", side_effect=RuntimeError("disk full")):
                task = asyncio.create_task(
                    gitsync._sync_loop(Path("/fake"), debounce_s=0.01)
                )
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            assert gitsync._last_sync is not None
            assert "disk full" in gitsync._last_sync.error
        finally:
            gitsync._pending = old_pending
            gitsync._last_sync = old_last

    @pytest.mark.asyncio
    async def test_sync_loop_push_error_logged(self):
        old_pending = gitsync._pending
        old_last = gitsync._last_sync
        try:
            gitsync._pending = asyncio.Event()
            gitsync._pending.set()

            sync_result = SyncResult(push_error="auth failed")
            with patch("acai.orchestrator.gitsync.git_sync", return_value=sync_result):
                task = asyncio.create_task(
                    gitsync._sync_loop(Path("/fake"), debounce_s=0.01)
                )
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            gitsync._pending = old_pending
            gitsync._last_sync = old_last

    @pytest.mark.asyncio
    async def test_sync_loop_error_logged(self):
        old_pending = gitsync._pending
        old_last = gitsync._last_sync
        try:
            gitsync._pending = asyncio.Event()
            gitsync._pending.set()

            sync_result = SyncResult(error="Not a git repository")
            with patch("acai.orchestrator.gitsync.git_sync", return_value=sync_result):
                task = asyncio.create_task(
                    gitsync._sync_loop(Path("/fake"), debounce_s=0.01)
                )
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            gitsync._pending = old_pending
            gitsync._last_sync = old_last
