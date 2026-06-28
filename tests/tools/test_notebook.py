"""Unit tests for acai/tools/notebook.py."""

from __future__ import annotations

import json
import os

import pytest

from acai.tools.notebook import read_notebook, edit_notebook_cell


def _make_notebook(cells=None, nbformat=4):
    """Helper to create a minimal notebook dict."""
    if cells is None:
        cells = []
    return {
        "nbformat": nbformat,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": cells,
    }


def _code_cell(source, cell_id="cell-1"):
    return {"id": cell_id, "cell_type": "code", "source": source, "metadata": {}, "outputs": []}


def _markdown_cell(source, cell_id="md-1"):
    return {"id": cell_id, "cell_type": "markdown", "source": source, "metadata": {}}


@pytest.fixture
def notebook_path(tmp_path):
    """Create a notebook file with two cells."""
    nb = _make_notebook([
        _code_cell("print('hello')\n", cell_id="c1"),
        _markdown_cell("# Title\nSome text", cell_id="md1"),
    ])
    p = tmp_path / "test.ipynb"
    p.write_text(json.dumps(nb), encoding="utf-8")
    return str(p)


@pytest.fixture
def list_source_notebook(tmp_path):
    """Create a notebook where cell source is a list of strings."""
    nb = _make_notebook([
        _code_cell(["import os\n", "print(os.getcwd())\n"], cell_id="list-cell"),
    ])
    p = tmp_path / "list_source.ipynb"
    p.write_text(json.dumps(nb), encoding="utf-8")
    return str(p)


class TestReadNotebook:

    def test_reads_cell_summaries(self, notebook_path):
        result = json.loads(read_notebook(notebook_path))
        assert "cells" in result
        assert len(result["cells"]) == 2
        assert result["cells"][0]["index"] == 0
        assert result["cells"][0]["id"] == "c1"
        assert result["cells"][0]["cell_type"] == "code"
        assert "hello" in result["cells"][0]["source_preview"]

    def test_includes_path_and_nbformat(self, notebook_path):
        result = json.loads(read_notebook(notebook_path))
        assert result["path"] == notebook_path
        assert result["nbformat"] == 4

    def test_source_list_joined(self, list_source_notebook):
        result = json.loads(read_notebook(list_source_notebook))
        preview = result["cells"][0]["source_preview"]
        assert "import os" in preview
        assert "print(os.getcwd())" in preview

    def test_source_preview_truncated_at_120_chars(self, tmp_path):
        long_source = "x" * 200
        nb = _make_notebook([_code_cell(long_source, cell_id="long")])
        p = tmp_path / "long.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = json.loads(read_notebook(str(p)))
        assert len(result["cells"][0]["source_preview"]) == 120

    def test_source_preview_strips_and_collapses_newlines(self, tmp_path):
        source = "\n  hello\nworld\n"
        nb = _make_notebook([_code_cell(source, cell_id="ws")])
        p = tmp_path / "ws.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = json.loads(read_notebook(str(p)))
        preview = result["cells"][0]["source_preview"]
        assert "\n" not in preview
        assert "hello" in preview

    def test_missing_cell_id_returns_empty_string(self, tmp_path):
        cell = {"cell_type": "code", "source": "x = 1", "metadata": {}, "outputs": []}
        nb = _make_notebook([cell])
        p = tmp_path / "noid.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = json.loads(read_notebook(str(p)))
        assert result["cells"][0]["id"] == ""

    def test_empty_notebook(self, tmp_path):
        nb = _make_notebook([])
        p = tmp_path / "empty.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = json.loads(read_notebook(str(p)))
        assert result["cells"] == []

    def test_nonexistent_file_returns_error(self):
        result = json.loads(read_notebook("/nonexistent/notebook.ipynb"))
        assert "error" in result

    def test_permission_error_returns_error(self, tmp_path):
        p = tmp_path / "noperm.ipynb"
        p.write_text("{}")
        os.chmod(str(p), 0o000)
        try:
            result = json.loads(read_notebook(str(p)))
            assert "error" in result
        finally:
            os.chmod(str(p), 0o644)


class TestEditNotebookCell:

    def test_edit_by_index(self, notebook_path):
        result = json.loads(edit_notebook_cell(notebook_path, "print('updated')\n", cell_index=0))
        assert result["ok"] is True
        assert result["cell_index"] == 0

        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
        assert nb["cells"][0]["source"] == "print('updated')\n"

    def test_edit_by_cell_id(self, notebook_path):
        result = json.loads(edit_notebook_cell(notebook_path, "# New Title", cell_id="md1"))
        assert result["ok"] is True
        assert result["cell_index"] == 1

        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
        assert nb["cells"][1]["source"] == "# New Title"

    def test_edit_changes_cell_type(self, notebook_path):
        result = json.loads(edit_notebook_cell(
            notebook_path, "raw content", cell_index=0, cell_type="raw"
        ))
        assert result["ok"] is True

        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
        assert nb["cells"][0]["cell_type"] == "raw"

    def test_cell_type_not_changed_when_empty(self, notebook_path):
        edit_notebook_cell(notebook_path, "new code", cell_index=0)
        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
        assert nb["cells"][0]["cell_type"] == "code"

    def test_cell_id_not_found_returns_error(self, notebook_path):
        result = json.loads(edit_notebook_cell(notebook_path, "x", cell_id="nonexistent"))
        assert "error" in result
        assert "not found" in result["error"]

    def test_cell_index_out_of_range_negative(self, notebook_path):
        result = json.loads(edit_notebook_cell(notebook_path, "x", cell_index=-1))
        assert "error" in result
        assert "out of range" in result["error"]

    def test_cell_index_out_of_range_too_large(self, notebook_path):
        result = json.loads(edit_notebook_cell(notebook_path, "x", cell_index=99))
        assert "error" in result
        assert "out of range" in result["error"]

    def test_nonexistent_file_returns_error(self):
        result = json.loads(edit_notebook_cell("/no/such/file.ipynb", "x", cell_index=0))
        assert "error" in result

    def test_read_permission_error(self, tmp_path):
        p = tmp_path / "noperm.ipynb"
        p.write_text(json.dumps(_make_notebook([_code_cell("x")])))
        os.chmod(str(p), 0o000)
        try:
            result = json.loads(edit_notebook_cell(str(p), "y", cell_index=0))
            assert "error" in result
        finally:
            os.chmod(str(p), 0o644)

    def test_write_permission_error(self, tmp_path):
        nb = _make_notebook([_code_cell("x", cell_id="c")])
        p = tmp_path / "readonly.ipynb"
        p.write_text(json.dumps(nb))
        os.chmod(str(p), 0o444)
        try:
            result = json.loads(edit_notebook_cell(str(p), "y", cell_index=0))
            assert "error" in result
        finally:
            os.chmod(str(p), 0o644)

    def test_cell_id_takes_priority_over_index(self, notebook_path):
        result = json.loads(edit_notebook_cell(
            notebook_path, "edited", cell_index=0, cell_id="md1"
        ))
        assert result["ok"] is True
        assert result["cell_index"] == 1

    def test_output_is_valid_json_with_indent(self, notebook_path):
        edit_notebook_cell(notebook_path, "hello", cell_index=0)
        with open(notebook_path, encoding="utf-8") as f:
            content = f.read()
        assert "\n" in content
        json.loads(content)

    def test_edit_preserves_other_cells(self, notebook_path):
        edit_notebook_cell(notebook_path, "changed", cell_index=0)
        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
        assert nb["cells"][1]["source"] == "# Title\nSome text"
        assert nb["cells"][1]["id"] == "md1"
