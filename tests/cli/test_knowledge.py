"""Tests for acai.cli.knowledge — helper functions and command logic."""

from __future__ import annotations

import pytest

from acai.cli.knowledge import fmt_time, truncate


class TestTruncate:

    def test_short_text_unchanged(self):
        assert truncate("hello", 60) == "hello"

    def test_long_text_truncated(self):
        text = "x" * 100
        result = truncate(text, 20)
        assert len(result) == 21  # 20 chars + ellipsis
        assert result.endswith("…")

    def test_newlines_replaced(self):
        text = "line1\nline2\nline3"
        result = truncate(text, 60)
        assert "\n" not in result
        assert "line1 line2 line3" == result

    def test_exact_width(self):
        text = "x" * 60
        result = truncate(text, 60)
        assert result == text  # no truncation needed

    def test_empty_string(self):
        assert truncate("", 60) == ""

    def test_whitespace_stripped(self):
        assert truncate("  hello  ", 60) == "hello"


class TestFmtTime:

    def test_zero_returns_dash(self):
        assert fmt_time(0) == "—"

    def test_none_returns_dash(self):
        assert fmt_time(None) == "—"

    def test_valid_timestamp(self):
        result = fmt_time(1672531200.0)  # 2023-01-01 00:00 UTC
        assert "2023" in result
        assert "01-01" in result

    def test_format_includes_time(self):
        result = fmt_time(1672531200.0)
        assert ":" in result  # has HH:MM


# ── Fixtures & helpers for command tests ──────────────────────────

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch
from io import StringIO
import sys

from acai.knowledge.models import KnowledgeDoc, Facets


def _make_doc(
    subject="python",
    subsubject="asyncio",
    title="generators",
    content="Some content about generators",
    updated_at=1700000000.0,
    tags=None,
    facets=None,
):
    return KnowledgeDoc(
        subject=subject,
        subsubject=subsubject,
        title=title,
        content=content,
        updated_at=updated_at,
        tags=tags or [],
        facets=facets or Facets(),
    )


@dataclass
class _FakeArgs:
    """Minimal args object that satisfies all CLI commands."""
    config: str = None
    db: str = None
    verbose: bool = False
    path: str = None
    query: str = None
    subject: str = ""
    subsubject: str = ""
    full: bool = False
    force: bool = False
    set_tags: str = ""
    set_facet: str = ""


# ── Delete command ────────────────────────────────────────────────

class TestDeleteCommand:

    @patch("acai.cli.knowledge.delete.get_store")
    def test_missing_path_returns_error(self, mock_gs, capsys):
        args = _FakeArgs(path=None)
        rc = self._execute(args)
        assert rc == 1
        assert "error: --path is required" in capsys.readouterr().err

    @patch("acai.cli.knowledge.delete.get_store")
    def test_empty_path_returns_error(self, mock_gs, capsys):
        args = _FakeArgs(path="")
        rc = self._execute(args)
        assert rc == 1

    @patch("acai.cli.knowledge.delete.get_store")
    def test_document_not_found(self, mock_gs, capsys):
        mock_gs.return_value.get_by_path.return_value = None
        args = _FakeArgs(path="a/b/c")
        rc = self._execute(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    @patch("acai.cli.knowledge.delete.get_store")
    def test_force_delete_skips_confirmation(self, mock_gs, capsys):
        doc = _make_doc()
        store = mock_gs.return_value
        store.get_by_path.return_value = doc

        args = _FakeArgs(path="python/asyncio/generators", force=True)
        rc = self._execute(args)
        assert rc == 0
        store.delete_by_path.assert_called_once_with("python/asyncio/generators")
        assert "Deleted" in capsys.readouterr().out

    @patch("builtins.input", return_value="yes")
    @patch("acai.cli.knowledge.delete.get_store")
    def test_confirmed_delete(self, mock_gs, mock_input, capsys):
        doc = _make_doc()
        store = mock_gs.return_value
        store.get_by_path.return_value = doc

        args = _FakeArgs(path="python/asyncio/generators")
        rc = self._execute(args)
        assert rc == 0
        store.delete_by_path.assert_called_once()

    @patch("builtins.input", return_value="no")
    @patch("acai.cli.knowledge.delete.get_store")
    def test_cancelled_delete(self, mock_gs, mock_input, capsys):
        doc = _make_doc()
        store = mock_gs.return_value
        store.get_by_path.return_value = doc

        args = _FakeArgs(path="python/asyncio/generators")
        rc = self._execute(args)
        assert rc == 0
        store.delete_by_path.assert_not_called()
        assert "Cancelled" in capsys.readouterr().out

    @patch("builtins.input", return_value="  YES  ")
    @patch("acai.cli.knowledge.delete.get_store")
    def test_confirmation_case_insensitive_with_whitespace(self, mock_gs, mock_input):
        store = mock_gs.return_value
        store.get_by_path.return_value = _make_doc()

        args = _FakeArgs(path="python/asyncio/generators")
        rc = self._execute(args)
        assert rc == 0
        store.delete_by_path.assert_called_once()

    @staticmethod
    def _execute(args):
        from acai.cli.knowledge.delete import Delete
        return Delete.execute(args)


# ── List command ──────────────────────────────────────────────────

class TestListCommand:

    @patch("acai.cli.knowledge.list.get_store")
    def test_no_documents(self, mock_gs, capsys):
        mock_gs.return_value.list.return_value = []
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        assert "No documents found" in capsys.readouterr().out

    @patch("acai.cli.knowledge.list.get_store")
    def test_lists_documents_with_header(self, mock_gs, capsys):
        docs = [_make_doc(), _make_doc(title="coroutines", updated_at=1700000100.0)]
        mock_gs.return_value.list.return_value = docs
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Path" in out
        assert "python/asyncio/generators" in out
        assert "python/asyncio/coroutines" in out
        assert "2 document(s)" in out

    @patch("acai.cli.knowledge.list.get_store")
    def test_full_flag_shows_content(self, mock_gs, capsys):
        doc = _make_doc(content="line1\nline2")
        mock_gs.return_value.list.return_value = [doc]
        args = _FakeArgs(full=True)
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "│ line1" in out
        assert "│ line2" in out

    @patch("acai.cli.knowledge.list.get_store")
    def test_subject_filter_passed_to_store(self, mock_gs):
        mock_gs.return_value.list.return_value = []
        args = _FakeArgs(subject="python", subsubject="asyncio")
        self._execute(args)
        mock_gs.return_value.list.assert_called_once_with(
            subject="python", subsubject="asyncio"
        )

    @patch("acai.cli.knowledge.list.get_store")
    def test_single_document(self, mock_gs, capsys):
        mock_gs.return_value.list.return_value = [_make_doc()]
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        assert "1 document(s)" in capsys.readouterr().out

    @staticmethod
    def _execute(args):
        from acai.cli.knowledge.list import List
        return List.execute(args)


# ── Search command ────────────────────────────────────────────────

class TestSearchCommand:

    @patch("acai.cli.knowledge.search.get_store")
    def test_missing_query_returns_error(self, mock_gs, capsys):
        args = _FakeArgs(query=None)
        rc = self._execute(args)
        assert rc == 1
        assert "error: --query is required" in capsys.readouterr().err

    @patch("acai.cli.knowledge.search.get_store")
    def test_empty_query_returns_error(self, mock_gs, capsys):
        args = _FakeArgs(query="")
        rc = self._execute(args)
        assert rc == 1

    @patch("acai.cli.knowledge.search.get_store")
    def test_no_results(self, mock_gs, capsys):
        mock_gs.return_value.search.return_value = []
        args = _FakeArgs(query="nonexistent")
        rc = self._execute(args)
        assert rc == 0
        assert 'No documents matching "nonexistent"' in capsys.readouterr().out

    @patch("acai.cli.knowledge.search.get_store")
    def test_results_displayed(self, mock_gs, capsys):
        docs = [_make_doc(), _make_doc(title="coroutines")]
        mock_gs.return_value.search.return_value = docs
        args = _FakeArgs(query="gen")
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Path" in out
        assert "Preview" in out
        assert "python/asyncio/generators" in out
        assert "2 result(s)" in out

    @patch("acai.cli.knowledge.search.get_store")
    def test_search_with_subject_filter(self, mock_gs):
        mock_gs.return_value.search.return_value = []
        args = _FakeArgs(query="test", subject="python", subsubject="asyncio")
        self._execute(args)
        mock_gs.return_value.search.assert_called_once_with(
            "test", subject="python", subsubject="asyncio"
        )

    @patch("acai.cli.knowledge.search.get_store")
    def test_long_content_truncated_in_preview(self, mock_gs, capsys):
        doc = _make_doc(content="a" * 200)
        mock_gs.return_value.search.return_value = [doc]
        args = _FakeArgs(query="aaa")
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "…" in out

    @patch("acai.cli.knowledge.search.get_store")
    def test_special_characters_in_query(self, mock_gs, capsys):
        mock_gs.return_value.search.return_value = []
        args = _FakeArgs(query='foo"bar <baz>')
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No documents matching" in out

    @staticmethod
    def _execute(args):
        from acai.cli.knowledge.search import Search
        return Search.execute(args)


# ── Show command ──────────────────────────────────────────────────

class TestShowCommand:

    @patch("acai.cli.knowledge.show.get_store")
    def test_missing_path_returns_error(self, mock_gs, capsys):
        args = _FakeArgs(path=None)
        rc = self._execute(args)
        assert rc == 1
        assert "error: --path is required" in capsys.readouterr().err

    @patch("acai.cli.knowledge.show.get_store")
    def test_empty_path_returns_error(self, mock_gs, capsys):
        args = _FakeArgs(path="")
        rc = self._execute(args)
        assert rc == 1

    @patch("acai.cli.knowledge.show.get_store")
    def test_document_not_found(self, mock_gs, capsys):
        mock_gs.return_value.get_by_path.return_value = None
        args = _FakeArgs(path="a/b/c")
        rc = self._execute(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    @patch("acai.cli.knowledge.show.get_store")
    def test_document_displayed(self, mock_gs, capsys):
        doc = _make_doc(content="# Hello\nThis is content.")
        mock_gs.return_value.get_by_path.return_value = doc
        args = _FakeArgs(path="python/asyncio/generators")
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Path:" in out
        assert "python/asyncio/generators" in out
        assert "Updated:" in out
        assert "# Hello" in out
        assert "This is content." in out

    @patch("acai.cli.knowledge.show.get_store")
    def test_separator_line_printed(self, mock_gs, capsys):
        doc = _make_doc()
        mock_gs.return_value.get_by_path.return_value = doc
        args = _FakeArgs(path="python/asyncio/generators")
        self._execute(args)
        out = capsys.readouterr().out
        assert "─" * 60 in out

    @patch("acai.cli.knowledge.show.get_store")
    def test_empty_content_document(self, mock_gs, capsys):
        doc = _make_doc(content="")
        mock_gs.return_value.get_by_path.return_value = doc
        args = _FakeArgs(path="python/asyncio/generators")
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Path:" in out

    @staticmethod
    def _execute(args):
        from acai.cli.knowledge.show import Show
        return Show.execute(args)


# ── Sync command ──────────────────────────────────────────────────

class TestSyncCommand:

    @patch("acai.cli.knowledge.sync._get_db")
    def test_basic_sync(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)
        mock_db.sync.return_value = {
            "added": 3, "updated": 1, "removed": 0, "total": 4
        }

        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Sync complete" in out
        assert "Added:   3" in out
        assert "Updated: 1" in out
        assert "Removed: 0" in out
        assert "Total:   4" in out
        mock_db.sync.assert_called_once_with("/fake/workspace/knowledge")

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_tags_success(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)
        mock_db.get.return_value = {
            "subject": "python",
            "subsubject": "asyncio",
            "title": "generators",
            "tags": ["old-tag"],
            "facets": Facets(),
            "updated_at": 1700000000.0,
        }

        args = _FakeArgs(set_tags=["python/asyncio/generators", "async,coroutine"])
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Tags set" in out
        mock_db.upsert.assert_called_once()
        call_kwargs = mock_db.upsert.call_args
        assert call_kwargs[1]["tags"] == ["async", "coroutine"]

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_tags_missing_args(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)

        args = _FakeArgs(set_tags=["only-path"])
        rc = self._execute(args)
        assert rc == 1
        assert "Usage" in capsys.readouterr().out

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_tags_doc_not_found(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)
        mock_db.get.return_value = None

        args = _FakeArgs(set_tags=["a/b/c", "tag1,tag2"])
        rc = self._execute(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_facet_success(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)
        mock_db.get.return_value = {
            "subject": "python",
            "subsubject": "asyncio",
            "title": "generators",
            "tags": [],
            "facets": {"personality": "language", "matter": "", "energy": "", "space": "", "time": ""},
            "updated_at": 1700000000.0,
        }

        args = _FakeArgs(set_facet=["python/asyncio/generators", "matter", "stdlib"])
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Facet 'matter' set" in out
        call_kwargs = mock_db.upsert.call_args
        assert call_kwargs[1]["facets"]["matter"] == "stdlib"
        assert call_kwargs[1]["facets"]["personality"] == "language"

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_facet_missing_args(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)

        args = _FakeArgs(set_facet=["path", "personality"])
        rc = self._execute(args)
        assert rc == 1
        assert "Usage" in capsys.readouterr().out

    @patch("acai.knowledge.FACETS", ("personality", "matter", "energy", "space", "time"))
    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_facet_unknown_facet(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)

        args = _FakeArgs(set_facet=["a/b/c", "bogus", "val"])
        rc = self._execute(args)
        assert rc == 1
        assert "Unknown facet" in capsys.readouterr().out

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_facet_doc_not_found(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)
        mock_db.get.return_value = None

        args = _FakeArgs(set_facet=["a/b/c", "personality", "val"])
        rc = self._execute(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().out.lower()

    @patch("acai.cli.knowledge.sync._get_db")
    def test_set_tags_with_whitespace_in_tags(self, mock_get_db, capsys):
        mock_db, mock_config = MagicMock(), MagicMock()
        mock_config.workspace = "/fake/workspace"
        mock_get_db.return_value = (mock_db, mock_config)
        mock_db.get.return_value = {
            "subject": "s", "subsubject": "ss", "title": "t",
            "tags": [], "facets": Facets(), "updated_at": 1.0,
        }

        args = _FakeArgs(set_tags=["s/ss/t", " tag1 , tag2 , "])
        rc = self._execute(args)
        assert rc == 0
        call_kwargs = mock_db.upsert.call_args
        assert call_kwargs[1]["tags"] == ["tag1", "tag2"]

    @staticmethod
    def _execute(args):
        from acai.cli.knowledge.sync import Sync
        return Sync.execute(args)


# ── Tags (tree) command ───────────────────────────────────────────

class TestTagsTreeCommand:

    @patch("acai.cli.knowledge.tags.get_store")
    def test_empty_tree(self, mock_gs, capsys):
        mock_gs.return_value.tree.return_value = {}
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        assert "empty" in capsys.readouterr().out.lower()

    @patch("acai.cli.knowledge.tags.get_store")
    def test_tree_with_documents(self, mock_gs, capsys):
        mock_gs.return_value.tree.return_value = {
            "python": {
                "asyncio": ["generators", "coroutines"],
                "typing": ["generics"],
            },
        }
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "python/" in out
        assert "  asyncio/" in out
        assert "    generators.md" in out
        assert "    coroutines.md" in out
        assert "  typing/" in out
        assert "    generics.md" in out
        assert "1 subject(s), 3 document(s)" in out

    @patch("acai.cli.knowledge.tags.get_store")
    def test_tree_multiple_subjects(self, mock_gs, capsys):
        mock_gs.return_value.tree.return_value = {
            "python": {"core": ["intro"]},
            "rust": {"basics": ["ownership"]},
        }
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "python/" in out
        assert "rust/" in out
        assert "2 subject(s), 2 document(s)" in out

    @patch("acai.cli.knowledge.tags.get_store")
    def test_tree_subjects_sorted(self, mock_gs, capsys):
        mock_gs.return_value.tree.return_value = {
            "zzz": {"a": ["doc"]},
            "aaa": {"b": ["doc"]},
        }
        args = _FakeArgs()
        rc = self._execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        aaa_pos = out.index("aaa/")
        zzz_pos = out.index("zzz/")
        assert aaa_pos < zzz_pos

    @staticmethod
    def _execute(args):
        from acai.cli.knowledge.tags import Tree
        return Tree.execute(args)
