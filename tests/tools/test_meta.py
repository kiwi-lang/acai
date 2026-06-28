"""Unit tests for acai/tools/meta.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from acai.tools.meta import (
    _configure,
    _get_registry,
    list_namespaces,
    list_tools,
    search_tools,
)


def _make_mock_registry(tools=None, namespaces=None):
    """Create a mock ToolRegistry."""
    registry = MagicMock()
    registry.namespaces.return_value = namespaces or ["shell", "filesystem", "git"]

    mock_tools = tools or [
        MagicMock(
            qualified_name="shell_run",
            namespace="shell",
            description="Run a shell command.",
            permissions=("execute",),
            resources=("shell:execute",),
        ),
        MagicMock(
            qualified_name="filesystem_read_file",
            namespace="filesystem",
            description="Read a file from the filesystem.",
            permissions=("read",),
            resources=("files:read",),
        ),
        MagicMock(
            qualified_name="git_status",
            namespace="git",
            description="Show the git status.",
            permissions=("read",),
            resources=("git:read",),
        ),
    ]
    registry.all_tools.return_value = mock_tools
    registry.tools_in.return_value = [t for t in mock_tools if t.namespace == "shell"]
    return registry


class TestConfigure:
    def test_configure_sets_registry(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            mock_reg = MagicMock()
            _configure(mock_reg)
            assert meta_mod._registry is mock_reg
        finally:
            meta_mod._registry = old


class TestGetRegistry:
    def test_returns_configured_registry(self):
        import acai.tools.meta as meta_mod
        mock_reg = MagicMock()
        old = meta_mod._registry
        try:
            meta_mod._registry = mock_reg
            assert _get_registry() is mock_reg
        finally:
            meta_mod._registry = old

    def test_discovers_if_none(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = None
            with patch("acai.orchestrator.tools.discover_tools") as mock_discover:
                mock_discover.return_value = MagicMock()
                result = _get_registry()
                mock_discover.assert_called_once()
                assert result is mock_discover.return_value
        finally:
            meta_mod._registry = old


class TestListNamespaces:
    def test_returns_namespaces(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(list_namespaces())
            assert result["namespaces"] == ["shell", "filesystem", "git"]
        finally:
            meta_mod._registry = old


class TestListTools:
    def test_list_all(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(list_tools())
            assert result["count"] == 3
            names = [t["qualified_name"] for t in result["tools"]]
            assert "shell_run" in names
            assert "filesystem_read_file" in names

        finally:
            meta_mod._registry = old

    def test_list_by_namespace(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(list_tools(namespace="shell"))
            assert result["count"] == 1
            assert result["tools"][0]["qualified_name"] == "shell_run"
        finally:
            meta_mod._registry = old


class TestSearchTools:
    def test_search_by_name(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(search_tools("shell"))
            assert result["count"] == 1
            assert result["tools"][0]["qualified_name"] == "shell_run"
        finally:
            meta_mod._registry = old

    def test_search_by_description(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(search_tools("filesystem"))
            assert result["count"] == 1
            assert result["tools"][0]["qualified_name"] == "filesystem_read_file"
        finally:
            meta_mod._registry = old

    def test_regex_mode(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(search_tools("regex:^shell"))
            assert result["count"] == 1
        finally:
            meta_mod._registry = old

    def test_invalid_regex(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(search_tools("regex:[invalid"))
            assert "error" in result
        finally:
            meta_mod._registry = old

    def test_max_results(self):
        import acai.tools.meta as meta_mod
        old = meta_mod._registry
        try:
            meta_mod._registry = _make_mock_registry()
            result = json.loads(search_tools("", max_results=2))
            assert result["count"] == 2
        finally:
            meta_mod._registry = old
