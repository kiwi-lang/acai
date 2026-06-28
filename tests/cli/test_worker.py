"""Tests for acai.cli.worker — worker command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from acai.cli.worker import (
    WorkerArguments,
    Worker,
    COMMANDS,
)


# ---------------------------------------------------------------------------
# WorkerArguments
# ---------------------------------------------------------------------------
class TestWorkerArguments:

    def test_defaults(self):
        args = WorkerArguments()
        assert args.host is None
        assert args.port is None
        assert args.orchestrator_url is None


# ---------------------------------------------------------------------------
# Worker.execute — create_worker_app imported locally, patch at source
# ---------------------------------------------------------------------------
class TestWorkerExecute:

    def _make_args(self, **kwargs):
        defaults = dict(
            config=None, db=None, verbose=False,
            host=None, port=None, orchestrator_url=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    @patch("acai.cli.worker.setup")
    @patch("acai.worker.app.create_worker_app")
    @patch("acai.cli.worker.threading.Thread")
    def test_happy_path(self, mock_thread, mock_create, mock_setup, capsys):
        mock_config = MagicMock()
        mock_config.worker.host = "0.0.0.0"
        mock_config.worker.port = 8080
        mock_config.worker.orchestrator_url = "http://orch:5050"
        mock_setup.return_value = (mock_config, MagicMock())

        mock_app = MagicMock()
        mock_sio = MagicMock()
        mock_poller = MagicMock()
        mock_llm = MagicMock()
        mock_create.return_value = (mock_app, mock_sio, mock_poller, mock_llm)

        rc = Worker.execute(self._make_args())
        assert rc == 0
        mock_sio.run.assert_called_once_with(
            mock_app, host="0.0.0.0", port=8080,
        )

        thread_instance = mock_thread.return_value
        thread_instance.start.assert_called_once()

        captured = capsys.readouterr().out
        assert "0.0.0.0:8080" in captured
        assert "http://orch:5050" in captured

    @patch("acai.cli.worker.setup")
    @patch("acai.worker.app.create_worker_app")
    @patch("acai.cli.worker.threading.Thread")
    def test_host_override(self, mock_thread, mock_create, mock_setup):
        mock_config = MagicMock()
        mock_config.worker.host = "0.0.0.0"
        mock_config.worker.port = 8080
        mock_config.worker.orchestrator_url = "http://orch:5050"
        mock_setup.return_value = (mock_config, MagicMock())

        mock_sio = MagicMock()
        mock_create.return_value = (MagicMock(), mock_sio, MagicMock(), MagicMock())

        Worker.execute(self._make_args(host="127.0.0.1"))
        assert mock_config.worker.host == "127.0.0.1"

    @patch("acai.cli.worker.setup")
    @patch("acai.worker.app.create_worker_app")
    @patch("acai.cli.worker.threading.Thread")
    def test_port_override(self, mock_thread, mock_create, mock_setup):
        mock_config = MagicMock()
        mock_config.worker.host = "0.0.0.0"
        mock_config.worker.port = 8080
        mock_config.worker.orchestrator_url = "http://orch:5050"
        mock_setup.return_value = (mock_config, MagicMock())

        mock_sio = MagicMock()
        mock_create.return_value = (MagicMock(), mock_sio, MagicMock(), MagicMock())

        Worker.execute(self._make_args(port=9999))
        assert mock_config.worker.port == 9999

    @patch("acai.cli.worker.setup")
    @patch("acai.worker.app.create_worker_app")
    @patch("acai.cli.worker.threading.Thread")
    def test_orchestrator_url_override(self, mock_thread, mock_create, mock_setup):
        mock_config = MagicMock()
        mock_config.worker.host = "0.0.0.0"
        mock_config.worker.port = 8080
        mock_config.worker.orchestrator_url = "http://old:5050"
        mock_setup.return_value = (mock_config, MagicMock())

        mock_sio = MagicMock()
        mock_create.return_value = (MagicMock(), mock_sio, MagicMock(), MagicMock())

        Worker.execute(self._make_args(orchestrator_url="http://new:6060"))
        assert mock_config.worker.orchestrator_url == "http://new:6060"

    @patch("acai.cli.worker.setup")
    @patch("acai.worker.app.create_worker_app")
    @patch("acai.cli.worker.threading.Thread")
    def test_all_overrides(self, mock_thread, mock_create, mock_setup):
        mock_config = MagicMock()
        mock_config.worker.host = "0.0.0.0"
        mock_config.worker.port = 8080
        mock_config.worker.orchestrator_url = "http://orch:5050"
        mock_setup.return_value = (mock_config, MagicMock())

        mock_sio = MagicMock()
        mock_create.return_value = (MagicMock(), mock_sio, MagicMock(), MagicMock())

        Worker.execute(self._make_args(
            host="10.0.0.1", port=7070,
            orchestrator_url="http://custom:3000",
        ))
        assert mock_config.worker.host == "10.0.0.1"
        assert mock_config.worker.port == 7070
        assert mock_config.worker.orchestrator_url == "http://custom:3000"

    @patch("acai.cli.worker.setup")
    @patch("acai.worker.app.create_worker_app")
    @patch("acai.cli.worker.threading.Thread")
    def test_poller_started_as_daemon(self, mock_thread, mock_create, mock_setup):
        mock_config = MagicMock()
        mock_config.worker.host = "0.0.0.0"
        mock_config.worker.port = 8080
        mock_config.worker.orchestrator_url = "http://orch:5050"
        mock_setup.return_value = (mock_config, MagicMock())

        mock_poller = MagicMock()
        mock_create.return_value = (MagicMock(), MagicMock(), mock_poller, MagicMock())

        Worker.execute(self._make_args())
        mock_thread.assert_called_once_with(
            target=mock_poller.run, daemon=True, name="poller",
        )


class TestWorkerCommandMeta:
    def test_commands_is_worker(self):
        assert COMMANDS is Worker

    def test_name(self):
        assert Worker.name == "worker"
