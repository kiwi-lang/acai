"""Unit tests for acai.orchestrator.updater — background auto-updater."""

from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

import acai
from acai.orchestrator.updater import (
    _CACHE_TTL,
    _latest_cache,
    _run,
    _sse,
    _stream_subprocess,
    _upgrade_cmd,
    _version_tuple,
    check_and_update,
    do_upgrade,
    get_latest_version,
    needs_update,
    PYPI_URL,
    restart_service,
    RESTART_EXIT_CODE,
    start_update_loop,
    stream_upgrade,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the module-level version cache between tests."""
    original = _latest_cache.copy()
    _latest_cache["version"] = None
    _latest_cache["ts"] = 0.0
    yield
    _latest_cache.update(original)


# ---------------------------------------------------------------------------
# get_latest_version
# ---------------------------------------------------------------------------


class TestGetLatestVersion:
    def test_returns_version_from_pypi(self):
        payload = json.dumps({"info": {"version": "2.0.0"}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("acai.orchestrator.updater.urlopen", return_value=mock_resp):
            result = get_latest_version()

        assert result == "2.0.0"

    def test_returns_cached_version_within_ttl(self):
        import time

        _latest_cache["version"] = "1.5.0"
        _latest_cache["ts"] = time.monotonic()

        result = get_latest_version()
        assert result == "1.5.0"

    def test_refreshes_after_ttl_expires(self):
        import time

        _latest_cache["version"] = "1.5.0"
        _latest_cache["ts"] = time.monotonic() - _CACHE_TTL - 1

        payload = json.dumps({"info": {"version": "2.0.0"}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("acai.orchestrator.updater.urlopen", return_value=mock_resp):
            result = get_latest_version()

        assert result == "2.0.0"

    def test_returns_none_on_network_error(self):
        with patch("acai.orchestrator.updater.urlopen", side_effect=OSError("timeout")):
            result = get_latest_version()

        assert result is None

    def test_returns_none_on_json_decode_error(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("acai.orchestrator.updater.urlopen", return_value=mock_resp):
            result = get_latest_version()

        assert result is None

    def test_returns_none_on_missing_key(self):
        payload = json.dumps({"info": {}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("acai.orchestrator.updater.urlopen", return_value=mock_resp):
            result = get_latest_version()

        assert result is None


# ---------------------------------------------------------------------------
# _version_tuple
# ---------------------------------------------------------------------------


class TestVersionTuple:
    def test_simple_version(self):
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_single_digit(self):
        assert _version_tuple("5") == (5,)

    def test_two_part_version(self):
        assert _version_tuple("0.1") == (0, 1)

    def test_raises_on_non_numeric(self):
        with pytest.raises(ValueError):
            _version_tuple("1.2.beta")


# ---------------------------------------------------------------------------
# needs_update
# ---------------------------------------------------------------------------


class TestNeedsUpdate:
    def test_newer_version_returns_true(self):
        with patch.object(acai, "__version__", "1.0.0"):
            assert needs_update("2.0.0") is True

    def test_same_version_returns_false(self):
        with patch.object(acai, "__version__", "1.0.0"):
            assert needs_update("1.0.0") is False

    def test_older_version_returns_false(self):
        with patch.object(acai, "__version__", "2.0.0"):
            assert needs_update("1.0.0") is False

    def test_invalid_latest_version_returns_false(self):
        assert needs_update("not.a.version") is False

    def test_none_latest_raises(self):
        with pytest.raises(AttributeError):
            needs_update(None)

    def test_minor_bump_returns_true(self):
        with patch.object(acai, "__version__", "1.2.3"):
            assert needs_update("1.2.4") is True

    def test_major_bump_returns_true(self):
        with patch.object(acai, "__version__", "1.9.9"):
            assert needs_update("2.0.0") is True


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------


class TestRun:
    def test_successful_command(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "output"
        result.stderr = ""

        with patch("acai.orchestrator.updater.subprocess.run", return_value=result):
            ok, output = _run(["echo", "hello"])

        assert ok is True
        assert output == "output"

    def test_failed_command(self):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "error message"

        with patch("acai.orchestrator.updater.subprocess.run", return_value=result):
            ok, output = _run(["false"])

        assert ok is False
        assert output == "error message"

    def test_timeout(self):
        with patch(
            "acai.orchestrator.updater.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["sleep", "999"], timeout=5),
        ):
            ok, output = _run(["sleep", "999"], timeout=5)

        assert ok is False
        assert "timed out after 5s" in output

    def test_command_not_found(self):
        with patch(
            "acai.orchestrator.updater.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            ok, output = _run(["nonexistent_cmd"])

        assert ok is False
        assert "Command not found: nonexistent_cmd" in output

    def test_generic_exception(self):
        with patch(
            "acai.orchestrator.updater.subprocess.run",
            side_effect=PermissionError("access denied"),
        ):
            ok, output = _run(["cmd"])

        assert ok is False
        assert "PermissionError" in output
        assert "access denied" in output


# ---------------------------------------------------------------------------
# _upgrade_cmd
# ---------------------------------------------------------------------------


class TestUpgradeCmd:
    def test_uses_uv_when_available(self):
        with patch("acai.orchestrator.updater.shutil.which", return_value="/usr/bin/uv"):
            cmd = _upgrade_cmd()

        assert cmd[0] == "/usr/bin/uv"
        assert "pip" in cmd
        assert "--upgrade" in cmd
        assert "acai-swarm" in cmd

    def test_falls_back_to_pip(self):
        with patch("acai.orchestrator.updater.shutil.which", return_value=None):
            cmd = _upgrade_cmd()

        assert "-m" in cmd
        assert "pip" in cmd
        assert "--upgrade" in cmd
        assert "acai-swarm" in cmd


# ---------------------------------------------------------------------------
# do_upgrade
# ---------------------------------------------------------------------------


class TestDoUpgrade:
    def test_calls_run_with_upgrade_cmd(self):
        with (
            patch("acai.orchestrator.updater._upgrade_cmd", return_value=["uv", "pip", "install"]),
            patch("acai.orchestrator.updater._run", return_value=(True, "installed")) as mock_run,
        ):
            ok, output = do_upgrade()

        assert ok is True
        assert output == "installed"
        mock_run.assert_called_once_with(["uv", "pip", "install"], timeout=120)


# ---------------------------------------------------------------------------
# restart_service
# ---------------------------------------------------------------------------


class TestRestartService:
    def test_exits_with_restart_code(self):
        with patch("acai.orchestrator.updater.os._exit") as mock_exit:
            restart_service()

        mock_exit.assert_called_once_with(RESTART_EXIT_CODE)


# ---------------------------------------------------------------------------
# _sse
# ---------------------------------------------------------------------------


class TestSSE:
    def test_formats_event_correctly(self):
        result = _sse("log", "hello world")
        assert result == "event: log\ndata: hello world\n\n"

    def test_formats_done_event(self):
        result = _sse("done", '{"status": "ok"}')
        assert result == 'event: done\ndata: {"status": "ok"}\n\n'


# ---------------------------------------------------------------------------
# _stream_subprocess (async)
# ---------------------------------------------------------------------------


class TestStreamSubprocess:
    @pytest.mark.asyncio
    async def test_streams_lines_and_returns_success(self):
        mock_proc = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            side_effect=[b"line1\n", b"line2\n", b""]
        )
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            events = []
            async for event in _stream_subprocess(["cmd"]):
                events.append(event)

        assert events == ["line1", "line2", True]

    @pytest.mark.asyncio
    async def test_streams_lines_and_returns_failure(self):
        mock_proc = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            side_effect=[b"output\n", b""]
        )
        mock_proc.wait = AsyncMock(return_value=1)
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            events = []
            async for event in _stream_subprocess(["cmd"]):
                events.append(event)

        assert events == ["output", False]

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        mock_proc = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            events = []
            async for event in _stream_subprocess(["cmd"], timeout=5):
                events.append(event)

        assert "timed out after 5s" in events[0]
        assert events[-1] is False
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError(),
        ):
            events = []
            async for event in _stream_subprocess(["bad_cmd"]):
                events.append(event)

        assert "Command not found: bad_cmd" in events[0]
        assert events[-1] is False

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=PermissionError("denied"),
        ):
            events = []
            async for event in _stream_subprocess(["cmd"]):
                events.append(event)

        assert "PermissionError" in events[0]
        assert "denied" in events[0]
        assert events[-1] is False


# ---------------------------------------------------------------------------
# stream_upgrade (async)
# ---------------------------------------------------------------------------


class TestStreamUpgrade:
    @pytest.mark.asyncio
    async def test_successful_upgrade_yields_events_and_restarts(self):
        async def fake_stream(cmd):
            yield "Installing..."
            yield True

        with (
            patch.object(acai, "__version__", "1.0.0"),
            patch("acai.orchestrator.updater._upgrade_cmd", return_value=["uv", "pip", "install"]),
            patch("acai.orchestrator.updater._stream_subprocess", side_effect=fake_stream),
            patch("acai.orchestrator.updater.restart_service") as mock_restart,
        ):
            events = []
            async for event in stream_upgrade():
                events.append(event)

        raw = "".join(events)
        assert "Current version: 1.0.0" in raw
        assert "Installing latest version" in raw
        assert "Upgrade successful" in raw
        assert '"status": "updated"' in raw
        mock_restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_upgrade_yields_error(self):
        async def fake_stream(cmd):
            yield "error output"
            yield False

        with (
            patch.object(acai, "__version__", "1.0.0"),
            patch("acai.orchestrator.updater._upgrade_cmd", return_value=["pip", "install"]),
            patch("acai.orchestrator.updater._stream_subprocess", side_effect=fake_stream),
            patch("acai.orchestrator.updater.restart_service") as mock_restart,
        ):
            events = []
            async for event in stream_upgrade():
                events.append(event)

        raw = "".join(events)
        assert "ERROR: Upgrade failed" in raw
        assert '"status": "error"' in raw
        mock_restart.assert_not_called()


# ---------------------------------------------------------------------------
# check_and_update (async)
# ---------------------------------------------------------------------------


class TestCheckAndUpdate:
    @pytest.mark.asyncio
    async def test_pypi_unreachable(self):
        with patch("acai.orchestrator.updater.get_latest_version", return_value=None):
            result = await check_and_update()

        assert result["status"] == "error"
        assert "Could not reach PyPI" in result["message"]

    @pytest.mark.asyncio
    async def test_already_up_to_date(self):
        with (
            patch("acai.orchestrator.updater.get_latest_version", return_value="1.0.0"),
            patch("acai.orchestrator.updater.needs_update", return_value=False),
            patch.object(acai, "__version__", "1.0.0"),
        ):
            result = await check_and_update()

        assert result["status"] == "up-to-date"
        assert result["current"] == "1.0.0"
        assert result["latest"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_upgrade_fails(self):
        with (
            patch("acai.orchestrator.updater.get_latest_version", return_value="2.0.0"),
            patch("acai.orchestrator.updater.needs_update", return_value=True),
            patch("acai.orchestrator.updater.do_upgrade", return_value=(False, "pip error")),
        ):
            result = await check_and_update()

        assert result["status"] == "error"
        assert "Upgrade failed" in result["message"]
        assert result["output"] == "pip error"

    @pytest.mark.asyncio
    async def test_upgrade_success_restarts(self):
        with (
            patch("acai.orchestrator.updater.get_latest_version", return_value="2.0.0"),
            patch("acai.orchestrator.updater.needs_update", return_value=True),
            patch("acai.orchestrator.updater.do_upgrade", return_value=(True, "ok")),
            patch("acai.orchestrator.updater.restart_service") as mock_restart,
            patch.object(acai, "__version__", "1.0.0"),
        ):
            result = await check_and_update()

        mock_restart.assert_called_once()
        assert result["status"] == "updated"
        assert result["from"] == "1.0.0"
        assert result["to"] == "2.0.0"


# ---------------------------------------------------------------------------
# start_update_loop
# ---------------------------------------------------------------------------


class TestStartUpdateLoop:
    @pytest.mark.asyncio
    async def test_starts_task(self):
        with patch("acai.orchestrator.updater._update_loop", new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = None
            start_update_loop(interval_hours=12.0)

        import acai.orchestrator.updater as updater_mod
        assert updater_mod._update_task is not None
        updater_mod._update_task.cancel()
        try:
            await updater_mod._update_task
        except asyncio.CancelledError:
            pass
