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
from assai.tools.input import Input, Message, Conversation, text as text_input
from datetime import datetime


def huggingface_run(model, device):
    def load():
        # Use transformers pipeline for text generation
        pipe = pipeline(
            "text-generation",
            model=model,
            device=device,
            torch_dtype=torch.float8_e4m3fn
        )
        return pipe
        
    return load



def tensorrt_run(model, device):
    from tensorrt_llm import LLM, SamplingParams

    def load():
        llm = LLM(
            model="nvidia/Llama-4-Scout-17B-16E-Instruct-FP8", 
            attn_backend="FLASHINFER", 
            backend="pytorch", 
            tensor_parallel_size=1
        )

        def run(prompt: str):
            sampling_params = SamplingParams(temperature=0.8, top_p=0.95)
            return llm.generate(prompt, sampling_params)

        return run

    return load
    



def routes(app: ASSAI, db):
    #
    # We need something to handle keeping models in VRAM/RAM
    # to reduce latency but also a way to move them if we need more VRAM/RAM
    #
    route = namespaced_route(app, '/text2text')
    default_model = "mistralai/Mistral-Small-3.1-24B-Instruct-2503"  # Default conversational model

    @route("/model/download")
    @route("/model/download/<path:name>")
    def download_model_t2t(name=default_model):
        """Download a new model"""
        # Need to spawn a long term running process
        # to start the download and have a way to measure progress as well
        # and resume previous downloads

    @route("/model/delete/<path:name>")
    def delete_model_t2t(name):
        """Delete a local model"""

    @route("/model/list")
    def list_model_t2t():
        """List local models the user can choose from"""
        return [
            default_model,
            # "meta-llama/Llama-4-Scout-17B-16E",
            "nvidia/Llama-4-Scout-17B-16E-Instruct-FP8",
            "nvidia/Llama-4-Scout-17B-16E-Instruct-NVFP4",
            # "gpt2-medium",
            # "gpt2-large",
        ]

    @route("/model/settings")
    @route("/model/settings/<path:name>")
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
    @route("/model/run/<path:model>", methods=['POST'])
    def run_t2t(model=default_model):
        """Execute the model from the provided input using Message format"""

        data = request.get_json()
        message = data.pop("message", {})
        session_id = data.pop("session_id", None)
        action_id = data.pop("action_id", 0)

        device = 0 if accelerator.device_type == "cuda" else -1

        # Validate message
        if not message:
            return {"error": "No message provided"}, 400

        if message.get("role") != "user":
            return {"error": "Message must be from user"}, 400

        content_input = message.get("content", {})
        if content_input.get("kind") != "text":
            return {"error": "Text2Text expects text input"}, 400

        prompt = content_input.get("data", "")
        
        
        @cached("t2t", model, name=model)
        def load():
            loader = tensorrt_run(model, device)
            return loader()

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
        
        pusher = websocket_pusher(app, action_id)
        with capture_progress_thread(pusher, action_id):
            pipe = load()

            full_prompt = prompt

            try:
                # Generate text
                outputs = pipe(
                    full_prompt,
                    **generation_args
                )
            except Exception as e:
                return {"error": str(e)}

            # Extract generated text
            if isinstance(outputs, list) and len(outputs) > 0:
                generated_text = outputs[0].get("generated_text", "")
                # Remove the prompt from the generated text
                if generated_text.startswith(full_prompt):
                    generated_text = generated_text[len(full_prompt):].strip()
            else:
                generated_text = str(outputs)

            # Return Message format
            response_input: Input = text_input(generated_text)

            response_message: Message = {
                "id": int(time.time() * 1000),
                "action_id": action_id,
                "role": "assistant",
                "content": response_input,
                "timestamp": datetime.now().isoformat()
            }

            return {"message": response_message}

if __name__ == "__main__":
    routes(None)
