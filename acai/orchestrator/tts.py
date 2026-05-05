"""Piper TTS service — lazy-loaded PiperVoice wrapper.

Provides synchronous synthesis methods used by both the HTTP endpoint
and the ``tts_accumulate`` workflow node.

The voice catalog is fetched from the ``rhasspy/piper-voices``
HuggingFace repository and cached locally in ``acai/data/piper_voices.json``
so nothing is hardcoded.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import threading
import time
import wave
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:
    from acai.orchestrator.config import TTSConfig

log = logging.getLogger(__name__)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_HF_REPO = "rhasspy/piper-voices"
_HF_BASE = f"https://huggingface.co/{_HF_REPO}/resolve/main"
_HF_VOICES_URL = f"{_HF_BASE}/voices.json"

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "data")
_CACHE_PATH = os.path.join(_DATA_DIR, "piper_voices.json")
_CACHE_MAX_AGE = 7 * 24 * 3600  # refresh weekly


# ---------------------------------------------------------------------------
# Voice catalog — fetched from HuggingFace, cached in acai/data
# ---------------------------------------------------------------------------

def _load_cache() -> dict | None:
    """Return the cached catalog if present and fresh, else ``None``."""
    if not os.path.isfile(_CACHE_PATH):
        return None
    try:
        age = time.time() - os.path.getmtime(_CACHE_PATH)
        if age > _CACHE_MAX_AGE:
            return None
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(data: dict) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    tmp = _CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, _CACHE_PATH)


_catalog_lock = threading.Lock()
_catalog_cache: dict | None = None


def get_voice_catalog() -> dict:
    """Return the cached catalog dict (keyed by voice id).

    Only reads from the in-memory or on-disk cache.  The catalog is
    populated by the frontend calling ``ingest_voice_catalog()`` — the
    backend never fetches from HuggingFace directly (avoids UA blocks).
    """
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache

    with _catalog_lock:
        if _catalog_cache is not None:
            return _catalog_cache

        data = _load_cache()
        if data is not None:
            _catalog_cache = data
            return data

        return {}


def ingest_voice_catalog(data: dict) -> dict:
    """Accept a catalog dict (fetched by the browser) and cache it.

    Returns the ingested catalog.
    """
    global _catalog_cache
    _save_cache(data)
    with _catalog_lock:
        _catalog_cache = data
    log.info("ingested voice catalog with %d entries", len(data))
    return data


def _voice_to_entry(voice_id: str, raw: dict) -> dict:
    """Convert a raw catalog entry to the flat dict used by the API."""
    lang = raw.get("language", {})
    name_eng = lang.get("name_english", "")
    locale = lang.get("code", "")
    quality = raw.get("quality", "")
    voice_name = raw.get("name", voice_id)
    label = f"{name_eng} — {voice_name.capitalize()} ({quality})" if name_eng else voice_id
    return {
        "id": voice_id,
        "lang": lang.get("family", ""),
        "locale": locale,
        "name": voice_name,
        "quality": quality,
        "label": label,
    }


def _subpaths_from_voice_id(voice_id: str) -> tuple[str, str]:
    """Derive repo-relative file paths from the voice ID naming convention.

    Piper IDs follow ``{locale}-{name}-{quality}`` (e.g.
    ``en_GB-cori-medium``).  The repo path is
    ``{family}/{locale}/{name}/{quality}/{voice_id}.onnx``.
    """
    parts = voice_id.split("-")
    if len(parts) >= 3:
        locale = parts[0]                    # en_GB
        family = locale.split("_")[0]        # en
        name = "-".join(parts[1:-1])         # cori  (handles multi-part names)
        quality = parts[-1]                  # medium
    else:
        family, locale, name, quality = "en", "en_US", voice_id, "medium"
    onnx_path = f"{family}/{locale}/{name}/{quality}/{voice_id}.onnx"
    return onnx_path, onnx_path + ".json"


def _model_subpaths(voice_id: str, raw: dict) -> tuple[str, str]:
    """Return ``(onnx_subpath, json_subpath)`` from catalog file paths."""
    onnx_path = ""
    json_path = ""
    for fpath in raw.get("files", {}):
        if fpath.endswith(".onnx"):
            onnx_path = fpath
        elif fpath.endswith(".onnx.json"):
            json_path = fpath
    if not onnx_path:
        return _subpaths_from_voice_id(voice_id)
    return onnx_path, json_path


class _HFProgressBar:
    """Minimal tqdm-compatible class that forwards updates to a callback."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.total: int = int(kwargs.get("total", 0) or 0)
        self.n: int = 0
        self._cb: Callable[[int, int], None] | None = kwargs.get("_acai_cb")  # type: ignore[assignment]

    def update(self, n: int = 1) -> None:
        self.n += n
        if self._cb:
            self._cb(self.n, self.total)

    def close(self) -> None:
        pass

    def __enter__(self) -> "_HFProgressBar":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _download_voice_files(
    voice_id: str,
    onnx_subpath: str,
    json_subpath: str,
    dest_dir: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """Download voice model files via ``huggingface_hub``.

    Uses the HF token from the environment when available, giving
    authenticated download speeds and avoiding throttling.

    Returns the local path to the ``.onnx`` file.
    """
    from functools import partial
    from huggingface_hub import hf_hub_download
    import shutil

    os.makedirs(dest_dir, exist_ok=True)
    dest_onnx = os.path.join(dest_dir, f"{voice_id}.onnx")
    dest_json = dest_onnx + ".json"

    if os.path.isfile(dest_onnx):
        return dest_onnx

    log.info("downloading %s from %s via huggingface_hub", voice_id, _HF_REPO)

    tqdm_cls = partial(_HFProgressBar, _acai_cb=on_progress) if on_progress else None

    try:
        cached = hf_hub_download(
            repo_id=_HF_REPO,
            filename=onnx_subpath,
            repo_type="model",
            **({"tqdm_class": tqdm_cls} if tqdm_cls else {}),
        )
        shutil.copy2(cached, dest_onnx)
        log.info("voice model saved to %s", dest_onnx)
    except Exception:
        log.exception("hf_hub_download failed for %s", onnx_subpath)
        raise

    # Config JSON — best effort
    if not os.path.isfile(dest_json):
        try:
            cached_json = hf_hub_download(
                repo_id=_HF_REPO,
                filename=json_subpath,
                repo_type="model",
            )
            shutil.copy2(cached_json, dest_json)
        except Exception:
            log.warning("failed to download config %s (non-fatal)", json_subpath)

    return dest_onnx


class TTSService:
    """Thread-safe wrapper around ``piper.PiperVoice``."""

    def __init__(self, config: TTSConfig, workspace: str = ""):
        self._config = config
        self._workspace = workspace
        self._voice = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def sample_rate(self) -> int:
        return self._config.sample_rate

    def _models_dir(self) -> str:
        base = os.path.join(self._workspace, ".models", "tts") if self._workspace else ".models/tts"
        return base

    def _resolve_model_path(self) -> str:
        if self._config.model_path:
            return self._config.model_path
        voice = self._config.voice or "en_US-lessac-medium"
        return os.path.join(self._models_dir(), f"{voice}.onnx")

    @staticmethod
    def _voice_subpaths(voice_id: str) -> tuple[str, str]:
        """Return ``(onnx_subpath, json_subpath)`` for a voice."""
        catalog = get_voice_catalog()
        raw = catalog.get(voice_id)
        if raw:
            return _model_subpaths(voice_id, raw)
        log.info("voice %s not in catalog cache, deriving paths from ID", voice_id)
        return _subpaths_from_voice_id(voice_id)

    def _ensure_model(self, model_path: str) -> None:
        """Download the model + config from HuggingFace if not on disk."""
        if os.path.isfile(model_path):
            return
        voice_id = self._config.voice or "en_US-lessac-medium"
        onnx_sub, json_sub = self._voice_subpaths(voice_id)
        _download_voice_files(voice_id, onnx_sub, json_sub, self._models_dir())

    def _load(self):
        if self._voice is not None:
            return self._voice
        with self._lock:
            if self._voice is not None:
                return self._voice
            from piper import PiperVoice

            model_path = self._resolve_model_path()
            self._ensure_model(model_path)
            log.info("loading piper voice from %s (cuda=%s)", model_path, self._config.use_cuda)
            self._voice = PiperVoice.load(model_path, use_cuda=self._config.use_cuda)

            if self._voice.config and hasattr(self._voice.config, "sample_rate"):
                actual_sr = self._voice.config.sample_rate
                if actual_sr and actual_sr != self._config.sample_rate:
                    log.info("voice sample rate %d overrides config %d", actual_sr, self._config.sample_rate)
                    self._config.sample_rate = actual_sr

            return self._voice

    def list_voices(self) -> list[dict]:
        """Return the voice catalog with ``downloaded`` status for each."""
        catalog = get_voice_catalog()
        models_dir = self._models_dir()
        result = []
        for voice_id, raw in catalog.items():
            entry = _voice_to_entry(voice_id, raw)
            path = os.path.join(models_dir, f"{voice_id}.onnx")
            entry["downloaded"] = os.path.isfile(path)
            result.append(entry)
        result.sort(key=lambda v: v["label"])
        return result

    def download_voice(
        self,
        voice_id: str,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download a voice model by ID. Returns the local path."""
        onnx_sub, json_sub = self._voice_subpaths(voice_id)
        return _download_voice_files(
            voice_id, onnx_sub, json_sub,
            self._models_dir(), on_progress=on_progress,
        )

    def synthesize(self, text: str) -> bytes:
        """Synthesize *text* and return complete WAV bytes."""
        voice = self._load()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav)
        return buf.getvalue()

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        """Yield raw PCM int16 chunks for *text*."""
        from piper.config import SynthesisConfig

        voice = self._load()
        speed = self._config.length_scale or 1.0
        syn_cfg = SynthesisConfig(
            length_scale=1.0 / speed,
            noise_scale=self._config.noise_scale,
            noise_w_scale=self._config.noise_w,
        )
        for chunk in voice.synthesize(text, syn_config=syn_cfg):
            yield chunk.audio_int16_bytes

    def synthesize_pcm(self, text: str) -> bytes:
        """Synthesize *text* and return concatenated PCM int16 bytes."""
        parts: list[bytes] = []
        for chunk in self.synthesize_stream(text):
            parts.append(chunk)
        return b"".join(parts)

    def pcm_to_base64(self, pcm: bytes) -> str:
        return base64.b64encode(pcm).decode("ascii")

    def audio_event(self, pcm: bytes) -> dict:
        """Build an SSE-ready audio event payload."""
        return {
            "pcm_base64": self.pcm_to_base64(pcm),
            "sample_rate": self._config.sample_rate,
            "sample_width": 2,
            "channels": 1,
        }

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        """Split *text* on sentence boundaries."""
        parts = _SENTENCE_RE.split(text)
        return [s.strip() for s in parts if s.strip()]
