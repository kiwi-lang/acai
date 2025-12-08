from __future__ import annotations

import os
import sys
import base64
import numpy as np
from io import BytesIO
import wave
from threading import Lock
import threading

import torch
from flask import request
from contextlib import contextmanager
import torchcompat.core as accelerator
from transformers import pipeline

from assai.tools import namespaced_route, capture_progress_thread, cached, websocket_pusher


def audio_base64_to_numpy(audio_base64_data_uri, sample_rate=16000):
    """Convert base64 audio data URI (WAV or WebM) to numpy array"""
    # Remove data URI prefix
    if audio_base64_data_uri.startswith("data:audio/"):
        audio_base64 = audio_base64_data_uri.split(",", 1)[1]
        mime_type = audio_base64_data_uri.split(";")[0].split(":")[1]
    else:
        audio_base64 = audio_base64_data_uri
        mime_type = "audio/wav"

    # Decode base64
    audio_bytes = base64.b64decode(audio_base64)
    buffer = BytesIO(audio_bytes)

    # Try librosa first (handles many formats including WebM if ffmpeg is available)
    try:
        import librosa
        audio_array, sample_rate_from_file = librosa.load(buffer, sr=sample_rate, mono=True)
        return audio_array, sample_rate_from_file
    except ImportError:
        # Fallback to WAV only if librosa not available
        if mime_type == "audio/wav" or mime_type == "audio/wave":
            with wave.open(buffer, 'rb') as wav_file:
                frames = wav_file.getnframes()
                sample_rate_from_file = wav_file.getframerate()
                audio_bytes = wav_file.readframes(frames)
                audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0

                # Resample if needed
                if sample_rate_from_file != sample_rate:
                    ratio = sample_rate / sample_rate_from_file
                    indices = np.arange(len(audio_array)) * ratio
                    audio_array = np.interp(indices, np.arange(len(audio_array)), audio_array)

                return audio_array, sample_rate
        else:
            raise ValueError(f"Audio format {mime_type} not supported. Please install librosa for WebM/Opus support or convert to WAV.")


def routes(app: ASSAI, db):
    #
    # We need something to handle keeping models in VRAM/RAM
    # to reduce latency but also a way to move them if we need more VRAM/RAM
    #
    route = namespaced_route(app, '/speech2text')
    default_model = "openai/whisper-large"  # Default ASR model

    @route("/model/download")
    @route("/model/download/<string:name>")
    def download_model_s2t(name=default_model):
        """Download a new model"""
        # Need to spawn a long term running process
        # to start the download and have a way to measure progress as well
        # and resume previous downloads

    @route("/model/delete/<string:name>")
    def delete_model_s2t(name):
        """Delete a local model"""

    @route("/model/list")
    def list_model_s2t():
        """List local models the user can choose from"""
        return [
            default_model,
            "openai/whisper-tiny",
            "openai/whisper-small",
            "openai/whisper-medium",
            "openai/whisper-large",
        ]

    @route("/model/settings")
    @route("/model/settings/<string:name>")
    def model_settings_s2t(name=default_model):
        return {
            "language": {
                "type": str,
                "default": None  # None = auto-detect
            },
            "task": {
                "type": str,
                "default": "transcribe"  # "transcribe" or "translate"
            },
        }

    @route("/model/run", methods=['POST'])
    @route("/model/run/<string:model>", methods=['POST'])
    def run_s2t(model=default_model):
        """Execute the model from the provided input"""

        data = request.get_json()
        audio_data_uri = data.pop("audio")  # Base64 WAV data URI
        session_id = data.pop("session_id", None)
        action_id = data.pop("action_id", 0)

        pusher = websocket_pusher(app, action_id)

        @cached("s2t")
        def load():
            with capture_progress_thread(pusher, action_id):
                print(f"[S2T] Loading ASR model: {model}", flush=True)
                sys.stdout.flush()
                # Use transformers pipeline for automatic speech recognition
                device = 0 if accelerator.device_type == "cuda" else -1
                print(f"[S2T] Using device: {device}", flush=True)
                sys.stdout.flush()
                pipe = pipeline(
                    "automatic-speech-recognition",
                    model=model,
                    device=device,
                    torch_dtype=torch.float16 if accelerator.device_type == "cuda" else torch.float32
                )
                print("[S2T] Model loaded successfully", flush=True)
                sys.stdout.flush()
                return pipe

        generation_args = {
            "language": None,  # Auto-detect
            "task": "transcribe",
        }

        # Update with any provided parameters
        generation_args.update({k: v for k, v in data.items() if k in generation_args})

        pipe = load()

        with capture_progress_thread(pusher, action_id):
            print(f"[S2T] Starting speech recognition", flush=True)
            print(f"[S2T] Action ID: {action_id}", flush=True)
            sys.stdout.flush()

            # Convert audio data URI to numpy array
            print("[S2T] Converting audio data...", flush=True)
            sys.stdout.flush()
            audio_array, sample_rate = audio_base64_to_numpy(audio_data_uri)
            print(f"[S2T] Audio shape: {audio_array.shape}, sample rate: {sample_rate}", flush=True)
            sys.stdout.flush()

            print("[S2T] Transcribing audio...", flush=True)
            sys.stdout.flush()

            # Transcribe audio
            result = pipe(
                audio_array,
                **{k: v for k, v in generation_args.items() if v is not None}
            )

            print("[S2T] Transcription complete", flush=True)
            sys.stdout.flush()

            # Extract text from result
            if isinstance(result, dict):
                text = result.get("text", "")
            elif isinstance(result, str):
                text = result
            else:
                text = str(result)

            return {"text": text}

if __name__ == "__main__":
    routes(None)
