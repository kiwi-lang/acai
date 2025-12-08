from __future__ import annotations

import os
import sys
import time
from threading import Lock
import threading

import torch
from flask import request
from contextlib import contextmanager
import torchcompat.core as accelerator
from transformers import pipeline

from assai.tools import namespaced_route, capture_progress_thread, cached, websocket_pusher


def routes(app: ASSAI, db):
    #
    # We need something to handle keeping models in VRAM/RAM
    # to reduce latency but also a way to move them if we need more VRAM/RAM
    #
    route = namespaced_route(app, '/text2text')
    default_model = "mistralai/Mistral-Small-3.1-24B-Instruct-2503"  # Default conversational model

    @route("/model/download")
    @route("/model/download/<string:name>")
    def download_model_t2t(name=default_model):
        """Download a new model"""
        # Need to spawn a long term running process
        # to start the download and have a way to measure progress as well
        # and resume previous downloads

    @route("/model/delete/<string:name>")
    def delete_model_t2t(name):
        """Delete a local model"""

    @route("/model/list")
    def list_model_t2t():
        """List local models the user can choose from"""
        return [
            default_model,
            "gpt2",
            "gpt2-medium",
            "gpt2-large",
        ]

    @route("/model/settings")
    @route("/model/settings/<string:name>")
    def model_settings_t2t(name=default_model):
        return {
            "max_length": {
                "type": int,
                "min": 1,
                "max": 2048,
                "default": 100
            },
            "max_new_tokens": {
                "type": int,
                "min": 1,
                "max": 2048,
                "default": 2048
            },
            "temperature": {
                "type": float,
                "min": 0.0,
                "max": 2.0,
                "default": 0.7
            },
            "top_p": {
                "type": float,
                "min": 0.0,
                "max": 1.0,
                "default": 0.9
            },
            "top_k": {
                "type": int,
                "min": 0,
                "max": 100,
                "default": 50
            },
            "repetition_penalty": {
                "type": float,
                "min": 0.0,
                "max": 2.0,
                "default": 1.0
            },
            "do_sample": {
                "type": bool,
                "default": True
            },
        }

    @route("/model/run", methods=['POST'])
    @route("/model/run/<string:model>", methods=['POST'])
    def run_t2t(model=default_model):
        """Execute the model from the provided input"""

        data = request.get_json()
        prompt = data.pop("prompt")
        session_id = data.pop("session_id", None)
        action_id = data.pop("action_id", 0)
        conversation_history = data.pop("conversation_history", [])

        pusher = websocket_pusher(app, action_id)

        @cached("t2t")
        def load():
            with capture_progress_thread(pusher, action_id):
                print(f"[T2T] Loading text generation model: {model}", flush=True)
                sys.stdout.flush()
                # Use transformers pipeline for text generation
                device = 0 if accelerator.device_type == "cuda" else -1
                print(f"[T2T] Using device: {device}", flush=True)
                sys.stdout.flush()
                pipe = pipeline(
                    "text-generation",
                    model=model,
                    device=device,
                    torch_dtype=torch.float16 if accelerator.device_type == "cuda" else torch.float32
                )
                print("[T2T] Model loaded successfully", flush=True)
                sys.stdout.flush()
                return pipe

        generation_args = {
            "max_new_tokens": 50,
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "repetition_penalty": 1.0,
            "do_sample": True,
        }

        # Update with any provided parameters
        generation_args.update({k: v for k, v in data.items() if k in generation_args})

        pipe = load()

        with capture_progress_thread(pusher, action_id):
            print(f"[T2T] Starting text generation for prompt: {prompt[:50]}...", flush=True)
            print(f"[T2T] Action ID: {action_id}", flush=True)
            sys.stdout.flush()

            # Build context from conversation history if provided
            if conversation_history:
                # Format conversation history as context
                context = "\n".join([
                    f"{'User' if msg.get('role') == 'user' else 'Assistant'}: {msg.get('content', '')}"
                    for msg in conversation_history[-5:]  # Last 5 messages for context
                ])
                full_prompt = f"{context}\nUser: {prompt}\nAssistant:"
            else:
                full_prompt = prompt

            print("[T2T] Generating text...", flush=True)
            sys.stdout.flush()

            # Generate text
            outputs = pipe(
                full_prompt,
                **generation_args
            )

            print("[T2T] Text generation complete", flush=True)
            sys.stdout.flush()

            # Extract generated text
            if isinstance(outputs, list) and len(outputs) > 0:
                generated_text = outputs[0].get("generated_text", "")
                # Remove the prompt from the generated text
                if generated_text.startswith(full_prompt):
                    generated_text = generated_text[len(full_prompt):].strip()
                return {"text": generated_text}
            else:
                return {"text": str(outputs)}

if __name__ == "__main__":
    routes(None)
