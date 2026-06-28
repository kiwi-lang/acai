"""Tests for acai.cli.uber — uber command and create_app factory."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from acai.cli.uber import (
    UberArguments,
    Uber,
    create_app,
    _ENV_KEY,
    COMMANDS,
)


# ---------------------------------------------------------------------------
# UberArguments
# ---------------------------------------------------------------------------
class TestUberArguments:

    def test_defaults(self):
        args = UberArguments()
        assert args.host == "0.0.0.0"
        assert args.port == 5050
        assert args.prefix == "/agent"
        assert args.debug is False
        assert args.extern_llm is False


# ---------------------------------------------------------------------------
# create_app — all imports happen inside create_app, patch at source
# ---------------------------------------------------------------------------
class TestCreateApp:

    def test_missing_env_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_ENV_KEY, None)
            with pytest.raises(RuntimeError, match="_ACAI_UBER_ARGS"):
                create_app()

    def test_empty_env_raises(self):
        with patch.dict(os.environ, {_ENV_KEY: ""}):
            with pytest.raises(RuntimeError, match="_ACAI_UBER_ARGS"):
                create_app()

    @patch("threading.Thread")
    @patch("acai.ui.mount_ui")
    @patch("acai.worker.app.create_worker_router")
    @patch("acai.orchestrator.server.routes")
    @patch("acai.orchestrator.load_balancer.LoadBalancer")
    @patch("acai.worker.app._setup_telemetry")
    @patch("acai.cli.setup")
    def test_create_app_success(
        self, mock_setup, mock_telem, mock_lb_cls, mock_routes,
        mock_cwr, mock_mount, mock_thread,
    ):
        mock_config = MagicMock()
        active = MagicMock()
        active.model = "test-model"
        active.backend = "vllm"
        mock_config.active_provider.return_value = active
        mock_setup.return_value = (mock_config, MagicMock())

        mock_lb = MagicMock()
        mock_lb.register.return_value = "worker-1"
        mock_lb_cls.return_value = mock_lb

        mock_app = MagicMock()
        mock_socketio = MagicMock()
        mock_routes.return_value = (
            mock_app, mock_socketio, MagicMock(), MagicMock(),
            MagicMock(), mock_config, MagicMock(), mock_lb,
        )

        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.namespaces.return_value = []
        mock_router = MagicMock()
        mock_registry.router.return_value = mock_router
        mock_cwr.return_value = (MagicMock(), MagicMock(), mock_registry, MagicMock())

        uber_env = json.dumps({
            "config": None, "db": None, "verbose": False,
            "port": 5050, "prefix": "/agent", "extern_llm": False,
        })

        with patch.dict(os.environ, {_ENV_KEY: uber_env}):
            result = create_app()

        mock_socketio.make_asgi.assert_called_once_with(mock_app)
        mock_lb.register.assert_called_once()
        mock_thread.assert_called_once()

    @patch("threading.Thread")
    @patch("acai.ui.mount_ui")
    @patch("acai.worker.app.create_worker_router")
    @patch("acai.orchestrator.server.routes")
    @patch("acai.orchestrator.load_balancer.LoadBalancer")
    @patch("acai.worker.app._setup_telemetry")
    @patch("acai.cli.setup")
    def test_create_app_debug_flag(
        self, mock_setup, mock_telem, mock_lb_cls, mock_routes,
        mock_cwr, mock_mount, mock_thread,
    ):
        mock_config = MagicMock()
        active = MagicMock()
        active.model = "test-model"
        active.backend = "vllm"
        mock_config.active_provider.return_value = active
        mock_setup.return_value = (mock_config, MagicMock())

        mock_lb = MagicMock()
        mock_lb.register.return_value = "worker-1"
        mock_lb_cls.return_value = mock_lb

        mock_app = MagicMock()
        mock_socketio = MagicMock()
        mock_routes.return_value = (
            mock_app, mock_socketio, MagicMock(), MagicMock(),
            MagicMock(), mock_config, MagicMock(), mock_lb,
        )

        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.namespaces.return_value = []
        mock_registry.router.return_value = MagicMock()
        mock_cwr.return_value = (MagicMock(), MagicMock(), mock_registry, MagicMock())

        uber_env = json.dumps({
            "config": None, "db": None, "verbose": False,
            "debug": True,
            "port": 5050, "prefix": "/agent", "extern_llm": True,
        })

        with patch.dict(os.environ, {_ENV_KEY: uber_env}):
            create_app()

        assert mock_config.dump_rendered_request is True

    @patch("threading.Thread")
    @patch("acai.ui.mount_ui")
    @patch("acai.worker.app.create_worker_router")
    @patch("acai.orchestrator.server.routes")
    @patch("acai.orchestrator.load_balancer.LoadBalancer")
    @patch("acai.worker.app._setup_telemetry")
    @patch("acai.cli.setup")
    def test_create_app_custom_prefix(
        self, mock_setup, mock_telem, mock_lb_cls, mock_routes,
        mock_cwr, mock_mount, mock_thread,
    ):
        mock_config = MagicMock()
        active = MagicMock()
        active.model = "test-model"
        active.backend = "vllm"
        mock_config.active_provider.return_value = active
        mock_setup.return_value = (mock_config, MagicMock())

        mock_lb = MagicMock()
        mock_lb.register.return_value = "w-1"
        mock_lb_cls.return_value = mock_lb

        mock_app = MagicMock()
        mock_socketio = MagicMock()
        mock_routes.return_value = (
            mock_app, mock_socketio, MagicMock(), MagicMock(),
            MagicMock(), mock_config, MagicMock(), mock_lb,
        )

        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.namespaces.return_value = []
        mock_registry.router.return_value = MagicMock()
        mock_cwr.return_value = (MagicMock(), MagicMock(), mock_registry, MagicMock())

        uber_env = json.dumps({
            "config": None, "db": None, "verbose": False,
            "port": 5050, "prefix": "/custom", "extern_llm": False,
        })

        with patch.dict(os.environ, {_ENV_KEY: uber_env}):
            create_app()

        mock_routes.assert_called_once()
        call_kwargs = mock_routes.call_args
        assert call_kwargs.kwargs.get("prefix") == "/custom"


# ---------------------------------------------------------------------------
# Uber.execute — uvicorn is imported locally, patch at source
# ---------------------------------------------------------------------------
class TestUberExecute:

    def _make_args(self, **kwargs):
        defaults = dict(
            config=None, db=None, verbose=False,
            host="0.0.0.0", port=5050, prefix="/agent",
            debug=False, extern_llm=False,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    @patch("acai.cli.uber.setup")
    @patch("acai.cli.uber.create_app")
    @patch("uvicorn.run")
    def test_non_debug_mode(self, mock_uv_run, mock_create_app, mock_setup):
        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())
        mock_create_app.return_value = MagicMock()

        rc = Uber.execute(self._make_args())
        assert rc == 0
        mock_uv_run.assert_called_once()
        call_kwargs = mock_uv_run.call_args
        assert call_kwargs.kwargs.get("host") == "0.0.0.0"
        assert call_kwargs.kwargs.get("port") == 5050

    @patch("acai.cli.uber.setup")
    @patch("uvicorn.run")
    def test_debug_mode(self, mock_uv_run, mock_setup):
        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())

        rc = Uber.execute(self._make_args(debug=True))
        assert rc == 0
        assert mock_config.dump_rendered_request is True
        call_kwargs = mock_uv_run.call_args
        assert call_kwargs.kwargs.get("reload") is True
        assert call_kwargs.kwargs.get("factory") is True

    @patch("acai.cli.uber.setup")
    @patch("acai.cli.uber.create_app")
    @patch("uvicorn.run")
    def test_extern_llm_print(self, mock_uv_run, mock_create_app, mock_setup, capsys):
        mock_config = MagicMock()
        active = MagicMock()
        active.endpoint = "http://gpu-host:8000"
        mock_config.active_provider.return_value = active
        mock_setup.return_value = (mock_config, MagicMock())
        mock_create_app.return_value = MagicMock()

        Uber.execute(self._make_args(extern_llm=True))
        captured = capsys.readouterr().out
        assert "external LLM" in captured
        assert "gpu-host:8000" in captured

    @patch("acai.cli.uber.setup")
    @patch("acai.cli.uber.create_app")
    @patch("uvicorn.run")
    def test_env_var_set_non_debug(self, mock_uv_run, mock_create_app, mock_setup):
        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())
        mock_create_app.return_value = MagicMock()

        Uber.execute(self._make_args(port=7777))

        raw = os.environ.get(_ENV_KEY)
        assert raw is not None
        data = json.loads(raw)
        assert data["port"] == 7777
        assert data["debug"] is False

    @patch("acai.cli.uber.setup")
    @patch("uvicorn.run")
    def test_debug_env_var_set(self, mock_uv_run, mock_setup):
        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())

        Uber.execute(self._make_args(debug=True, port=6060))

        raw = os.environ.get(_ENV_KEY)
        data = json.loads(raw)
        assert data["debug"] is True
        assert data["port"] == 6060

    @patch("acai.cli.uber.setup")
    @patch("acai.cli.uber.create_app")
    @patch("uvicorn.run")
    def test_suppresses_socketio_logging(
        self, mock_uv_run, mock_create_app, mock_setup,
    ):
        import logging
        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())
        mock_create_app.return_value = MagicMock()

        Uber.execute(self._make_args())

        for name in ("engineio", "socketio", "socketio.server"):
            assert logging.getLogger(name).level >= logging.WARNING

    @patch("acai.cli.uber.setup")
    @patch("acai.cli.uber.create_app")
    @patch("uvicorn.run")
    def test_non_debug_no_extern_print(
        self, mock_uv_run, mock_create_app, mock_setup, capsys,
    ):
        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())
        mock_create_app.return_value = MagicMock()

        Uber.execute(self._make_args())
        captured = capsys.readouterr().out
        assert "external LLM" not in captured
        assert "Uber server" in captured


class TestUberCommandMeta:
    def test_commands_is_uber(self):
        assert COMMANDS is Uber

    def test_name(self):
        assert Uber.name == "uber"
