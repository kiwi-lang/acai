"""Tests for acai.devserver.manager — process manager for dev services."""

from __future__ import annotations

import io
import os
import signal
import subprocess
import threading
import time
from collections import deque
from unittest.mock import MagicMock, Mock, mock_open, patch, call

import pytest

from acai.devserver.manager import (
    DEFAULT_RING_SIZE,
    STOP_TIMEOUT,
    ProcessManager,
    ServiceSpec,
    ServiceStatus,
    _RunningService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_spec():
    return ServiceSpec(name="web", command="python -m http.server", cwd="/tmp", env={"PORT": "8000"})


@pytest.fixture
def specs():
    return [
        ServiceSpec(name="api", command="uvicorn main:app", cwd="/app"),
        ServiceSpec(name="worker", command="celery -A tasks", cwd="/app", auto_start=False),
    ]


@pytest.fixture
def manager(specs):
    return ProcessManager(specs)


def _make_proc(pid=1234, poll_value=None, returncode=0):
    """Helper to build a mock Popen.

    We avoid spec=subprocess.Popen because in tests that patch
    subprocess.Popen, the class itself becomes a MagicMock and
    MagicMock(spec=<MagicMock>) raises InvalidSpecError.
    """
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = poll_value
    proc.returncode = returncode
    proc.stdout = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# ServiceStatus
# ---------------------------------------------------------------------------

class TestServiceStatus:
    def test_values(self):
        assert ServiceStatus.stopped == "stopped"
        assert ServiceStatus.running == "running"
        assert ServiceStatus.crashed == "crashed"

    def test_is_str_enum(self):
        assert isinstance(ServiceStatus.running, str)


# ---------------------------------------------------------------------------
# ServiceSpec
# ---------------------------------------------------------------------------

class TestServiceSpec:
    def test_defaults(self):
        spec = ServiceSpec(name="svc", command="echo hi")
        assert spec.cwd == "."
        assert spec.env == {}
        assert spec.auto_start is True

    def test_custom_values(self, simple_spec):
        assert simple_spec.name == "web"
        assert simple_spec.env == {"PORT": "8000"}
        assert simple_spec.cwd == "/tmp"


# ---------------------------------------------------------------------------
# _RunningService
# ---------------------------------------------------------------------------

class TestRunningService:
    def test_uptime_when_running(self):
        svc = _RunningService(spec=ServiceSpec(name="a", command="x"))
        svc.status = ServiceStatus.running
        svc.started_at = time.time() - 10.0
        uptime = svc.uptime()
        assert uptime is not None
        assert 9.0 <= uptime <= 12.0

    def test_uptime_when_stopped(self):
        svc = _RunningService(spec=ServiceSpec(name="a", command="x"))
        svc.status = ServiceStatus.stopped
        assert svc.uptime() is None

    def test_uptime_running_but_no_started_at(self):
        svc = _RunningService(spec=ServiceSpec(name="a", command="x"))
        svc.status = ServiceStatus.running
        svc.started_at = None
        assert svc.uptime() is None

    def test_default_log_ring_size(self):
        svc = _RunningService(spec=ServiceSpec(name="a", command="x"))
        assert svc._log.maxlen == DEFAULT_RING_SIZE


# ---------------------------------------------------------------------------
# ProcessManager.__init__
# ---------------------------------------------------------------------------

class TestProcessManagerInit:
    def test_registers_services(self, manager, specs):
        assert manager.service_names == ["api", "worker"]

    def test_empty_specs(self):
        pm = ProcessManager([])
        assert pm.service_names == []

    def test_custom_ring_size(self, specs):
        pm = ProcessManager(specs, ring_size=50)
        svc = pm.get_service("api")
        assert svc._log.maxlen == 50

    @patch("os.makedirs")
    def test_creates_log_dir(self, mock_mkdirs, specs):
        ProcessManager(specs, log_dir="/var/logs/dev")
        mock_mkdirs.assert_called_once_with("/var/logs/dev", exist_ok=True)

    def test_no_log_dir(self, specs):
        pm = ProcessManager(specs, log_dir=None)
        assert pm._log_dir is None


# ---------------------------------------------------------------------------
# ProcessManager.get_service
# ---------------------------------------------------------------------------

class TestGetService:
    def test_existing(self, manager):
        svc = manager.get_service("api")
        assert svc is not None
        assert svc.spec.name == "api"

    def test_missing(self, manager):
        assert manager.get_service("nonexistent") is None


# ---------------------------------------------------------------------------
# ProcessManager.start
# ---------------------------------------------------------------------------

class TestStart:
    @patch("subprocess.Popen")
    def test_start_success(self, mock_popen, manager):
        proc = _make_proc(pid=42)
        mock_popen.return_value = proc
        result = manager.start("api")
        assert result == {"ok": True, "pid": 42}
        svc = manager.get_service("api")
        assert svc.status == ServiceStatus.running
        assert svc.process is proc

    def test_start_unknown_service(self, manager):
        result = manager.start("ghost")
        assert "error" in result
        assert "unknown service" in result["error"]

    @patch("subprocess.Popen")
    def test_start_already_running(self, mock_popen, manager):
        proc = _make_proc(pid=42, poll_value=None)
        mock_popen.return_value = proc
        manager.start("api")

        result = manager.start("api")
        assert "error" in result
        assert "already running" in result["error"]
        assert "42" in result["error"]

    @patch("subprocess.Popen", side_effect=OSError("no such file"))
    def test_start_spawn_failure(self, mock_popen, manager):
        result = manager.start("api")
        assert "error" in result
        assert "no such file" in result["error"]
        svc = manager.get_service("api")
        assert svc.status == ServiceStatus.crashed
        assert svc.exit_code == -1
        assert any("failed to start" in line for line in svc._log)

    @patch("subprocess.Popen")
    def test_start_uses_merged_env(self, mock_popen, manager):
        proc = _make_proc()
        mock_popen.return_value = proc
        svc = manager.get_service("api")
        svc.spec.env = {"MY_VAR": "123"}
        manager.start("api")
        _, kwargs = mock_popen.call_args
        assert kwargs["env"]["MY_VAR"] == "123"
        assert "PATH" in kwargs["env"]

    @patch("subprocess.Popen")
    def test_start_uses_absolute_cwd(self, mock_popen, manager):
        proc = _make_proc()
        mock_popen.return_value = proc
        manager.start("api")
        _, kwargs = mock_popen.call_args
        assert os.path.isabs(kwargs["cwd"])

    @patch("subprocess.Popen")
    def test_start_spawns_reader_thread(self, mock_popen, manager):
        proc = _make_proc()
        mock_popen.return_value = proc
        manager.start("api")
        svc = manager.get_service("api")
        assert svc._reader_thread is not None
        assert svc._reader_thread.daemon is True

    @patch("subprocess.Popen")
    def test_start_clears_previous_exit_code(self, mock_popen, manager):
        svc = manager.get_service("api")
        svc.exit_code = 1
        proc = _make_proc()
        mock_popen.return_value = proc
        manager.start("api")
        assert svc.exit_code is None

    @patch("subprocess.Popen", side_effect=PermissionError("permission denied"))
    def test_start_permission_error(self, mock_popen, manager):
        result = manager.start("api")
        assert "error" in result
        assert "permission denied" in result["error"]


# ---------------------------------------------------------------------------
# ProcessManager.stop
# ---------------------------------------------------------------------------

class TestStop:
    def test_stop_unknown_service(self, manager):
        result = manager.stop("ghost")
        assert "error" in result
        assert "unknown service" in result["error"]

    def test_stop_already_stopped_no_process(self, manager):
        result = manager.stop("api")
        assert result["ok"] is True
        assert result["already_stopped"] is True

    @patch("os.getpgid", return_value=100)
    @patch("os.killpg")
    def test_stop_running_service(self, mock_killpg, mock_getpgid, manager):
        svc = manager.get_service("api")
        proc = _make_proc(pid=99)
        proc.poll.side_effect = [None, 0, 0, 0]
        proc.returncode = 0
        svc.process = proc
        svc.status = ServiceStatus.running

        result = manager.stop("api")
        assert result["ok"] is True
        assert result["exit_code"] == 0
        assert svc.status == ServiceStatus.stopped
        mock_killpg.assert_any_call(100, signal.SIGTERM)

    @patch("os.getpgid", return_value=100)
    @patch("os.killpg")
    def test_stop_process_already_exited_during_poll(self, mock_killpg, mock_getpgid, manager):
        svc = manager.get_service("api")
        proc = _make_proc(pid=99)
        proc.poll.return_value = 1
        proc.returncode = 1
        svc.process = proc
        svc.status = ServiceStatus.stopped
        result = manager.stop("api")
        assert result["ok"] is True
        assert result["already_stopped"] is True

    @patch("os.getpgid", side_effect=ProcessLookupError)
    def test_stop_process_vanished_before_getpgid(self, mock_getpgid, manager):
        svc = manager.get_service("api")
        proc = _make_proc(pid=99, poll_value=None)
        svc.process = proc
        svc.status = ServiceStatus.running
        with pytest.raises(ProcessLookupError):
            manager.stop("api")

    @patch("os.getpgid", return_value=100)
    @patch("os.killpg", side_effect=ProcessLookupError)
    def test_stop_sigterm_process_lookup_error(self, mock_killpg, mock_getpgid, manager):
        svc = manager.get_service("api")
        proc = _make_proc(pid=99)
        proc.poll.side_effect = [None, 0, 0, 0]
        proc.returncode = 0
        svc.process = proc
        svc.status = ServiceStatus.running

        result = manager.stop("api")
        assert result["ok"] is True

    @patch("os.getpgid", return_value=100)
    @patch("os.killpg")
    @patch("time.sleep")
    def test_stop_escalates_to_sigkill(self, mock_sleep, mock_killpg, mock_getpgid, manager):
        svc = manager.get_service("api")
        proc = _make_proc(pid=99)
        proc.poll.return_value = None
        proc.returncode = -9
        proc.wait.side_effect = lambda timeout: setattr(proc, 'returncode', -9)
        svc.process = proc
        svc.status = ServiceStatus.running

        with patch("time.time") as mock_time:
            mock_time.side_effect = [
                100.0,
                100.0 + STOP_TIMEOUT + 1,
                100.0 + STOP_TIMEOUT + 2,
            ]

            result = manager.stop("api")

        assert result["ok"] is True
        sigkill_calls = [c for c in mock_killpg.call_args_list if c == call(100, signal.SIGKILL)]
        assert len(sigkill_calls) >= 1
        assert any("[spawner] SIGKILL" in line for line in svc._log)


# ---------------------------------------------------------------------------
# ProcessManager.restart
# ---------------------------------------------------------------------------

class TestRestart:
    @patch.object(ProcessManager, "stop", return_value={"ok": True})
    @patch.object(ProcessManager, "start", return_value={"ok": True, "pid": 10})
    def test_restart_calls_stop_then_start(self, mock_start, mock_stop, manager):
        result = manager.restart("api")
        mock_stop.assert_called_once_with("api")
        mock_start.assert_called_once_with("api")
        assert result == {"ok": True, "pid": 10}


# ---------------------------------------------------------------------------
# ProcessManager.status / status_all
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_unknown_service(self, manager):
        assert manager.status("ghost") is None

    def test_status_stopped_service(self, manager):
        s = manager.status("api")
        assert s["name"] == "api"
        assert s["status"] == "stopped"
        assert s["pid"] is None

    @patch("subprocess.Popen")
    def test_status_running_service(self, mock_popen, manager):
        proc = _make_proc(pid=55, poll_value=None)
        mock_popen.return_value = proc
        manager.start("api")
        s = manager.status("api")
        assert s["status"] == "running"
        assert s["pid"] == 55
        assert s["uptime"] is not None

    def test_status_all(self, manager):
        results = manager.status_all()
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"api", "worker"}

    def test_status_includes_all_fields(self, manager):
        s = manager.status("api")
        for key in ("name", "command", "cwd", "status", "pid", "uptime", "exit_code", "auto_start"):
            assert key in s

    def test_status_reflects_crash(self, manager):
        svc = manager.get_service("api")
        proc = _make_proc(pid=10, poll_value=1)
        proc.returncode = 1
        svc.process = proc
        svc.status = ServiceStatus.running

        s = manager.status("api")
        assert s["status"] == "crashed"
        assert s["exit_code"] == 1


# ---------------------------------------------------------------------------
# ProcessManager.logs
# ---------------------------------------------------------------------------

class TestLogs:
    def test_logs_unknown_service(self, manager):
        assert manager.logs("ghost") is None

    def test_logs_empty(self, manager):
        lines = manager.logs("api")
        assert lines == []

    def test_logs_returns_all_when_fewer_than_tail(self, manager):
        svc = manager.get_service("api")
        svc._log.extend(["line1", "line2"])
        lines = manager.logs("api", tail=100)
        assert lines == ["line1", "line2"]

    def test_logs_tail_truncation(self, manager):
        svc = manager.get_service("api")
        svc._log.extend([f"line{i}" for i in range(50)])
        lines = manager.logs("api", tail=5)
        assert len(lines) == 5
        assert lines[-1] == "line49"

    def test_logs_tail_zero_returns_all(self, manager):
        svc = manager.get_service("api")
        svc._log.extend([f"line{i}" for i in range(10)])
        lines = manager.logs("api", tail=0)
        assert len(lines) == 10


# ---------------------------------------------------------------------------
# ProcessManager.start_all / stop_all
# ---------------------------------------------------------------------------

class TestStartAll:
    @patch.object(ProcessManager, "start")
    @patch("time.sleep")
    def test_start_all_respects_auto_start(self, mock_sleep, mock_start, manager):
        mock_start.return_value = {"ok": True, "pid": 1}
        manager.start_all()
        mock_start.assert_called_once_with("api")

    @patch.object(ProcessManager, "start")
    @patch("time.sleep")
    def test_start_all_logs_error(self, mock_sleep, mock_start, manager):
        mock_start.return_value = {"error": "boom"}
        manager.start_all()
        mock_start.assert_called_once_with("api")

    @patch.object(ProcessManager, "start")
    @patch("time.sleep")
    def test_start_all_detects_immediate_crash(self, mock_sleep, mock_start, manager):
        mock_start.return_value = {"ok": True, "pid": 1}
        svc = manager.get_service("api")
        proc = _make_proc(pid=1, poll_value=1)
        proc.returncode = 1
        svc.process = proc
        svc.status = ServiceStatus.running

        manager.start_all()
        assert svc.status == ServiceStatus.crashed

    @patch.object(ProcessManager, "stop")
    def test_stop_all(self, mock_stop, manager):
        manager.stop_all()
        assert mock_stop.call_count == 2
        mock_stop.assert_any_call("api")
        mock_stop.assert_any_call("worker")


# ---------------------------------------------------------------------------
# ProcessManager._refresh_status
# ---------------------------------------------------------------------------

class TestRefreshStatus:
    def test_no_process(self, manager):
        svc = manager.get_service("api")
        manager._refresh_status(svc)
        assert svc.status == ServiceStatus.stopped

    def test_running_process_still_alive(self, manager):
        svc = manager.get_service("api")
        svc.process = _make_proc(poll_value=None)
        svc.status = ServiceStatus.running
        manager._refresh_status(svc)
        assert svc.status == ServiceStatus.running

    def test_running_process_exited_cleanly(self, manager):
        svc = manager.get_service("api")
        proc = _make_proc(poll_value=0)
        svc.process = proc
        svc.status = ServiceStatus.running
        manager._refresh_status(svc)
        assert svc.status == ServiceStatus.stopped
        assert svc.exit_code == 0

    def test_running_process_exited_with_error(self, manager):
        svc = manager.get_service("api")
        proc = _make_proc(poll_value=1)
        svc.process = proc
        svc.status = ServiceStatus.running
        manager._refresh_status(svc)
        assert svc.status == ServiceStatus.crashed
        assert svc.exit_code == 1
        assert any("process exited" in line for line in svc._log)

    def test_already_stopped_not_re_checked(self, manager):
        svc = manager.get_service("api")
        svc.process = _make_proc(poll_value=0)
        svc.status = ServiceStatus.stopped
        manager._refresh_status(svc)
        assert svc.status == ServiceStatus.stopped


# ---------------------------------------------------------------------------
# ProcessManager._serialize_status
# ---------------------------------------------------------------------------

class TestSerializeStatus:
    def test_stopped_service(self, manager):
        svc = manager.get_service("api")
        result = manager._serialize_status(svc)
        assert result["pid"] is None
        assert result["uptime"] is None
        assert result["status"] == "stopped"

    def test_running_service_includes_pid(self, manager):
        svc = manager.get_service("api")
        svc.process = _make_proc(pid=77)
        svc.status = ServiceStatus.running
        svc.started_at = time.time()
        result = manager._serialize_status(svc)
        assert result["pid"] == 77
        assert result["uptime"] is not None

    def test_crashed_service_no_pid(self, manager):
        svc = manager.get_service("api")
        svc.process = _make_proc(pid=77)
        svc.status = ServiceStatus.crashed
        svc.exit_code = 137
        result = manager._serialize_status(svc)
        assert result["pid"] is None
        assert result["exit_code"] == 137


# ---------------------------------------------------------------------------
# ProcessManager._log_path
# ---------------------------------------------------------------------------

class TestLogPath:
    def test_with_log_dir(self):
        pm = ProcessManager([], log_dir="/tmp/logs")
        assert pm._log_path("api") == "/tmp/logs/api.log"

    def test_without_log_dir(self, manager):
        assert manager._log_path("api") is None


# ---------------------------------------------------------------------------
# ProcessManager._read_output
# ---------------------------------------------------------------------------

class TestReadOutput:
    def test_no_process(self, manager):
        svc = manager.get_service("api")
        svc.process = None
        manager._read_output(svc)

    def test_no_stdout(self, manager):
        svc = manager.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = None
        svc.process = proc
        manager._read_output(svc)

    def test_reads_lines_into_ring_buffer(self, manager):
        svc = manager.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = io.BytesIO(b"hello\nworld\n")
        svc.process = proc
        manager._read_output(svc)
        assert "hello" in list(svc._log)
        assert "world" in list(svc._log)

    def test_writes_to_log_file(self):
        pm = ProcessManager(
            [ServiceSpec(name="api", command="echo hi")],
            log_dir="/tmp/test-logs",
        )
        svc = pm.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = io.BytesIO(b"log line\n")
        svc.process = proc

        m = mock_open()
        with patch("builtins.open", m):
            pm._read_output(svc)
        m.assert_called_once_with("/tmp/test-logs/api.log", "a", encoding="utf-8")
        handle = m()
        handle.write.assert_called_with("log line\n")
        handle.flush.assert_called()

    def test_log_file_open_failure(self):
        pm = ProcessManager(
            [ServiceSpec(name="api", command="echo hi")],
            log_dir="/tmp/test-logs",
        )
        svc = pm.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = io.BytesIO(b"hello\n")
        svc.process = proc

        with patch("builtins.open", side_effect=OSError("disk full")):
            pm._read_output(svc)

        assert "hello" in list(svc._log)

    def test_handles_decode_errors(self, manager):
        svc = manager.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = io.BytesIO(b"\xff\xfe bad bytes\n")
        svc.process = proc
        manager._read_output(svc)
        assert len(svc._log) >= 1

    def test_handles_read_oserror(self, manager):
        svc = manager.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        stdout_mock = MagicMock()
        stdout_mock.readline.side_effect = OSError("broken pipe")
        proc.stdout = stdout_mock
        svc.process = proc
        manager._read_output(svc)

    def test_handles_read_value_error(self, manager):
        svc = manager.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        stdout_mock = MagicMock()
        stdout_mock.readline.side_effect = ValueError("I/O closed")
        proc.stdout = stdout_mock
        svc.process = proc
        manager._read_output(svc)

    def test_stdout_close_oserror(self, manager):
        svc = manager.get_service("api")
        proc = MagicMock(spec=subprocess.Popen)
        proc.stdout = io.BytesIO(b"ok\n")
        real_close = proc.stdout.close
        proc.stdout.close = MagicMock(side_effect=OSError("close fail"))
        svc.process = proc
        manager._read_output(svc)
        assert "ok" in list(svc._log)
