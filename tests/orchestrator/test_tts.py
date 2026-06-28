"""Tests for acai.orchestrator.tts — Piper TTS service."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, mock_open

import pytest

import acai.orchestrator.tts as tts_mod
from acai.orchestrator.tts import (
    TTSService,
    _HFProgressBar,
    _download_voice_files,
    _load_cache,
    _model_subpaths,
    _save_cache,
    _subpaths_from_voice_id,
    _voice_to_entry,
    get_voice_catalog,
    ingest_voice_catalog,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeTTSConfig:
    enabled: bool = True
    model_path: str = ""
    voice: str = "en_US-lessac-medium"
    length_scale: float = 1.0
    noise_scale: float = 0.667
    noise_w: float = 0.8
    use_cuda: bool = False
    sample_rate: int = 22050
    sentence_silence: float = 0.2
    sentence_end: str = r"[.!?]\s"
    clause_break: str = r"[,;:\n\u2014]\s"
    min_clause_len: int = 40
    volume: float = 1.0


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Reset module-level catalog cache between tests."""
    original = tts_mod._catalog_cache
    tts_mod._catalog_cache = None
    yield
    tts_mod._catalog_cache = original


@pytest.fixture
def cfg():
    return _FakeTTSConfig()


@pytest.fixture
def svc(cfg, tmp_path):
    return TTSService(cfg, workspace=str(tmp_path))


# ===================================================================
# _load_cache / _save_cache
# ===================================================================

class TestLoadCache:

    def test_returns_none_when_file_missing(self, tmp_path):
        with patch.object(tts_mod, "_CACHE_PATH", str(tmp_path / "no.json")):
            assert _load_cache() is None

    def test_returns_data_when_fresh(self, tmp_path):
        p = tmp_path / "voices.json"
        p.write_text('{"v1": {}}')
        with patch.object(tts_mod, "_CACHE_PATH", str(p)):
            assert _load_cache() == {"v1": {}}

    def test_returns_none_when_stale(self, tmp_path):
        p = tmp_path / "voices.json"
        p.write_text('{"v1": {}}')
        old_time = time.time() - 8 * 24 * 3600
        os.utime(str(p), (old_time, old_time))
        with patch.object(tts_mod, "_CACHE_PATH", str(p)):
            assert _load_cache() is None

    def test_returns_none_on_bad_json(self, tmp_path):
        p = tmp_path / "voices.json"
        p.write_text("NOT-JSON")
        with patch.object(tts_mod, "_CACHE_PATH", str(p)):
            assert _load_cache() is None

    def test_returns_none_on_os_error(self, tmp_path):
        p = tmp_path / "voices.json"
        p.write_text('{"v1": {}}')
        with (
            patch.object(tts_mod, "_CACHE_PATH", str(p)),
            patch("os.path.getmtime", side_effect=OSError("boom")),
        ):
            assert _load_cache() is None


class TestSaveCache:

    def test_writes_json_atomically(self, tmp_path):
        dest = tmp_path / "data" / "voices.json"
        with patch.object(tts_mod, "_CACHE_PATH", str(dest)):
            _save_cache({"x": 1})
        assert json.loads(dest.read_text()) == {"x": 1}
        assert not (tmp_path / "data" / "voices.json.tmp").exists()


# ===================================================================
# get_voice_catalog / ingest_voice_catalog
# ===================================================================

class TestGetVoiceCatalog:

    def test_returns_inmemory_cache(self):
        tts_mod._catalog_cache = {"cached": True}
        assert get_voice_catalog() == {"cached": True}

    def test_returns_disk_cache(self, tmp_path):
        p = tmp_path / "voices.json"
        p.write_text('{"disk": true}')
        with patch.object(tts_mod, "_CACHE_PATH", str(p)):
            result = get_voice_catalog()
        assert result == {"disk": True}

    def test_returns_empty_dict_when_no_cache(self, tmp_path):
        with patch.object(tts_mod, "_CACHE_PATH", str(tmp_path / "nope.json")):
            assert get_voice_catalog() == {}


class TestIngestVoiceCatalog:

    def test_saves_and_caches(self, tmp_path):
        dest = tmp_path / "data" / "voices.json"
        data = {"voice1": {"quality": "high"}}
        with patch.object(tts_mod, "_CACHE_PATH", str(dest)):
            result = ingest_voice_catalog(data)
        assert result is data
        assert tts_mod._catalog_cache is data
        assert json.loads(dest.read_text()) == data


# ===================================================================
# _voice_to_entry
# ===================================================================

class TestVoiceToEntry:

    def test_full_entry(self):
        raw = {
            "language": {
                "name_english": "English",
                "code": "en_US",
                "family": "en",
            },
            "quality": "medium",
            "name": "lessac",
        }
        entry = _voice_to_entry("en_US-lessac-medium", raw)
        assert entry["id"] == "en_US-lessac-medium"
        assert entry["lang"] == "en"
        assert entry["locale"] == "en_US"
        assert entry["name"] == "lessac"
        assert entry["quality"] == "medium"
        assert "English" in entry["label"]
        assert "Lessac" in entry["label"]

    def test_missing_name_english_uses_voice_id(self):
        raw = {"language": {}, "quality": "low"}
        entry = _voice_to_entry("xx-foo-low", raw)
        assert entry["label"] == "xx-foo-low"

    def test_missing_language_key(self):
        raw = {}
        entry = _voice_to_entry("x", raw)
        assert entry["name"] == "x"
        assert entry["lang"] == ""


# ===================================================================
# _subpaths_from_voice_id
# ===================================================================

class TestSubpathsFromVoiceId:

    def test_standard_three_part_id(self):
        onnx, js = _subpaths_from_voice_id("en_GB-cori-medium")
        assert onnx == "en/en_GB/cori/medium/en_GB-cori-medium.onnx"
        assert js == onnx + ".json"

    def test_multi_part_name(self):
        onnx, _ = _subpaths_from_voice_id("de_DE-thorsten-emotion-medium")
        assert onnx == "de/de_DE/thorsten-emotion/medium/de_DE-thorsten-emotion-medium.onnx"

    def test_short_id_fallback(self):
        onnx, js = _subpaths_from_voice_id("foo")
        assert onnx == "en/en_US/foo/medium/foo.onnx"
        assert js == onnx + ".json"

    def test_two_part_id_fallback(self):
        onnx, _ = _subpaths_from_voice_id("en-bar")
        assert "en_US" in onnx


# ===================================================================
# _model_subpaths
# ===================================================================

class TestModelSubpaths:

    def test_extracts_from_files_dict(self):
        raw = {"files": {
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx": {},
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json": {},
        }}
        onnx, js = _model_subpaths("en_US-lessac-medium", raw)
        assert onnx.endswith(".onnx")
        assert js.endswith(".onnx.json")

    def test_falls_back_when_no_onnx(self):
        raw = {"files": {"readme.txt": {}}}
        onnx, js = _model_subpaths("en_GB-cori-medium", raw)
        assert onnx == "en/en_GB/cori/medium/en_GB-cori-medium.onnx"

    def test_no_files_key(self):
        onnx, _ = _model_subpaths("en_US-lessac-medium", {})
        assert "lessac" in onnx


# ===================================================================
# _HFProgressBar
# ===================================================================

class TestHFProgressBar:

    def test_update_calls_callback(self):
        cb = MagicMock()
        bar = _HFProgressBar(total=100, _acai_cb=cb)
        bar.update(30)
        cb.assert_called_once_with(30, 100)
        bar.update(70)
        assert cb.call_count == 2

    def test_update_without_callback(self):
        bar = _HFProgressBar(total=50)
        bar.update(10)
        assert bar.n == 10

    def test_context_manager(self):
        bar = _HFProgressBar(total=10)
        with bar as b:
            assert b is bar
            b.update(5)
        assert bar.n == 5

    def test_close_is_noop(self):
        bar = _HFProgressBar()
        bar.close()

    def test_total_defaults_to_zero(self):
        bar = _HFProgressBar()
        assert bar.total == 0

    def test_total_none_treated_as_zero(self):
        bar = _HFProgressBar(total=None)
        assert bar.total == 0


# ===================================================================
# _download_voice_files
# ===================================================================

class TestDownloadVoiceFiles:

    def test_returns_early_if_onnx_exists(self, tmp_path):
        dest_dir = tmp_path / "models"
        dest_dir.mkdir()
        onnx = dest_dir / "test-voice.onnx"
        onnx.write_bytes(b"model")
        result = _download_voice_files(
            "test-voice", "sub/test.onnx", "sub/test.onnx.json", str(dest_dir),
        )
        assert result == str(onnx)

    def test_downloads_onnx_and_json(self, tmp_path):
        dest_dir = tmp_path / "models"
        cached_onnx = tmp_path / "cached.onnx"
        cached_onnx.write_bytes(b"onnx-data")
        cached_json = tmp_path / "cached.onnx.json"
        cached_json.write_text("{}")

        mock_hf = MagicMock(side_effect=[str(cached_onnx), str(cached_json)])

        with (
            patch("huggingface_hub.hf_hub_download", mock_hf),
            patch("shutil.copy2", side_effect=lambda src, dst: open(dst, "wb").write(b"x")),
        ):
            result = _download_voice_files(
                "v1", "sub/v1.onnx", "sub/v1.onnx.json", str(dest_dir),
            )

        assert result == str(dest_dir / "v1.onnx")
        assert mock_hf.call_count == 2

    def test_raises_on_onnx_download_failure(self, tmp_path):
        dest_dir = tmp_path / "models"
        mock_hf = MagicMock(side_effect=RuntimeError("network error"))

        with (
            patch("huggingface_hub.hf_hub_download", mock_hf),
            pytest.raises(RuntimeError, match="network error"),
        ):
            _download_voice_files("v1", "sub/v1.onnx", "sub/v1.onnx.json", str(dest_dir))

    def test_json_download_failure_is_nonfatal(self, tmp_path):
        dest_dir = tmp_path / "models"
        cached_onnx = tmp_path / "cached.onnx"
        cached_onnx.write_bytes(b"data")
        mock_hf = MagicMock(side_effect=[str(cached_onnx), Exception("json fail")])

        with (
            patch("huggingface_hub.hf_hub_download", mock_hf),
            patch("shutil.copy2", side_effect=lambda src, dst: open(dst, "wb").write(b"x")),
        ):
            result = _download_voice_files(
                "v1", "sub/v1.onnx", "sub/v1.onnx.json", str(dest_dir),
            )
        assert result == str(dest_dir / "v1.onnx")

    def test_progress_callback_wired(self, tmp_path):
        dest_dir = tmp_path / "models"
        cached_onnx = tmp_path / "cached.onnx"
        cached_onnx.write_bytes(b"data")
        mock_hf = MagicMock(return_value=str(cached_onnx))

        cb = MagicMock()
        with (
            patch("huggingface_hub.hf_hub_download", mock_hf),
            patch("shutil.copy2", side_effect=lambda src, dst: open(dst, "wb").write(b"x")),
        ):
            _download_voice_files(
                "v1", "sub/v1.onnx", "sub/v1.onnx.json", str(dest_dir),
                on_progress=cb,
            )
        call_kwargs = mock_hf.call_args_list[0].kwargs
        assert "tqdm_class" in call_kwargs


# ===================================================================
# TTSService — properties / helpers
# ===================================================================

class TestTTSServiceProperties:

    def test_enabled(self, svc, cfg):
        assert svc.enabled is True
        cfg.enabled = False
        assert svc.enabled is False

    def test_sample_rate(self, svc):
        assert svc.sample_rate == 22050

    def test_models_dir_with_workspace(self, svc, tmp_path):
        expected = os.path.join(str(tmp_path), ".models", "tts")
        assert svc._models_dir() == expected

    def test_models_dir_without_workspace(self, cfg):
        svc = TTSService(cfg, workspace="")
        assert svc._models_dir() == ".models/tts"

    def test_resolve_model_path_from_config(self, cfg, tmp_path):
        cfg.model_path = "/some/model.onnx"
        svc = TTSService(cfg, workspace=str(tmp_path))
        assert svc._resolve_model_path() == "/some/model.onnx"

    def test_resolve_model_path_default(self, svc, tmp_path):
        path = svc._resolve_model_path()
        assert path.endswith("en_US-lessac-medium.onnx")
        assert str(tmp_path) in path

    def test_resolve_model_path_custom_voice(self, cfg, tmp_path):
        cfg.voice = "de_DE-thorsten-medium"
        svc = TTSService(cfg, workspace=str(tmp_path))
        assert svc._resolve_model_path().endswith("de_DE-thorsten-medium.onnx")


# ===================================================================
# TTSService._voice_subpaths
# ===================================================================

class TestVoiceSubpaths:

    def test_uses_catalog_when_available(self):
        catalog = {
            "v1": {"files": {"path/v1.onnx": {}, "path/v1.onnx.json": {}}},
        }
        tts_mod._catalog_cache = catalog
        onnx, js = TTSService._voice_subpaths("v1")
        assert onnx == "path/v1.onnx"
        assert js == "path/v1.onnx.json"

    def test_falls_back_without_catalog(self):
        tts_mod._catalog_cache = {}
        onnx, js = TTSService._voice_subpaths("en_US-lessac-medium")
        assert "lessac" in onnx


# ===================================================================
# TTSService._ensure_model
# ===================================================================

class TestEnsureModel:

    def test_noop_when_model_exists(self, svc, tmp_path):
        model = tmp_path / ".models" / "tts" / "en_US-lessac-medium.onnx"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"model")
        svc._ensure_model(str(model))

    @patch.object(tts_mod, "_download_voice_files")
    def test_downloads_when_missing(self, mock_dl, svc, tmp_path):
        tts_mod._catalog_cache = {}
        model_path = str(tmp_path / ".models" / "tts" / "en_US-lessac-medium.onnx")
        svc._ensure_model(model_path)
        mock_dl.assert_called_once()


# ===================================================================
# TTSService._load
# ===================================================================

class TestLoad:

    def _setup_model_file(self, tmp_path):
        model_path = os.path.join(str(tmp_path), ".models", "tts", "en_US-lessac-medium.onnx")
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        with open(model_path, "wb") as f:
            f.write(b"model")
        return model_path

    def _mock_piper_module(self, mock_voice):
        mock_piper = MagicMock()
        mock_piper.PiperVoice.load.return_value = mock_voice
        return mock_piper

    def test_loads_voice(self, svc, tmp_path):
        self._setup_model_file(tmp_path)
        mock_voice = MagicMock()
        mock_voice.config = SimpleNamespace(sample_rate=None)
        mock_piper = self._mock_piper_module(mock_voice)

        with patch.dict(sys.modules, {"piper": mock_piper}):
            result = svc._load()
        assert result is mock_voice

    def test_overrides_sample_rate_from_voice(self, svc, tmp_path):
        self._setup_model_file(tmp_path)
        mock_voice = MagicMock()
        mock_voice.config = SimpleNamespace(sample_rate=16000)
        mock_piper = self._mock_piper_module(mock_voice)

        with patch.dict(sys.modules, {"piper": mock_piper}):
            svc._load()
        assert svc._config.sample_rate == 16000

    def test_double_load_returns_cached(self, svc, tmp_path):
        self._setup_model_file(tmp_path)
        mock_voice = MagicMock()
        mock_voice.config = SimpleNamespace(sample_rate=None)
        mock_piper = self._mock_piper_module(mock_voice)

        with patch.dict(sys.modules, {"piper": mock_piper}):
            v1 = svc._load()
            v2 = svc._load()
        assert v1 is v2
        mock_piper.PiperVoice.load.assert_called_once()


# ===================================================================
# TTSService.list_voices
# ===================================================================

class TestListVoices:

    def test_returns_sorted_entries(self, svc):
        tts_mod._catalog_cache = {
            "en_US-lessac-medium": {
                "language": {"name_english": "English", "code": "en_US", "family": "en"},
                "quality": "medium",
                "name": "lessac",
                "files": {},
            },
            "de_DE-thorsten-medium": {
                "language": {"name_english": "German", "code": "de_DE", "family": "de"},
                "quality": "medium",
                "name": "thorsten",
                "files": {},
            },
        }
        voices = svc.list_voices()
        assert len(voices) == 2
        assert voices[0]["id"] in ("en_US-lessac-medium", "de_DE-thorsten-medium")
        assert all("downloaded" in v for v in voices)

    def test_empty_catalog(self, svc):
        tts_mod._catalog_cache = {}
        assert svc.list_voices() == []

    def test_downloaded_flag(self, svc, tmp_path):
        models_dir = tmp_path / ".models" / "tts"
        models_dir.mkdir(parents=True)
        (models_dir / "v1.onnx").write_bytes(b"data")

        tts_mod._catalog_cache = {
            "v1": {"language": {}, "quality": "low", "name": "v1", "files": {}},
        }
        voices = svc.list_voices()
        assert voices[0]["downloaded"] is True


# ===================================================================
# TTSService.download_voice
# ===================================================================

class TestDownloadVoice:

    @patch.object(tts_mod, "_download_voice_files", return_value="/tmp/v.onnx")
    def test_calls_download(self, mock_dl, svc):
        tts_mod._catalog_cache = {}
        result = svc.download_voice("en_US-lessac-medium")
        assert result == "/tmp/v.onnx"
        mock_dl.assert_called_once()

    @patch.object(tts_mod, "_download_voice_files", return_value="/tmp/v.onnx")
    def test_passes_progress_callback(self, mock_dl, svc):
        tts_mod._catalog_cache = {}
        cb = MagicMock()
        svc.download_voice("en_US-lessac-medium", on_progress=cb)
        _, kwargs = mock_dl.call_args
        assert kwargs["on_progress"] is cb


# ===================================================================
# TTSService.synthesize
# ===================================================================

class TestSynthesize:

    def test_returns_wav_bytes(self, svc):
        mock_voice = MagicMock()

        def fake_synthesize_wav(text, wav_file):
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00" * 100)

        mock_voice.synthesize_wav = fake_synthesize_wav
        svc._voice = mock_voice

        result = svc.synthesize("Hello")
        assert result[:4] == b"RIFF"
        assert len(result) > 0


# ===================================================================
# TTSService.synthesize_stream
# ===================================================================

class TestSynthesizeStream:

    def _make_piper_modules(self):
        mock_piper = MagicMock()
        mock_piper_config = MagicMock()
        MockSynCfg = MagicMock()
        mock_piper_config.SynthesisConfig = MockSynCfg
        return {"piper": mock_piper, "piper.config": mock_piper_config}, MockSynCfg

    def test_yields_pcm_chunks(self, svc, cfg):
        mock_voice = MagicMock()
        chunk1 = SimpleNamespace(audio_int16_bytes=b"\x00\x01")
        chunk2 = SimpleNamespace(audio_int16_bytes=b"\x02\x03")
        mock_voice.synthesize.return_value = [chunk1, chunk2]
        svc._voice = mock_voice

        modules, MockSynCfg = self._make_piper_modules()
        MockSynCfg.return_value = MagicMock()
        with patch.dict(sys.modules, modules):
            chunks = list(svc.synthesize_stream("Hello"))

        assert chunks == [b"\x00\x01", b"\x02\x03"]

    def test_respects_speed_config(self, svc, cfg):
        cfg.length_scale = 2.0
        mock_voice = MagicMock()
        mock_voice.synthesize.return_value = []
        svc._voice = mock_voice

        modules, MockSynCfg = self._make_piper_modules()
        MockSynCfg.return_value = MagicMock()
        with patch.dict(sys.modules, modules):
            list(svc.synthesize_stream("Hi"))
            call_kwargs = MockSynCfg.call_args.kwargs
            assert call_kwargs["length_scale"] == pytest.approx(0.5)

    def test_none_length_scale_defaults_to_one(self, svc, cfg):
        cfg.length_scale = None
        mock_voice = MagicMock()
        mock_voice.synthesize.return_value = []
        svc._voice = mock_voice

        modules, MockSynCfg = self._make_piper_modules()
        MockSynCfg.return_value = MagicMock()
        with patch.dict(sys.modules, modules):
            list(svc.synthesize_stream("Hi"))
            call_kwargs = MockSynCfg.call_args.kwargs
            assert call_kwargs["length_scale"] == 1.0


# ===================================================================
# TTSService.synthesize_pcm
# ===================================================================

class TestSynthesizePcm:

    def test_concatenates_stream(self, svc):
        svc._voice = MagicMock()
        with patch.object(svc, "synthesize_stream", return_value=[b"aa", b"bb"]):
            result = svc.synthesize_pcm("Hello")
        assert result == b"aabb"

    def test_empty_text_returns_empty(self, svc):
        svc._voice = MagicMock()
        with patch.object(svc, "synthesize_stream", return_value=[]):
            result = svc.synthesize_pcm("")
        assert result == b""


# ===================================================================
# TTSService.pcm_to_base64 / audio_event
# ===================================================================

class TestPcmToBase64:

    def test_encodes_correctly(self, svc):
        pcm = b"\x01\x02\x03"
        result = svc.pcm_to_base64(pcm)
        assert base64.b64decode(result) == pcm

    def test_empty_input(self, svc):
        assert svc.pcm_to_base64(b"") == ""


class TestAudioEvent:

    def test_event_structure(self, svc):
        pcm = b"\x00\x01"
        event = svc.audio_event(pcm)
        assert event["sample_rate"] == 22050
        assert event["sample_width"] == 2
        assert event["channels"] == 1
        assert base64.b64decode(event["pcm_base64"]) == pcm

    def test_custom_sample_rate(self, cfg, tmp_path):
        cfg.sample_rate = 16000
        svc = TTSService(cfg, workspace=str(tmp_path))
        event = svc.audio_event(b"\x00")
        assert event["sample_rate"] == 16000


# ===================================================================
# TTSService.split_sentences
# ===================================================================

class TestSplitSentences:

    def test_basic_split(self):
        parts = TTSService.split_sentences("Hello world. How are you? Fine!")
        assert parts == ["Hello world.", "How are you?", "Fine!"]

    def test_single_sentence(self):
        assert TTSService.split_sentences("Just one.") == ["Just one."]

    def test_empty_string(self):
        assert TTSService.split_sentences("") == []

    def test_whitespace_only(self):
        assert TTSService.split_sentences("   ") == []

    def test_no_terminal_punctuation(self):
        assert TTSService.split_sentences("no punctuation here") == ["no punctuation here"]

    def test_multiple_spaces_between(self):
        parts = TTSService.split_sentences("A.   B.   C.")
        assert parts == ["A.", "B.", "C."]

    def test_exclamation_and_question(self):
        parts = TTSService.split_sentences("Wow! Really? Yes.")
        assert len(parts) == 3
