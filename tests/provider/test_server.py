"""Tests for acai.provider.server — LLMServer lifecycle, error handling, edge cases."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from unittest.mock import MagicMock, patch, mock_open

import pytest
import requests

from acai.provider.server import LLMServer, LLMServerError, _pid_alive, _kill_tree
from acai.provider.config import ProviderConfig, ModelConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    managed: bool = True,
    endpoint: str = "http://127.0.0.1:9123",
    **overrides,
) -> ProviderConfig:
    """Build a minimal ProviderConfig for testing."""
    models = [ModelConfig(name="test-model", slug="test-model")]
    cfg = ProviderConfig(
        name="test",
        backend="vllm",
        endpoint=endpoint,
        models=models,
        **overrides,
    )
    if not managed:
        cfg.launch_template = ""
        cfg.backend = "openai"
    return cfg


def _make_server(tmp_path, managed: bool = True, **config_kw) -> LLMServer:
    """Create an LLMServer pointing at a temp workspace."""
    config = _make_config(managed=managed, **config_kw)
    return LLMServer(config, workspace=str(tmp_path))


# ---------------------------------------------------------------------------
# Tests for _pid_alive
# ---------------------------------------------------------------------------


class TestPidAlive:

    def test_alive_current_process(self):
        assert _pid_alive(os.getpid()) is True

    def test_dead_pid(self):
        assert _pid_alive(99999999) is False

    @patch("os.kill", side_effect=PermissionError("no perms"))
    def test_permission_error_returns_false(self, _mock):
        assert _pid_alive(1) is False

    @patch("os.kill", side_effect=ProcessLookupError("no such"))
    def test_process_lookup_error_returns_false(self, _mock):
        assert _pid_alive(1) is False


# ---------------------------------------------------------------------------
# Tests for _kill_tree
# ---------------------------------------------------------------------------


class TestKillTree:

    @patch("os.killpg")
    @patch("os.getpgid", return_value=42)
    def test_kills_process_group_when_pgid_equals_pid(self, mock_pgid, mock_killpg):
        _kill_tree(42, signal.SIGTERM)
        mock_killpg.assert_called_once_with(42, signal.SIGTERM)

    @patch("os.kill")
    @patch("os.getpgid", return_value=1)
    def test_falls_back_to_kill_when_pgid_differs(self, mock_pgid, mock_kill):
        _kill_tree(42, signal.SIGTERM)
        mock_kill.assert_called_once_with(42, signal.SIGTERM)

    @patch("os.kill", side_effect=ProcessLookupError)
    @patch("os.getpgid", side_effect=ProcessLookupError)
    def test_handles_dead_process_gracefully(self, _pgid, _kill):
        _kill_tree(99999, signal.SIGTERM)

    @patch("os.kill", side_effect=PermissionError)
    @patch("os.getpgid", side_effect=PermissionError)
    def test_handles_permission_error_gracefully(self, _pgid, _kill):
        _kill_tree(1, signal.SIGTERM)

    @patch("os.kill", side_effect=ProcessLookupError)
    @patch("os.getpgid", side_effect=OSError("generic"))
    def test_handles_oserror_on_getpgid(self, _pgid, _kill):
        _kill_tree(42, signal.SIGTERM)


# ---------------------------------------------------------------------------
# Tests for LLMServer.__init__ and properties
# ---------------------------------------------------------------------------


class TestLLMServerInit:

    def test_workspace_paths_are_absolute(self, tmp_path):
        server = _make_server(tmp_path)
        assert os.path.isabs(server._ws)
        assert os.path.isabs(server._log_dir)
        assert os.path.isabs(server._lock_path)

    def test_pid_is_none_when_no_process(self, tmp_path):
        server = _make_server(tmp_path)
        assert server.pid is None

    def test_pid_returns_value_when_process_set(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        server.process = mock_proc
        assert server.pid == 12345

    def test_managed_reflects_config(self, tmp_path):
        server_managed = _make_server(tmp_path, managed=True)
        assert server_managed.managed is True

        server_unmanaged = _make_server(tmp_path, managed=False)
        assert server_unmanaged.managed is False


# ---------------------------------------------------------------------------
# Tests for is_running
# ---------------------------------------------------------------------------


class TestIsRunning:

    def test_running_when_process_alive(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        server.process = mock_proc
        assert server.is_running() is True

    def test_not_running_when_process_exited(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        server.process = mock_proc
        assert server.is_running() is False

    def test_running_via_lock_file_other_process(self, tmp_path):
        server = _make_server(tmp_path)
        server.process = None
        os.makedirs(os.path.dirname(server._lock_path), exist_ok=True)
        with open(server._lock_path, "w") as f:
            f.write(str(os.getpid()))
        assert server.is_running() is True

    def test_not_running_lock_file_dead_pid(self, tmp_path):
        server = _make_server(tmp_path)
        server.process = None
        os.makedirs(os.path.dirname(server._lock_path), exist_ok=True)
        with open(server._lock_path, "w") as f:
            f.write("99999999")
        assert server.is_running() is False

    def test_not_running_no_process_no_lock(self, tmp_path):
        server = _make_server(tmp_path)
        assert server.is_running() is False


# ---------------------------------------------------------------------------
# Tests for log access
# ---------------------------------------------------------------------------


class TestLogAccess:

    def test_latest_log_path_returns_current_if_exists(self, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "test.log")
        with open(log_path, "w") as f:
            f.write("hello")
        server._current_log_path = log_path
        assert server.latest_log_path() == log_path

    def test_latest_log_path_returns_none_when_current_missing(self, tmp_path):
        server = _make_server(tmp_path)
        server._current_log_path = "/nonexistent/path.log"
        assert server.latest_log_path() is None

    def test_latest_log_path_finds_sorted_log_files(self, tmp_path):
        server = _make_server(tmp_path)
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "llm_server_20240101_000000.log").write_text("old")
        (log_dir / "llm_server_20240102_000000.log").write_text("new")
        assert server.latest_log_path().endswith("20240102_000000.log")

    def test_latest_log_path_returns_none_when_no_logs(self, tmp_path):
        server = _make_server(tmp_path)
        (tmp_path / "logs").mkdir()
        assert server.latest_log_path() is None

    def test_read_log_no_file(self, tmp_path):
        server = _make_server(tmp_path)
        assert server.read_log() == "(no log file found)"

    def test_read_log_tail_lines(self, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "test.log")
        lines = [f"line {i}\n" for i in range(300)]
        with open(log_path, "w") as f:
            f.writelines(lines)
        server._current_log_path = log_path
        result = server.read_log(tail=5)
        assert "line 295\n" in result
        assert "line 299\n" in result
        assert "line 0\n" not in result

    def test_read_log_oserror(self, tmp_path):
        server = _make_server(tmp_path)
        server._current_log_path = str(tmp_path / "exists.log")
        (tmp_path / "exists.log").write_text("data")
        with patch("builtins.open", side_effect=OSError("disk full")):
            result = server.read_log()
        assert "(error reading log: disk full)" in result


# ---------------------------------------------------------------------------
# Tests for lock file management
# ---------------------------------------------------------------------------


class TestLockFile:

    def test_write_and_read_lock(self, tmp_path):
        server = _make_server(tmp_path)
        server._write_lock(42)
        assert server._read_lock() == 42

    def test_read_lock_returns_none_when_missing(self, tmp_path):
        server = _make_server(tmp_path)
        assert server._read_lock() is None

    def test_read_lock_returns_none_on_invalid_content(self, tmp_path):
        server = _make_server(tmp_path)
        os.makedirs(os.path.dirname(server._lock_path), exist_ok=True)
        with open(server._lock_path, "w") as f:
            f.write("not-a-number")
        assert server._read_lock() is None

    def test_read_lock_returns_none_on_empty_file(self, tmp_path):
        server = _make_server(tmp_path)
        os.makedirs(os.path.dirname(server._lock_path), exist_ok=True)
        with open(server._lock_path, "w") as f:
            f.write("")
        assert server._read_lock() is None

    def test_clear_lock_removes_file(self, tmp_path):
        server = _make_server(tmp_path)
        server._write_lock(100)
        assert os.path.exists(server._lock_path)
        server._clear_lock()
        assert not os.path.exists(server._lock_path)

    def test_clear_lock_no_error_when_missing(self, tmp_path):
        server = _make_server(tmp_path)
        server._clear_lock()


# ---------------------------------------------------------------------------
# Tests for _kill_stale_lock
# ---------------------------------------------------------------------------


class TestKillStaleLock:

    def test_no_lock_file_does_nothing(self, tmp_path):
        server = _make_server(tmp_path)
        server._kill_stale_lock()

    @patch("acai.provider.server._pid_alive", return_value=False)
    def test_dead_pid_clears_lock(self, mock_alive, tmp_path):
        server = _make_server(tmp_path)
        server._write_lock(99999)
        server._kill_stale_lock()
        assert not os.path.exists(server._lock_path)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server._kill_tree")
    @patch("acai.provider.server._pid_alive", side_effect=[True, False])
    def test_kills_alive_stale_process(self, mock_alive, mock_kill, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        server._write_lock(12345)
        server._kill_stale_lock()
        mock_kill.assert_called_once_with(12345, signal.SIGTERM)
        assert not os.path.exists(server._lock_path)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server._kill_tree")
    @patch("acai.provider.server._pid_alive", return_value=True)
    def test_sends_sigkill_if_process_does_not_die(self, mock_alive, mock_kill, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        server._write_lock(12345)
        server._kill_stale_lock()
        calls = mock_kill.call_args_list
        assert any(c[0][1] == signal.SIGKILL for c in calls)


# ---------------------------------------------------------------------------
# Tests for start_process
# ---------------------------------------------------------------------------


class TestStartProcess:

    def test_skips_if_already_running(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        server.process = mock_proc
        server.start_process()
        assert server.process is mock_proc

    def test_skips_if_not_managed(self, tmp_path):
        server = _make_server(tmp_path, managed=False)
        server.start_process()
        assert server.process is None

    @patch("acai.provider.server.LLMServer._kill_stale_lock")
    @patch("subprocess.Popen")
    @patch("acai.orchestrator.env.build_env", return_value={"PATH": "/usr/bin"})
    def test_starts_process_and_writes_lock(self, mock_env, mock_popen, mock_stale, tmp_path):
        mock_proc = MagicMock()
        mock_proc.pid = 54321
        mock_popen.return_value = mock_proc

        server = _make_server(tmp_path)
        server.start_process()

        assert server.process is mock_proc
        assert server._read_lock() == 54321
        assert server._current_log_path is not None
        mock_popen.assert_called_once()

        if server._log_file:
            server._log_file.close()


# ---------------------------------------------------------------------------
# Tests for stop
# ---------------------------------------------------------------------------


class TestStop:

    def test_stop_does_nothing_when_no_process(self, tmp_path):
        server = _make_server(tmp_path)
        server.stop()
        assert server.process is None

    @patch("acai.provider.server._kill_tree")
    def test_stop_kills_process_and_clears_lock(self, mock_kill, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 111
        mock_proc.wait.return_value = 0
        server.process = mock_proc
        server._write_lock(111)

        log_file = MagicMock()
        server._log_file = log_file

        server.stop()

        mock_kill.assert_called_with(111, signal.SIGTERM)
        assert server.process is None
        assert not os.path.exists(server._lock_path)
        log_file.close.assert_called_once()

    @patch("acai.provider.server._kill_tree")
    def test_stop_sends_sigkill_on_timeout(self, mock_kill, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 222
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("cmd", 30),
            subprocess.TimeoutExpired("cmd", 5),
        ]
        server.process = mock_proc
        server._log_file = MagicMock()

        server.stop(timeout=30)

        kill_calls = mock_kill.call_args_list
        assert kill_calls[0][0] == (222, signal.SIGTERM)
        assert kill_calls[1][0] == (222, signal.SIGKILL)
        assert server.process is None

    @patch("acai.provider.server._kill_tree")
    def test_stop_handles_sigkill_timeout_gracefully(self, mock_kill, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 333
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 5)
        server.process = mock_proc
        server._log_file = None

        server.stop(timeout=1)
        assert server.process is None


# ---------------------------------------------------------------------------
# Tests for _check_alive
# ---------------------------------------------------------------------------


class TestCheckAlive:

    def test_does_nothing_when_no_process(self, tmp_path):
        server = _make_server(tmp_path)
        server._check_alive()

    def test_does_nothing_when_process_still_running(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        server.process = mock_proc
        server._check_alive()

    def test_raises_with_clear_message_on_crash(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 137
        mock_proc.returncode = 137
        server.process = mock_proc
        server._write_lock(999)

        log_path = str(tmp_path / "crash.log")
        with open(log_path, "w") as f:
            f.write("CUDA error: out of memory\n")
        server._current_log_path = log_path

        with pytest.raises(LLMServerError, match="exit code 137"):
            server._check_alive()

        assert server.process is None
        assert not os.path.exists(server._lock_path)

    def test_closes_log_file_on_crash(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        server.process = mock_proc

        log_file = MagicMock()
        server._log_file = log_file

        with pytest.raises(LLMServerError):
            server._check_alive()

        log_file.close.assert_called_once()
        assert server._log_file is None

    def test_error_message_includes_log_tail(self, tmp_path):
        server = _make_server(tmp_path)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 2
        mock_proc.returncode = 2
        server.process = mock_proc

        log_path = str(tmp_path / "server.log")
        with open(log_path, "w") as f:
            f.write("RuntimeError: model too large for GPU\n")
        server._current_log_path = log_path

        with pytest.raises(LLMServerError) as exc_info:
            server._check_alive()
        assert "model too large for GPU" in str(exc_info.value)
        assert "exit code 2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests for is_healthy
# ---------------------------------------------------------------------------


class TestIsHealthy:

    @patch("requests.get")
    def test_healthy_status_200(self, mock_get, tmp_path):
        server = _make_server(tmp_path)
        mock_get.return_value = MagicMock(status_code=200)
        assert server.is_healthy() is True
        mock_get.assert_called_once_with("http://127.0.0.1:9123/health", timeout=2)

    @patch("requests.get")
    def test_healthy_status_499_still_healthy(self, mock_get, tmp_path):
        server = _make_server(tmp_path)
        mock_get.return_value = MagicMock(status_code=499)
        assert server.is_healthy() is True

    @patch("requests.get")
    def test_unhealthy_status_500(self, mock_get, tmp_path):
        server = _make_server(tmp_path)
        mock_get.return_value = MagicMock(status_code=500)
        assert server.is_healthy() is False

    @patch("requests.get")
    def test_unhealthy_status_503(self, mock_get, tmp_path):
        server = _make_server(tmp_path)
        mock_get.return_value = MagicMock(status_code=503)
        assert server.is_healthy() is False

    @patch("requests.get", side_effect=requests.ConnectionError("refused"))
    def test_connection_error_returns_false(self, _mock, tmp_path):
        server = _make_server(tmp_path)
        assert server.is_healthy() is False

    @patch("requests.get", side_effect=requests.Timeout("timed out"))
    def test_timeout_returns_false(self, _mock, tmp_path):
        server = _make_server(tmp_path)
        assert server.is_healthy() is False


# ---------------------------------------------------------------------------
# Tests for wait_healthy
# ---------------------------------------------------------------------------


class TestWaitHealthy:

    @patch("acai.provider.server.time.sleep")
    @patch("requests.get")
    @patch("acai.provider.server.LLMServer._wait_model_loaded")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_succeeds_on_first_try(self, mock_alive, mock_loaded, mock_get, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        mock_get.return_value = MagicMock(status_code=200)
        server.wait_healthy(retries=5)
        mock_loaded.assert_called_once()

    @patch("acai.provider.server.time.sleep")
    @patch("requests.get")
    @patch("acai.provider.server.LLMServer._wait_model_loaded")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_retries_on_connection_error_then_succeeds(self, mock_alive, mock_loaded, mock_get, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        mock_get.side_effect = [
            requests.ConnectionError("refused"),
            requests.ConnectionError("refused"),
            MagicMock(status_code=200),
        ]
        server.wait_healthy(retries=5)

    @patch("acai.provider.server.time.sleep")
    @patch("requests.get", side_effect=requests.ConnectionError("refused"))
    @patch("acai.provider.server.LLMServer._wait_model_loaded")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_raises_after_retries_exhausted(self, mock_alive, mock_loaded, mock_get, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        server._current_log_path = "/tmp/test.log"
        with pytest.raises(LLMServerError, match="not healthy after checkpoint load"):
            server.wait_healthy(retries=3, interval=0.01)

    @patch("acai.provider.server.time.sleep")
    @patch("requests.get")
    @patch("acai.provider.server.LLMServer._wait_model_loaded")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_error_message_includes_log_path(self, mock_alive, mock_loaded, mock_get, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        server._current_log_path = "/var/log/llm_server.log"
        mock_get.side_effect = requests.ConnectionError("nope")
        with pytest.raises(LLMServerError) as exc_info:
            server.wait_healthy(retries=2, interval=0.01)
        assert "/var/log/llm_server.log" in str(exc_info.value)

    @patch("acai.provider.server.time.sleep")
    @patch("requests.get")
    @patch("acai.provider.server.LLMServer._wait_model_loaded")
    @patch("acai.provider.server.LLMServer._check_alive", side_effect=LLMServerError("crashed"))
    def test_propagates_check_alive_error(self, mock_alive, mock_loaded, mock_get, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        with pytest.raises(LLMServerError, match="crashed"):
            server.wait_healthy(retries=5)


# ---------------------------------------------------------------------------
# Tests for _wait_model_loaded
# ---------------------------------------------------------------------------


class TestWaitModelLoaded:

    def test_returns_immediately_when_no_log_path(self, tmp_path):
        server = _make_server(tmp_path)
        server._current_log_path = None
        server._wait_model_loaded(timeout=1)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_detects_shard_progress_100_percent(self, mock_alive, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write("Loading safetensors checkpoint shards: 100% Completed | 4/4\n")
        server._current_log_path = log_path
        server._wait_model_loaded(timeout=10)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_detects_weights_loaded_message(self, mock_alive, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write("INFO Loading weights took 45.3 seconds\n")
        server._current_log_path = log_path
        server._wait_model_loaded(timeout=10)

    @patch("acai.provider.server.time.monotonic")
    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_times_out_when_no_progress(self, mock_alive, mock_sleep, mock_time, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write("nothing relevant here\n")
        server._current_log_path = log_path
        mock_time.side_effect = [0, 0, 100]
        server._wait_model_loaded(timeout=5)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_handles_oserror_reading_log(self, mock_alive, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write("")
        server._current_log_path = log_path

        call_count = [0]
        original_open = open

        def flaky_open(path, *args, **kwargs):
            if path == log_path:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise OSError("disk error")
                with original_open(path, "w") as wf:
                    wf.write("Loading weights took 10s\n")
                return original_open(path, *args, **kwargs)
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=flaky_open):
            server._wait_model_loaded(timeout=30)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive", side_effect=LLMServerError("died"))
    def test_propagates_process_crash(self, mock_alive, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write("loading...\n")
        server._current_log_path = log_path
        with pytest.raises(LLMServerError, match="died"):
            server._wait_model_loaded(timeout=30)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_partial_shard_progress_updates(self, mock_alive, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write(
                "Loading safetensors checkpoint shards:  50% Completed | 2/4\n"
                "Loading safetensors checkpoint shards: 100% Completed | 4/4\n"
            )
        server._current_log_path = log_path
        server._wait_model_loaded(timeout=10)

    @patch("acai.provider.server.time.sleep")
    @patch("acai.provider.server.LLMServer._check_alive")
    def test_weights_done_with_info_prefix(self, mock_alive, mock_sleep, tmp_path):
        server = _make_server(tmp_path)
        log_path = str(tmp_path / "loading.log")
        with open(log_path, "w") as f:
            f.write("2024-01-01 INFO Loading weights took 123.4 seconds\n")
        server._current_log_path = log_path
        server._wait_model_loaded(timeout=10)


# ---------------------------------------------------------------------------
# Tests for start (combined start_process + wait_healthy)
# ---------------------------------------------------------------------------


class TestStart:

    @patch("acai.provider.server.LLMServer.wait_healthy")
    @patch("acai.provider.server.LLMServer.start_process")
    def test_calls_start_process_then_wait_healthy(self, mock_start, mock_wait, tmp_path):
        server = _make_server(tmp_path)
        server.start()
        mock_start.assert_called_once()
        mock_wait.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for LLMServerError
# ---------------------------------------------------------------------------


class TestLLMServerError:

    def test_is_runtime_error_subclass(self):
        err = LLMServerError("something went wrong")
        assert isinstance(err, RuntimeError)
        assert str(err) == "something went wrong"

    def test_can_be_caught_as_runtime_error(self):
        with pytest.raises(RuntimeError, match="server failed"):
            raise LLMServerError("server failed")
