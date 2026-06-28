"""Tests for acai.cli.mcp — MCP tool server command."""

from __future__ import annotations

import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from acai.cli.mcp import (
    McpArguments,
    Mcp,
    COMMANDS,
)


# ---------------------------------------------------------------------------
# McpArguments
# ---------------------------------------------------------------------------
class TestMcpArguments:

    def test_defaults(self):
        args = McpArguments()
        assert args.host == "0.0.0.0"
        assert args.port == 9200


# ---------------------------------------------------------------------------
# Mcp.execute — all heavy imports are inside execute(), patch at source
# ---------------------------------------------------------------------------
class TestMcpExecute:

    def _make_args(self, **kwargs):
        defaults = dict(config=None, db=None, verbose=False, host="0.0.0.0", port=9200)
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    @patch("uvicorn.run")
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_happy_path(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run,
    ):
        mock_tool = MagicMock()
        mock_tool.qualified_name = "shell.exec"
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = [mock_tool]
        mock_registry.router.return_value = MagicMock()
        mock_discover.return_value = mock_registry

        mock_store = MagicMock()
        mock_skill_store_cls.return_value = mock_store

        rc = Mcp.execute(self._make_args())
        assert rc == 0
        mock_discover.assert_called_once()
        mock_meta.assert_called_once_with(mock_registry)
        mock_store.register_all.assert_called_once_with(mock_registry)
        mock_uvicorn_run.assert_called_once()

        call_kwargs = mock_uvicorn_run.call_args
        assert call_kwargs.kwargs.get("host") == "0.0.0.0"
        assert call_kwargs.kwargs.get("port") == 9200

    @patch("uvicorn.run")
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_verbose_sets_debug_logging(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run,
    ):
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.router.return_value = MagicMock()
        mock_discover.return_value = mock_registry
        mock_skill_store_cls.return_value = MagicMock()

        Mcp.execute(self._make_args(verbose=True))

    @patch("uvicorn.run")
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_custom_host_port(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run,
    ):
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.router.return_value = MagicMock()
        mock_discover.return_value = mock_registry
        mock_skill_store_cls.return_value = MagicMock()

        Mcp.execute(self._make_args(host="127.0.0.1", port=9300))

        call_kwargs = mock_uvicorn_run.call_args
        assert call_kwargs.kwargs.get("host") == "127.0.0.1"
        assert call_kwargs.kwargs.get("port") == 9300

    @patch("uvicorn.run", side_effect=KeyboardInterrupt)
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_keyboard_interrupt_handled(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run,
    ):
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.router.return_value = MagicMock()
        mock_discover.return_value = mock_registry
        mock_skill_store_cls.return_value = MagicMock()

        rc = Mcp.execute(self._make_args())
        assert rc == 0

    @patch("uvicorn.run")
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_workspace_env_used(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run,
    ):
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.router.return_value = MagicMock()
        mock_discover.return_value = mock_registry
        mock_skill_store_cls.return_value = MagicMock()

        with patch.dict(os.environ, {"ACAI_WORKSPACE": "/custom/ws"}):
            Mcp.execute(self._make_args())

        mock_skill_store_cls.assert_called_once()
        store_path = mock_skill_store_cls.call_args[0][0]
        assert "/custom/ws" in store_path

    @patch("uvicorn.run")
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_tool_router_mounted(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run,
    ):
        mock_router = MagicMock()
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = []
        mock_registry.router.return_value = mock_router
        mock_discover.return_value = mock_registry
        mock_skill_store_cls.return_value = MagicMock()

        Mcp.execute(self._make_args())

        mock_registry.router.assert_called_once_with(url_prefix="/tools")

    @patch("uvicorn.run")
    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_multiple_tools_logged(
        self, mock_discover, mock_meta, mock_skill_store_cls,
        mock_conf_skills, mock_uvicorn_run, caplog,
    ):
        tool_a = MagicMock()
        tool_a.qualified_name = "code.run"
        tool_b = MagicMock()
        tool_b.qualified_name = "git.status"
        mock_registry = MagicMock()
        mock_registry.all_tools.return_value = [tool_a, tool_b]
        mock_registry.router.return_value = MagicMock()
        mock_discover.return_value = mock_registry
        mock_skill_store_cls.return_value = MagicMock()

        with caplog.at_level(logging.INFO, logger="acai.cli.mcp"):
            Mcp.execute(self._make_args())

        assert any("tools=2" in r.message for r in caplog.records)


class TestMcpCommandMeta:
    def test_commands_is_mcp(self):
        assert COMMANDS is Mcp

    def test_name(self):
        assert Mcp.name == "mcp"
