"""Tests for acai.cli.dev — Dev parent command and dev serve subcommand."""

from __future__ import annotations

import signal
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Dev parent command (acai/cli/dev/__init__.py)
# ---------------------------------------------------------------------------

class TestDevParentCommand:

    def test_import_commands(self):
        from acai.cli.dev import COMMANDS
        assert COMMANDS is not None

    def test_dev_name(self):
        from acai.cli.dev import Dev
        assert Dev.name == "dev"

    def test_module_returns_correct_module(self):
        from acai.cli.dev import Dev
        mod = Dev.module()
        import acai.cli.dev as expected
        assert mod is expected


# ---------------------------------------------------------------------------
# Serve command helpers (acai/cli/dev/serve.py)
# ---------------------------------------------------------------------------

class TestGetLocalIp:

    def test_returns_ip_string(self):
        from acai.cli.dev.serve import _get_local_ip
        ip = _get_local_ip()
        parts = ip.split(".")
        assert len(parts) == 4

    def test_returns_fallback_on_socket_error(self):
        from acai.cli.dev.serve import _get_local_ip
        with patch("acai.cli.dev.serve.socket.socket") as mock_sock:
            mock_sock.return_value.connect.side_effect = OSError("no network")
            ip = _get_local_ip()
        assert ip == "127.0.0.1"


class TestPackageDir:

    def test_returns_string(self):
        from acai.cli.dev.serve import _package_dir
        d = _package_dir()
        assert isinstance(d, str)
        assert len(d) > 0

    def test_is_parent_of_acai_package(self):
        from acai.cli.dev.serve import _package_dir
        import os
        d = _package_dir()
        assert os.path.isdir(os.path.join(d, "acai"))


class TestDefaultServices:

    def test_returns_list_of_dicts(self):
        from acai.cli.dev.serve import _default_services
        result = _default_services()
        assert isinstance(result, list)
        assert len(result) == 3

    def test_service_names(self):
        from acai.cli.dev.serve import _default_services
        names = [s["name"] for s in _default_services()]
        assert "frontend" in names
        assert "backend" in names
        assert "vllm" in names

    def test_commands_have_port_placeholder(self):
        from acai.cli.dev.serve import _default_services
        for s in _default_services():
            assert "{port}" in s["command"]


class TestServeArguments:

    def test_defaults(self):
        from acai.cli.dev.serve import ServeArguments
        a = ServeArguments()
        assert a.host == "0.0.0.0"
        assert a.port == 0


# ---------------------------------------------------------------------------
# Serve.execute (the big integration path)
# ---------------------------------------------------------------------------

class TestServeExecute:

    def _make_args(self, port=0, host="0.0.0.0", config=None, db=None, verbose=False):
        class FakeArgs:
            pass
        args = FakeArgs()
        args.port = port
        args.host = host
        args.config = config
        args.db = db
        args.verbose = verbose
        return args

    @patch("uvicorn.run")
    @patch("time.sleep")
    @patch("acai.cli.dev.serve.signal.signal")
    @patch("acai.cli.dev.serve._get_local_ip", return_value="192.168.1.10")
    def test_execute_happy_path(self, mock_ip, mock_signal, mock_sleep, mock_uvicorn_run, tmp_path):
        from acai.cli.dev.serve import Serve

        mock_manager_cls = MagicMock()
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager
        mock_manager.status.return_value = {"status": "running", "pid": 1234}
        mock_manager.logs.return_value = []

        mock_create_app = MagicMock(return_value=MagicMock())

        mock_config = MagicMock()
        mock_config.dev.port = 5100
        mock_config.dev.services = []
        mock_config.workspace = str(tmp_path)

        mock_queue = MagicMock()

        with patch("acai.cli.dev.serve.setup", return_value=(mock_config, mock_queue)), \
             patch("acai.devserver.manager.ProcessManager", mock_manager_cls), \
             patch("acai.devserver.app.create_dev_app", mock_create_app), \
             patch("acai.cli.dev.serve._default_services", return_value=[
                 {"name": "frontend", "command": "npm run dev --port {port}", "cwd": "/tmp", "auto_start": True},
             ]):
            args = self._make_args(port=5100)
            result = Serve.execute(args)

        assert result == 0
        mock_manager.start_all.assert_called_once()
        mock_uvicorn_run.assert_called_once()

    @patch("uvicorn.run")
    @patch("time.sleep")
    @patch("acai.cli.dev.serve.signal.signal")
    @patch("acai.cli.dev.serve._get_local_ip", return_value="10.0.0.5")
    def test_execute_uses_config_port(self, mock_ip, mock_signal, mock_sleep, mock_uvicorn_run, tmp_path):
        from acai.cli.dev.serve import Serve

        mock_manager_cls = MagicMock()
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager
        mock_manager.status.return_value = {"status": "stopped", "pid": None}

        mock_config = MagicMock()
        mock_config.dev.port = 6000
        mock_config.dev.services = []
        mock_config.workspace = str(tmp_path)

        with patch("acai.cli.dev.serve.setup", return_value=(mock_config, MagicMock())), \
             patch("acai.devserver.manager.ProcessManager", mock_manager_cls), \
             patch("acai.devserver.app.create_dev_app", MagicMock()), \
             patch("acai.cli.dev.serve._default_services", return_value=[
                 {"name": "svc", "command": "cmd --port {port}", "cwd": "/tmp", "auto_start": True},
             ]):
            args = self._make_args(port=0)
            Serve.execute(args)

        # uvicorn should be called with port 6000 (from config)
        call_kwargs = mock_uvicorn_run.call_args
        assert call_kwargs[1].get("port") == 6000 or call_kwargs[0][2] == 6000

    @patch("uvicorn.run")
    @patch("time.sleep")
    @patch("acai.cli.dev.serve.signal.signal")
    @patch("acai.cli.dev.serve._get_local_ip", return_value="10.0.0.5")
    def test_execute_crashed_service_prints_logs(self, mock_ip, mock_signal, mock_sleep, mock_uvicorn_run, tmp_path, capsys):
        from acai.cli.dev.serve import Serve

        mock_manager_cls = MagicMock()
        mock_manager = MagicMock()
        mock_manager_cls.return_value = mock_manager
        mock_manager.status.return_value = {"status": "crashed", "pid": None}
        mock_manager.logs.return_value = ["error line 1", "error line 2"]

        mock_config = MagicMock()
        mock_config.dev.port = 5100
        mock_config.dev.services = []
        mock_config.workspace = str(tmp_path)

        with patch("acai.cli.dev.serve.setup", return_value=(mock_config, MagicMock())), \
             patch("acai.devserver.manager.ProcessManager", mock_manager_cls), \
             patch("acai.devserver.app.create_dev_app", MagicMock()), \
             patch("acai.cli.dev.serve._default_services", return_value=[
                 {"name": "badservice", "command": "failing {port}", "cwd": "/tmp", "auto_start": True},
             ]):
            args = self._make_args(port=5100)
            Serve.execute(args)

        captured = capsys.readouterr()
        assert "error line 1" in captured.out

    def test_serve_command_name(self):
        from acai.cli.dev.serve import Serve
        assert Serve.name == "serve"

    def test_serve_command_has_arguments_class(self):
        from acai.cli.dev.serve import Serve, ServeArguments
        assert Serve.Arguments is ServeArguments
