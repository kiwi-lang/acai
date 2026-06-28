"""Tests for acai.cli.new — New parent command and plugin scaffolding subcommand."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# New parent command (acai/cli/new/__init__.py)
# ---------------------------------------------------------------------------

class TestNewParentCommand:

    def test_import_commands(self):
        from acai.cli.new import COMMANDS
        assert COMMANDS is not None

    def test_new_name(self):
        from acai.cli.new import New
        assert New.name == "new"

    def test_module_returns_correct_module(self):
        from acai.cli.new import New
        mod = New.module()
        import acai.cli.new as expected
        assert mod is expected


# ---------------------------------------------------------------------------
# Plugin command (acai/cli/new/plugin.py)
# ---------------------------------------------------------------------------

class TestPluginArgs:

    def test_defaults(self):
        from acai.cli.new.plugin import PluginArgs
        a = PluginArgs(name="test-plugin")
        assert a.name == "test-plugin"
        assert a.dest == ""


class TestPluginCommand:

    def test_command_name(self):
        from acai.cli.new.plugin import Plugin
        assert Plugin.name == "plugin"

    def test_command_has_arguments(self):
        from acai.cli.new.plugin import Plugin, PluginArgs
        assert Plugin.Arguments is PluginArgs

    @patch("acai.cli.scaffold.scaffold_plugin")
    def test_execute_success(self, mock_scaffold, capsys):
        from acai.cli.new.plugin import Plugin

        mock_scaffold.return_value = "/tmp/acai-plugin-my-tools"

        @dataclass
        class FakeArgs:
            name: str = "my-tools"
            dest: str = ""

        result = Plugin.execute(FakeArgs())

        assert result == 0
        mock_scaffold.assert_called_once_with("my-tools", dest=None)
        captured = capsys.readouterr()
        assert "Created plugin at /tmp/acai-plugin-my-tools" in captured.out
        assert "Next steps:" in captured.out

    @patch("acai.cli.scaffold.scaffold_plugin")
    def test_execute_with_dest(self, mock_scaffold, capsys):
        from acai.cli.new.plugin import Plugin

        mock_scaffold.return_value = "/custom/path/acai-plugin-foo"

        @dataclass
        class FakeArgs:
            name: str = "foo"
            dest: str = "/custom/path"

        result = Plugin.execute(FakeArgs())

        assert result == 0
        mock_scaffold.assert_called_once_with("foo", dest="/custom/path")

    @patch("acai.cli.scaffold.scaffold_plugin")
    def test_execute_file_exists_error(self, mock_scaffold, capsys):
        from acai.cli.new.plugin import Plugin

        mock_scaffold.side_effect = FileExistsError("Directory already exists: /tmp/acai-plugin-dup")

        @dataclass
        class FakeArgs:
            name: str = "dup"
            dest: str = ""

        result = Plugin.execute(FakeArgs())

        assert result == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.out
        assert "already exists" in captured.out

    @patch("acai.cli.scaffold.scaffold_plugin")
    def test_execute_empty_dest_passes_none(self, mock_scaffold):
        from acai.cli.new.plugin import Plugin

        mock_scaffold.return_value = "/some/path"

        @dataclass
        class FakeArgs:
            name: str = "x"
            dest: str = ""

        Plugin.execute(FakeArgs())
        mock_scaffold.assert_called_once_with("x", dest=None)

    @patch("acai.cli.scaffold.scaffold_plugin")
    def test_execute_non_empty_dest_passes_value(self, mock_scaffold):
        from acai.cli.new.plugin import Plugin

        mock_scaffold.return_value = "/dest/acai-plugin-y"

        @dataclass
        class FakeArgs:
            name: str = "y"
            dest: str = "/dest"

        Plugin.execute(FakeArgs())
        mock_scaffold.assert_called_once_with("y", dest="/dest")
