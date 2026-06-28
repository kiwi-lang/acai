"""Tests for acai.cli misc commands — scratch, tools, orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call

import pytest


# ===========================================================================
# scratch.py — legacy model-serving server
# ===========================================================================

class TestScratchArguments:

    def test_defaults(self):
        from acai.cli.scratch import ServerArguments
        a = ServerArguments()
        assert a.host == "0.0.0.0"
        assert a.port == 5001
        assert a.debug is False


class TestScratchCommand:

    def test_command_name(self):
        from acai.cli.scratch import Server
        assert Server.name == "scratch"

    def test_command_has_arguments(self):
        from acai.cli.scratch import Server, ServerArguments
        assert Server.Arguments is ServerArguments

    @patch("acai.server.run.ACAI")
    def test_execute_starts_server(self, mock_acai_cls):
        from acai.cli.scratch import Server

        mock_server = MagicMock()
        mock_acai_cls.return_value = mock_server

        @dataclass
        class FakeArgs:
            host: str = "127.0.0.1"
            port: int = 9000
            debug: bool = True

        result = Server.execute(FakeArgs())

        assert result == 0
        mock_acai_cls.assert_called_once()
        mock_server.socketio.run.assert_called_once_with(
            mock_server.app,
            host="127.0.0.1",
            port=9000,
            debug=True,
        )

    @patch("acai.server.run.ACAI")
    def test_execute_default_args(self, mock_acai_cls):
        from acai.cli.scratch import Server

        mock_server = MagicMock()
        mock_acai_cls.return_value = mock_server

        @dataclass
        class FakeArgs:
            host: str = "0.0.0.0"
            port: int = 5001
            debug: bool = False

        result = Server.execute(FakeArgs())

        assert result == 0
        mock_server.socketio.run.assert_called_once_with(
            mock_server.app,
            host="0.0.0.0",
            port=5001,
            debug=False,
        )


# ===========================================================================
# tools.py — list all discovered tools
# ===========================================================================

class TestToolsArguments:

    def test_inherits_common_arguments(self):
        from acai.cli.tools import ToolsArguments
        from acai.cli import CommonArguments
        assert issubclass(ToolsArguments, CommonArguments)

    def test_defaults(self):
        from acai.cli.tools import ToolsArguments
        a = ToolsArguments()
        assert a.config is None
        assert a.db is None
        assert a.verbose is False


class TestToolsCommand:

    def test_command_name(self):
        from acai.cli.tools import Tools
        assert Tools.name == "tools"

    def test_command_has_arguments(self):
        from acai.cli.tools import Tools, ToolsArguments
        assert Tools.Arguments is ToolsArguments

    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_execute_lists_tools(self, mock_discover, mock_meta, mock_skill_store_cls, mock_skills, capsys, monkeypatch):
        from acai.cli.tools import Tools

        mock_registry = MagicMock()
        mock_discover.return_value = mock_registry

        mock_registry.namespaces.return_value = ["core", "skills"]

        tool1 = MagicMock()
        tool1.name = "search"
        tool1.description = "Search the web for information"

        tool2 = MagicMock()
        tool2.name = "run_code"
        tool2.description = "Execute code\nin a sandbox"

        tool3 = MagicMock()
        tool3.name = "no_desc"
        tool3.description = ""

        def tools_in(ns):
            if ns == "core":
                return [tool1, tool2]
            return [tool3]

        mock_registry.tools_in.side_effect = tools_in

        mock_store = MagicMock()
        mock_skill_store_cls.return_value = mock_store

        monkeypatch.setenv("ACAI_WORKSPACE", "/tmp/test-workspace")

        @dataclass
        class FakeArgs:
            config: str = None
            db: str = None
            verbose: bool = False

        result = Tools.execute(FakeArgs())

        assert result == 0
        mock_discover.assert_called_once()
        mock_meta.assert_called_once_with(mock_registry)
        mock_store.register_all.assert_called_once_with(mock_registry)

        captured = capsys.readouterr()
        assert "core" in captured.out
        assert "search" in captured.out
        assert "Search the web" in captured.out
        assert "skills" in captured.out

    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_execute_no_workspace_env(self, mock_discover, mock_meta, mock_skill_store_cls, mock_skills, monkeypatch):
        from acai.cli.tools import Tools

        mock_registry = MagicMock()
        mock_discover.return_value = mock_registry
        mock_registry.namespaces.return_value = []

        mock_store = MagicMock()
        mock_skill_store_cls.return_value = mock_store

        monkeypatch.delenv("ACAI_WORKSPACE", raising=False)

        @dataclass
        class FakeArgs:
            config: str = None
            db: str = None
            verbose: bool = False

        result = Tools.execute(FakeArgs())
        assert result == 0
        # Should default to "workspace" when env not set
        import os
        expected_path = os.path.join("workspace", "skills")
        mock_skill_store_cls.assert_called_once_with(expected_path)

    @patch("acai.tools.skills._configure")
    @patch("acai.orchestrator.skill_store.SkillStore")
    @patch("acai.tools.meta._configure")
    @patch("acai.orchestrator.tools.discover_tools")
    def test_execute_tool_with_multiline_description(self, mock_discover, mock_meta, mock_skill_store_cls, mock_skills, capsys, monkeypatch):
        from acai.cli.tools import Tools

        mock_registry = MagicMock()
        mock_discover.return_value = mock_registry
        mock_registry.namespaces.return_value = ["ns"]

        tool = MagicMock()
        tool.name = "multi"
        tool.description = "First line\nSecond line\nThird line"
        mock_registry.tools_in.return_value = [tool]

        mock_store = MagicMock()
        mock_skill_store_cls.return_value = mock_store
        monkeypatch.setenv("ACAI_WORKSPACE", "/tmp")

        @dataclass
        class FakeArgs:
            config: str = None
            db: str = None
            verbose: bool = False

        Tools.execute(FakeArgs())
        captured = capsys.readouterr()
        assert "First line" in captured.out
        assert "Second line" not in captured.out


# ===========================================================================
# orchestrator.py — run the orchestrator server
# ===========================================================================

class TestOrchestratorArguments:

    def test_defaults(self):
        from acai.cli.orchestrator import OrchestratorArguments
        a = OrchestratorArguments()
        assert a.host == "0.0.0.0"
        assert a.port == 5050
        assert a.prefix == "/agent"
        assert a.debug is False

    def test_inherits_common_arguments(self):
        from acai.cli.orchestrator import OrchestratorArguments
        from acai.cli import CommonArguments
        assert issubclass(OrchestratorArguments, CommonArguments)


class TestOrchestratorCommand:

    def test_command_name(self):
        from acai.cli.orchestrator import Orchestrator
        assert Orchestrator.name == "orchestrator"

    def test_command_has_arguments(self):
        from acai.cli.orchestrator import Orchestrator, OrchestratorArguments
        assert Orchestrator.Arguments is OrchestratorArguments

    @patch("acai.orchestrator.server.routes")
    @patch("fastapi.FastAPI")
    @patch("acai.cli.orchestrator.setup")
    def test_execute_starts_server(self, mock_setup, mock_fastapi_cls, mock_routes, capsys):
        from acai.cli.orchestrator import Orchestrator

        mock_config = MagicMock()
        mock_queue = MagicMock()
        mock_setup.return_value = (mock_config, mock_queue)

        mock_app = MagicMock()
        mock_fastapi_cls.return_value = mock_app

        mock_socketio = MagicMock()
        mock_routes.return_value = (mock_app, mock_socketio, MagicMock(), MagicMock(), MagicMock(), mock_config, MagicMock())

        @dataclass
        class FakeArgs:
            host: str = "127.0.0.1"
            port: int = 8080
            prefix: str = "/api"
            debug: bool = False
            config: str = None
            db: str = None
            verbose: bool = False

        result = Orchestrator.execute(FakeArgs())

        assert result == 0
        mock_setup.assert_called_once()
        mock_routes.assert_called_once_with(mock_app, mock_config, prefix="/api")
        mock_socketio.run.assert_called_once_with(
            mock_app, host="127.0.0.1", port=8080, debug=False,
        )

        captured = capsys.readouterr()
        assert "http://127.0.0.1:8080/api" in captured.out

    @patch("acai.orchestrator.server.routes")
    @patch("fastapi.FastAPI")
    @patch("acai.cli.orchestrator.setup")
    def test_execute_debug_mode(self, mock_setup, mock_fastapi_cls, mock_routes):
        from acai.cli.orchestrator import Orchestrator

        mock_config = MagicMock()
        mock_queue = MagicMock()
        mock_setup.return_value = (mock_config, mock_queue)

        mock_app = MagicMock()
        mock_fastapi_cls.return_value = mock_app

        mock_socketio = MagicMock()
        mock_routes.return_value = (mock_app, mock_socketio, MagicMock(), MagicMock(), MagicMock(), mock_config, MagicMock())

        @dataclass
        class FakeArgs:
            host: str = "0.0.0.0"
            port: int = 5050
            prefix: str = "/agent"
            debug: bool = True
            config: str = None
            db: str = None
            verbose: bool = False

        Orchestrator.execute(FakeArgs())

        assert mock_config.dump_rendered_request is True
        mock_socketio.run.assert_called_once_with(
            mock_app, host="0.0.0.0", port=5050, debug=True,
        )

    @patch("acai.orchestrator.server.routes")
    @patch("fastapi.FastAPI")
    @patch("acai.cli.orchestrator.setup")
    def test_execute_default_prefix(self, mock_setup, mock_fastapi_cls, mock_routes, capsys):
        from acai.cli.orchestrator import Orchestrator

        mock_config = MagicMock()
        mock_setup.return_value = (mock_config, MagicMock())
        mock_app = MagicMock()
        mock_fastapi_cls.return_value = mock_app

        mock_socketio = MagicMock()
        mock_routes.return_value = (mock_app, mock_socketio, MagicMock(), MagicMock(), MagicMock(), mock_config, MagicMock())

        @dataclass
        class FakeArgs:
            host: str = "0.0.0.0"
            port: int = 5050
            prefix: str = "/agent"
            debug: bool = False
            config: str = None
            db: str = None
            verbose: bool = False

        Orchestrator.execute(FakeArgs())
        mock_routes.assert_called_once_with(mock_app, mock_config, prefix="/agent")
