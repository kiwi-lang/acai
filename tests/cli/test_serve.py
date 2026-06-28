"""Tests for acai.cli.serve — serve command and helpers."""

from __future__ import annotations

import os
import signal
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest

from acai.cli.serve import (
    ServeArguments,
    _resolve_model_source,
    _tail_log,
    Serve,
    COMMANDS,
)


# ---------------------------------------------------------------------------
# _resolve_model_source
# ---------------------------------------------------------------------------
class TestResolveModelSource:

    def test_local_directory(self, tmp_path):
        model_dir = tmp_path / "my-model"
        model_dir.mkdir()
        result = _resolve_model_source(str(model_dir))
        assert result == str(model_dir)

    def test_local_file(self, tmp_path):
        model_file = tmp_path / "model.bin"
        model_file.write_text("data")
        result = _resolve_model_source(str(model_file))
        assert result == str(model_file)

    def test_hf_cache_hit(self):
        mock_rev = MagicMock()
        mock_rev.snapshot_path = "/cache/snapshots/abc123"
        mock_rev.last_modified = 1000

        mock_repo = MagicMock()
        mock_repo.repo_id = "org/model"
        mock_repo.revisions = [mock_rev]

        mock_cache = MagicMock()
        mock_cache.repos = [mock_repo]

        with patch("acai.cli.serve.os.path.isdir", return_value=False), \
             patch("acai.cli.serve.os.path.isfile", return_value=False), \
             patch("huggingface_hub.scan_cache_dir", return_value=mock_cache):
            result = _resolve_model_source("org/model")
        assert result == "/cache/snapshots/abc123"

    def test_hf_cache_scan_exception_falls_through(self, tmp_path):
        hub_dir = tmp_path / "hub"
        slug_dir = hub_dir / "models--org--model" / "snapshots" / "rev1"
        slug_dir.mkdir(parents=True)

        with patch("huggingface_hub.scan_cache_dir", side_effect=ImportError), \
             patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            result = _resolve_model_source("org/model")
        assert "rev1" in result

    def test_hub_dir_candidate_without_snapshots(self, tmp_path):
        hub_dir = tmp_path / "hub"
        candidate = hub_dir / "models--org--model"
        candidate.mkdir(parents=True)

        with patch("huggingface_hub.scan_cache_dir", side_effect=ImportError), \
             patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            result = _resolve_model_source("org/model")
        assert result == str(candidate)

    def test_will_download_fallback(self, tmp_path):
        with patch("huggingface_hub.scan_cache_dir", side_effect=ImportError), \
             patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            result = _resolve_model_source("org/new-model")
        assert "(will download)" in result

    def test_hf_cache_no_matching_repo(self):
        mock_cache = MagicMock()
        mock_cache.repos = []

        with patch("acai.cli.serve.os.path.isdir", return_value=False), \
             patch("acai.cli.serve.os.path.isfile", return_value=False), \
             patch("huggingface_hub.scan_cache_dir", return_value=mock_cache), \
             patch.dict(os.environ, {"HF_HOME": "/nonexistent"}):
            result = _resolve_model_source("org/model")
        assert "(will download)" in result

    def test_hf_cache_repo_no_revisions(self):
        mock_repo = MagicMock()
        mock_repo.repo_id = "org/model"
        mock_repo.revisions = []

        mock_cache = MagicMock()
        mock_cache.repos = [mock_repo]

        with patch("acai.cli.serve.os.path.isdir", return_value=False), \
             patch("acai.cli.serve.os.path.isfile", return_value=False), \
             patch("huggingface_hub.scan_cache_dir", return_value=mock_cache), \
             patch.dict(os.environ, {"HF_HOME": "/nonexistent"}):
            result = _resolve_model_source("org/model")
        assert "(will download)" in result

    def test_snapshots_dir_empty(self, tmp_path):
        hub_dir = tmp_path / "hub"
        snapshots = hub_dir / "models--org--model" / "snapshots"
        snapshots.mkdir(parents=True)

        with patch("huggingface_hub.scan_cache_dir", side_effect=ImportError), \
             patch.dict(os.environ, {"HF_HOME": str(tmp_path)}):
            result = _resolve_model_source("org/model")
        assert result == str(hub_dir / "models--org--model")


# ---------------------------------------------------------------------------
# _tail_log
# ---------------------------------------------------------------------------
class TestTailLog:

    def test_reads_new_lines(self, tmp_path, capsys):
        log = tmp_path / "test.log"
        log.write_text("line1\nline2\n")
        pos = _tail_log(str(log), 0)
        assert pos > 0
        captured = capsys.readouterr().out
        assert "line1" in captured
        assert "line2" in captured

    def test_skips_empty_lines(self, tmp_path, capsys):
        log = tmp_path / "test.log"
        log.write_text("\n\n\nonly this\n\n")
        _tail_log(str(log), 0)
        captured = capsys.readouterr().out
        assert "only this" in captured
        lines = [l for l in captured.splitlines() if l.strip()]
        assert len(lines) == 1

    def test_strips_vllm_prefix(self, tmp_path, capsys):
        log = tmp_path / "test.log"
        log.write_text("(APIServer pid=12345) Model loaded\n")
        _tail_log(str(log), 0)
        captured = capsys.readouterr().out
        assert "Model loaded" in captured
        assert "APIServer" not in captured

    def test_returns_file_pos_on_oserror(self):
        result = _tail_log("/nonexistent/path/to/log", 42)
        assert result == 42

    def test_no_new_content(self, tmp_path, capsys):
        log = tmp_path / "test.log"
        log.write_text("old\n")
        pos1 = _tail_log(str(log), 0)
        pos2 = _tail_log(str(log), pos1)
        assert pos1 == pos2
        captured = capsys.readouterr().out
        assert captured.count("old") == 1

    def test_incremental_read(self, tmp_path, capsys):
        log = tmp_path / "test.log"
        log.write_text("first\n")
        pos = _tail_log(str(log), 0)
        capsys.readouterr()

        with open(str(log), "a") as f:
            f.write("second\n")
        pos2 = _tail_log(str(log), pos)
        captured = capsys.readouterr().out
        assert "second" in captured
        assert "first" not in captured
        assert pos2 > pos


# ---------------------------------------------------------------------------
# Serve.execute — imports are inside execute(), patch at source
# ---------------------------------------------------------------------------
class TestServeExecute:

    def _make_args(self, **kwargs):
        defaults = dict(
            config=None, db=None, verbose=False,
            model=None, backend=None, port=None,
            host=None, launch_template=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _make_provider(self):
        p = MagicMock()
        p.model = "test-model"
        p.backend = "vllm"
        p.server_host = "0.0.0.0"
        p.server_port = 8000
        return p

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.provider.LLMServer")
    def test_happy_path_server_healthy(self, mock_llm_cls, mock_resolve, mock_setup):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        mock_server = MagicMock()
        mock_server.pid = 1234
        mock_server.latest_log_path.return_value = None
        mock_server.is_healthy.return_value = True
        mock_server.process = MagicMock()
        mock_server.process.poll.return_value = 0
        mock_server.process.returncode = 0
        mock_llm_cls.return_value = mock_server

        rc = Serve.execute(self._make_args())
        assert rc == 0
        mock_server.start_process.assert_called_once()

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.provider.LLMServer")
    def test_start_process_raises_llm_error(self, mock_llm_cls, mock_resolve, mock_setup):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        from acai.provider.server import LLMServerError
        mock_server = MagicMock()
        mock_server.start_process.side_effect = LLMServerError("GPU OOM")
        mock_llm_cls.return_value = mock_server

        rc = Serve.execute(self._make_args())
        assert rc == 1

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.cli.serve._tail_log", return_value=0)
    @patch("acai.provider.LLMServer")
    def test_process_exits_during_health_wait(
        self, mock_llm_cls, mock_tail, mock_resolve, mock_setup,
    ):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.returncode = 1

        mock_server = MagicMock()
        mock_server.pid = 1234
        mock_server.latest_log_path.return_value = "/tmp/test.log"
        mock_server.is_healthy.return_value = False
        mock_server.process = mock_process
        mock_llm_cls.return_value = mock_server

        rc = Serve.execute(self._make_args())
        assert rc == 1

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.cli.serve._tail_log", return_value=0)
    @patch("acai.provider.LLMServer")
    def test_health_wait_raises_llm_error(
        self, mock_llm_cls, mock_tail, mock_resolve, mock_setup,
    ):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        from acai.provider.server import LLMServerError
        mock_server = MagicMock()
        mock_server.pid = 1234
        mock_server.latest_log_path.return_value = "/tmp/test.log"
        mock_server.is_healthy.side_effect = LLMServerError("timeout")
        mock_server.process = MagicMock()
        mock_llm_cls.return_value = mock_server

        rc = Serve.execute(self._make_args())
        assert rc == 1

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.provider.LLMServer")
    def test_overrides_applied(self, mock_llm_cls, mock_resolve, mock_setup):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        mock_server = MagicMock()
        mock_server.pid = 1234
        mock_server.latest_log_path.return_value = None
        mock_server.is_healthy.return_value = True
        mock_server.process = MagicMock()
        mock_server.process.poll.return_value = 0
        mock_server.process.returncode = 0
        mock_llm_cls.return_value = mock_server

        args = self._make_args(
            model="custom/model",
            backend="llamacpp",
            port=9999,
            host="127.0.0.1",
            launch_template="custom {model}",
        )

        rc = Serve.execute(args)
        assert rc == 0
        assert provider.backend == "llamacpp"
        assert provider.server_port == 9999
        assert provider.server_host == "127.0.0.1"
        assert provider.launch_template == "custom {model}"

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.provider.LLMServer")
    def test_local_provider_none_falls_to_active(
        self, mock_llm_cls, mock_resolve, mock_setup,
    ):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = None
        mock_config.active_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        mock_server = MagicMock()
        mock_server.pid = 1234
        mock_server.latest_log_path.return_value = None
        mock_server.is_healthy.return_value = True
        mock_server.process = MagicMock()
        mock_server.process.poll.return_value = 0
        mock_server.process.returncode = 0
        mock_llm_cls.return_value = mock_server

        rc = Serve.execute(self._make_args())
        assert rc == 0
        mock_config.active_provider.assert_called_once()

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.cli.serve.signal.pause", side_effect=KeyboardInterrupt)
    @patch("acai.provider.LLMServer")
    def test_no_process_returns_zero(
        self, mock_llm_cls, mock_pause, mock_resolve, mock_setup,
    ):
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        mock_server = MagicMock()
        mock_server.pid = None
        mock_server.latest_log_path.return_value = None
        mock_server.is_healthy.return_value = True
        mock_server.process = None
        mock_llm_cls.return_value = mock_server

        with pytest.raises(SystemExit) as exc_info:
            Serve.execute(self._make_args())
        assert exc_info.value.code == 0
        mock_server.stop.assert_called_once()

    @patch("acai.cli.serve.setup")
    @patch("acai.cli.serve._resolve_model_source", return_value="/models/test")
    @patch("acai.provider.LLMServer")
    def test_process_exits_with_zero_returncode(
        self, mock_llm_cls, mock_resolve, mock_setup,
    ):
        """Process exits with returncode=0 during health wait => return 1 (or 1)."""
        provider = self._make_provider()
        mock_config = MagicMock()
        mock_config.local_provider.return_value = provider
        mock_setup.return_value = (mock_config, MagicMock())

        mock_process = MagicMock()
        mock_process.poll.return_value = 0
        mock_process.returncode = 0

        mock_server = MagicMock()
        mock_server.pid = 1234
        mock_server.latest_log_path.return_value = None
        mock_server.is_healthy.return_value = False
        mock_server.process = mock_process
        mock_llm_cls.return_value = mock_server

        rc = Serve.execute(self._make_args())
        assert rc == 1


class TestServeArguments:
    def test_defaults(self):
        args = ServeArguments()
        assert args.model is None
        assert args.backend is None
        assert args.port is None
        assert args.host is None
        assert args.launch_template is None


class TestServeCommandMeta:
    def test_commands_is_serve(self):
        assert COMMANDS is Serve

    def test_name(self):
        assert Serve.name == "serve"
