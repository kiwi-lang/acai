"""Tests for acai.cli.audit — __init__, clear, list, plot subcommands."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ── helpers in acai.cli.audit.__init__ ──────────────────────────────

from acai.cli.audit import audit_dirs, load_audits


class TestAuditDirs:
    """Tests for audit_dirs()."""

    def test_returns_empty_when_root_missing(self, tmp_path):
        assert audit_dirs(str(tmp_path / "nope")) == []

    def test_returns_empty_when_root_is_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("")
        assert audit_dirs(str(f)) == []

    def test_lists_subdirs_sorted_by_mtime(self, tmp_path):
        d1 = tmp_path / "aaa"
        d2 = tmp_path / "bbb"
        d1.mkdir()
        d2.mkdir()
        os.utime(d1, (100, 100))
        os.utime(d2, (200, 200))
        result = audit_dirs(str(tmp_path))
        assert result == [str(d2), str(d1)]

    def test_skips_files_and_symlinks(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        (tmp_path / "file.txt").write_text("hi")
        link = tmp_path / "sym"
        link.symlink_to(real)
        result = audit_dirs(str(tmp_path))
        assert result == [str(real)]

    def test_empty_directory_returns_empty(self, tmp_path):
        assert audit_dirs(str(tmp_path)) == []


class TestLoadAudits:
    """Tests for load_audits()."""

    def _make_audit(self, root, name, data):
        d = root / name
        d.mkdir()
        (d / "audit.json").write_text(json.dumps(data))
        return d

    def test_loads_valid_audits(self, tmp_path):
        self._make_audit(tmp_path, "r1", {"request_id": "1"})
        self._make_audit(tmp_path, "r2", {"request_id": "2"})
        result = load_audits(str(tmp_path), 10)
        ids = {a["request_id"] for a in result}
        assert ids == {"1", "2"}

    def test_respects_limit(self, tmp_path):
        for i in range(5):
            d = self._make_audit(tmp_path, f"r{i}", {"request_id": str(i)})
            os.utime(d, (1000 + i, 1000 + i))
        result = load_audits(str(tmp_path), 2)
        assert len(result) == 2

    def test_skips_missing_audit_json(self, tmp_path):
        (tmp_path / "empty_dir").mkdir()
        assert load_audits(str(tmp_path), 10) == []

    def test_skips_malformed_json(self, tmp_path):
        d = tmp_path / "bad"
        d.mkdir()
        (d / "audit.json").write_text("{not json")
        assert load_audits(str(tmp_path), 10) == []

    def test_skips_unreadable_file(self, tmp_path):
        d = tmp_path / "locked"
        d.mkdir()
        f = d / "audit.json"
        f.write_text('{"ok": true}')
        f.chmod(0o000)
        result = load_audits(str(tmp_path), 10)
        assert result == []
        f.chmod(0o644)

    def test_nonexistent_root_returns_empty(self, tmp_path):
        assert load_audits(str(tmp_path / "nope"), 10) == []


# ── Audit ParentCommand ────────────────────────────────────────────

from acai.cli.audit import Audit


class TestAuditCommand:
    def test_name(self):
        assert Audit.name == "audit"

    def test_module_returns_module(self):
        mod = Audit.module()
        assert hasattr(mod, "COMMANDS")


# ── clear subcommand ───────────────────────────────────────────────

from acai.cli.audit.clear import Clear, ClearArguments


def _make_config(audit_dir):
    return SimpleNamespace(audit=SimpleNamespace(dir=audit_dir))


def _make_queue():
    return MagicMock()


class TestClearExecute:
    """Tests for Clear.execute()."""

    def _args(self, audit_dir, keep=10):
        args = ClearArguments(keep=keep, config=None, db=None, verbose=False)
        return args, audit_dir

    @patch("acai.cli.audit.clear.setup")
    def test_nothing_to_clear_no_dir(self, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "nope")
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        args, _ = self._args(audit_dir)
        rc = Clear.execute(args)
        assert rc == 0
        assert "Nothing to clear" in capsys.readouterr().out

    @patch("acai.cli.audit.clear.setup")
    def test_nothing_to_clear_within_keep(self, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(os.path.join(audit_dir, "d1"))
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        args, _ = self._args(audit_dir, keep=10)
        rc = Clear.execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to clear" in out

    @patch("acai.cli.audit.clear.setup")
    def test_clears_old_dirs(self, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(audit_dir, exist_ok=True)
        for i in range(5):
            d = os.path.join(audit_dir, f"d{i}")
            os.makedirs(d)
            os.utime(d, (1000 + i, 1000 + i))
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        args, _ = self._args(audit_dir, keep=2)
        rc = Clear.execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cleared 3" in out
        remaining = [
            d for d in os.listdir(audit_dir) if os.path.isdir(os.path.join(audit_dir, d))
        ]
        assert len(remaining) == 2

    @patch("acai.cli.audit.clear.setup")
    def test_clears_all_with_keep_zero(self, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(audit_dir, exist_ok=True)
        for i in range(3):
            os.makedirs(os.path.join(audit_dir, f"d{i}"))
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        args, _ = self._args(audit_dir, keep=0)
        rc = Clear.execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cleared 3" in out

    @patch("acai.cli.audit.clear.setup")
    def test_removes_stale_latest_symlink(self, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(audit_dir)
        d = os.path.join(audit_dir, "req1")
        os.makedirs(d)
        latest = os.path.join(audit_dir, "latest")
        os.symlink("req1", latest)
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        args, _ = self._args(audit_dir, keep=0)
        rc = Clear.execute(args)
        assert rc == 0
        assert not os.path.exists(latest)

    @patch("acai.cli.audit.clear.setup")
    def test_keeps_valid_latest_symlink(self, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(audit_dir)
        d1 = os.path.join(audit_dir, "req1")
        d2 = os.path.join(audit_dir, "req2")
        os.makedirs(d1)
        os.makedirs(d2)
        os.utime(d1, (100, 100))
        os.utime(d2, (200, 200))
        latest = os.path.join(audit_dir, "latest")
        os.symlink("req2", latest)
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        args, _ = self._args(audit_dir, keep=1)
        rc = Clear.execute(args)
        assert rc == 0
        assert os.path.islink(latest)

    def test_clear_arguments_defaults(self):
        args = ClearArguments(config=None, db=None, verbose=False)
        assert args.keep == 10


# ── list subcommand ────────────────────────────────────────────────

from acai.cli.audit.list import List, ListArguments


class TestListExecute:
    """Tests for List.execute()."""

    @patch("acai.cli.audit.list.setup")
    @patch("acai.cli.audit.list.load_audits")
    def test_no_audits(self, mock_load, mock_setup, capsys):
        mock_setup.return_value = (_make_config("/fake"), _make_queue())
        mock_load.return_value = []
        args = ListArguments(last=20, config=None, db=None, verbose=False)
        rc = List.execute(args)
        assert rc == 0
        assert "No audits" in capsys.readouterr().out

    @patch("acai.cli.audit.list.setup")
    @patch("acai.cli.audit.list.load_audits")
    def test_prints_table(self, mock_load, mock_setup, capsys):
        mock_setup.return_value = (_make_config("/fake"), _make_queue())
        mock_load.return_value = [
            {
                "request_id": "abc123def456",
                "meta": {"endpoint": "/v1/chat"},
                "total_duration_ms": 123.4,
                "started_at_iso": "2024-01-01T00:00:00Z",
            },
        ]
        args = ListArguments(last=20, config=None, db=None, verbose=False)
        rc = List.execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "abc123def456" in out
        assert "/v1/chat" in out
        assert "123.4ms" in out

    @patch("acai.cli.audit.list.setup")
    @patch("acai.cli.audit.list.load_audits")
    def test_missing_fields_use_defaults(self, mock_load, mock_setup, capsys):
        mock_setup.return_value = (_make_config("/fake"), _make_queue())
        mock_load.return_value = [{}]
        args = ListArguments(last=5, config=None, db=None, verbose=False)
        rc = List.execute(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "?" in out

    def test_list_arguments_defaults(self):
        args = ListArguments(config=None, db=None, verbose=False)
        assert args.last == 20


# ── plot subcommand ────────────────────────────────────────────────

from acai.cli.audit.plot import Plot, PlotArguments, _build_spans


class TestBuildSpans:
    """Tests for _build_spans() helper."""

    def test_empty_audits(self):
        assert _build_spans([]) == []

    def test_paired_start_end(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 100,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 10},
                    {"event": "dispatch.end", "elapsed_ms": 50},
                ],
            }
        ]
        rows = _build_spans(audits)
        assert len(rows) == 1
        assert rows[0]["span"] == "dispatch"
        assert rows[0]["start_ms"] == 10
        assert rows[0]["end_ms"] == 50
        assert rows[0]["duration_ms"] == 40

    def test_unpaired_event_becomes_marker(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 100,
                "events": [
                    {"event": "worker.acquired", "elapsed_ms": 5},
                ],
            }
        ]
        rows = _build_spans(audits)
        assert len(rows) == 1
        assert rows[0]["span"] == "worker.acquired"
        assert rows[0]["duration_ms"] == 0.5

    def test_error_event_closes_span(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {},
                "total_duration_ms": 100,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 10},
                    {"event": "dispatch.error", "elapsed_ms": 30},
                ],
            }
        ]
        rows = _build_spans(audits)
        assert len(rows) == 1
        assert rows[0]["span"] == "dispatch"
        assert rows[0]["end_ms"] == 30

    def test_end_without_start_becomes_marker(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {},
                "total_duration_ms": 100,
                "events": [
                    {"event": "foo.end", "elapsed_ms": 20},
                ],
            }
        ]
        rows = _build_spans(audits)
        assert len(rows) == 1
        assert rows[0]["span"] == "foo"
        assert rows[0]["start_ms"] == 20
        assert rows[0]["end_ms"] == 20.5

    def test_open_span_extends_to_total(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {},
                "total_duration_ms": 200,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 10},
                ],
            }
        ]
        rows = _build_spans(audits)
        assert len(rows) == 1
        assert rows[0]["end_ms"] == 200
        assert rows[0]["duration_ms"] == 190

    def test_dispatch_tokens_splits_into_ttft_and_gen(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 200,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 10},
                    {
                        "event": "dispatch.tokens",
                        "ttft_ms": 20,
                        "generation_ms": 80,
                        "token_count": 50,
                        "itl_ms": 1.2,
                    },
                    {"event": "dispatch.end", "elapsed_ms": 100},
                ],
            }
        ]
        rows = _build_spans(audits)
        spans = [r["span"] for r in rows]
        assert "ttft" in spans
        assert "generation" in spans

    def test_dispatch_tokens_with_tail(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 200,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 0},
                    {
                        "event": "dispatch.tokens",
                        "ttft_ms": 10,
                        "generation_ms": 50,
                        "token_count": 30,
                        "itl_ms": 1.0,
                    },
                    {"event": "dispatch.end", "elapsed_ms": 100},
                ],
            }
        ]
        rows = _build_spans(audits)
        spans = [r["span"] for r in rows]
        assert "dispatch.tail" in spans

    def test_dispatch_tokens_no_tail_when_close(self):
        audits = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 200,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 0},
                    {
                        "event": "dispatch.tokens",
                        "ttft_ms": 10,
                        "generation_ms": 50,
                        "token_count": 30,
                        "itl_ms": 1.0,
                    },
                    {"event": "dispatch.end", "elapsed_ms": 50.4},
                ],
            }
        ]
        rows = _build_spans(audits)
        spans = [r["span"] for r in rows]
        assert "dispatch.tail" not in spans

    def test_multiple_audits_produce_rows_for_each(self):
        audits = [
            {
                "request_id": "aaa",
                "meta": {},
                "total_duration_ms": 100,
                "events": [{"event": "x.start", "elapsed_ms": 0}, {"event": "x.end", "elapsed_ms": 50}],
            },
            {
                "request_id": "bbb",
                "meta": {},
                "total_duration_ms": 100,
                "events": [{"event": "y.start", "elapsed_ms": 0}, {"event": "y.end", "elapsed_ms": 30}],
            },
        ]
        rows = _build_spans(audits)
        assert len(rows) == 2

    def test_row_label_includes_endpoint_and_rid_prefix(self):
        audits = [
            {
                "request_id": "abcdefghij",
                "meta": {"endpoint": "/v1/chat"},
                "total_duration_ms": 100,
                "events": [{"event": "foo", "elapsed_ms": 5}],
            }
        ]
        rows = _build_spans(audits)
        assert "/v1/chat" in rows[0]["request"]
        assert "abcdefgh" in rows[0]["request"]

    def test_no_events_no_rows(self):
        audits = [
            {
                "request_id": "xyz",
                "meta": {},
                "total_duration_ms": 100,
                "events": [],
            }
        ]
        assert _build_spans(audits) == []


class TestPlotExecute:
    """Tests for Plot.execute()."""

    @patch("acai.cli.audit.plot.setup")
    @patch("acai.cli.audit.plot.load_audits")
    def test_missing_altair_prints_error(self, mock_load, mock_setup, capsys):
        mock_setup.return_value = (_make_config("/fake"), _make_queue())
        mock_load.return_value = []

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "altair":
                raise ImportError("no altair")
            return real_import(name, *args, **kwargs)

        args = PlotArguments(last=5, output=None, config=None, db=None, verbose=False)
        with patch("builtins.__import__", side_effect=fake_import):
            rc = Plot.execute(args)
        assert rc == 1
        assert "altair" in capsys.readouterr().err

    @patch("acai.cli.audit.plot.setup")
    @patch("acai.cli.audit.plot.load_audits")
    def test_no_audit_data(self, mock_load, mock_setup, capsys):
        mock_setup.return_value = (_make_config("/fake"), _make_queue())
        mock_load.return_value = []
        # altair must be importable inside execute, so we mock the import path
        alt_mock = MagicMock()
        with patch.dict(sys.modules, {"altair": alt_mock}):
            args = PlotArguments(last=5, output=None, config=None, db=None, verbose=False)
            rc = Plot.execute(args)
        assert rc == 1
        assert "No audit data" in capsys.readouterr().err

    @patch("acai.cli.audit.plot.setup")
    @patch("acai.cli.audit.plot.load_audits")
    def test_no_span_data(self, mock_load, mock_setup, capsys):
        mock_setup.return_value = (_make_config("/fake"), _make_queue())
        mock_load.return_value = [
            {"request_id": "x", "meta": {}, "total_duration_ms": 0, "events": []}
        ]
        alt_mock = MagicMock()
        with patch.dict(sys.modules, {"altair": alt_mock}):
            args = PlotArguments(last=5, output=None, config=None, db=None, verbose=False)
            rc = Plot.execute(args)
        assert rc == 1
        assert "No span data" in capsys.readouterr().err

    @patch("acai.cli.audit.plot.setup")
    @patch("acai.cli.audit.plot.load_audits")
    def test_successful_plot(self, mock_load, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(audit_dir)
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        mock_load.return_value = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 100,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 10},
                    {"event": "dispatch.end", "elapsed_ms": 50},
                ],
            }
        ]
        chart_mock = MagicMock()
        alt_mock = MagicMock()
        alt_mock.Chart.return_value.mark_bar.return_value.encode.return_value = chart_mock
        chart_mock.properties.return_value = chart_mock

        with patch.dict(sys.modules, {"altair": alt_mock}):
            out_file = str(tmp_path / "out.png")
            args = PlotArguments(last=5, output=out_file, config=None, db=None, verbose=False)
            rc = Plot.execute(args)

        assert rc == 0
        chart_mock.save.assert_called_once_with(out_file)
        assert "Saved to" in capsys.readouterr().out

    @patch("acai.cli.audit.plot.setup")
    @patch("acai.cli.audit.plot.load_audits")
    def test_default_output_path(self, mock_load, mock_setup, tmp_path, capsys):
        audit_dir = str(tmp_path / "audit")
        os.makedirs(audit_dir)
        mock_setup.return_value = (_make_config(audit_dir), _make_queue())
        mock_load.return_value = [
            {
                "request_id": "abc12345xxxx",
                "meta": {"endpoint": "/chat"},
                "total_duration_ms": 100,
                "events": [
                    {"event": "dispatch.start", "elapsed_ms": 10},
                    {"event": "dispatch.end", "elapsed_ms": 50},
                ],
            }
        ]
        chart_mock = MagicMock()
        alt_mock = MagicMock()
        alt_mock.Chart.return_value.mark_bar.return_value.encode.return_value = chart_mock
        chart_mock.properties.return_value = chart_mock

        with patch.dict(sys.modules, {"altair": alt_mock}):
            args = PlotArguments(last=5, output=None, config=None, db=None, verbose=False)
            rc = Plot.execute(args)

        assert rc == 0
        expected = os.path.join(audit_dir, "_plot.png")
        chart_mock.save.assert_called_once_with(expected)

    def test_plot_arguments_defaults(self):
        args = PlotArguments(config=None, db=None, verbose=False)
        assert args.last == 5
        assert args.output is None
