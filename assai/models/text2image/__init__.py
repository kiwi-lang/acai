from __future__ import annotations

import os
import sys
import time
import base64
from io import BytesIO
from threading import Lock
import threading
from datetime import datetime
from contextlib import contextmanager

import torch
from flask import request
import torchcompat.core as accelerator
from torchvision.transforms.functional import to_pil_image

from assai.tools import namespaced_route, capture_progress_thread, pil_to_base64_png, cached, websocket_pusher
from assai.tools.input import Input, Message, Conversation, text as text_input

import assai.models.text2image.flux as flux
import assai.models.text2image.pony as pony
import assai.models.text2image.chroma as chroma
import assai.models.text2image.qwen as qwen
import assai.models.text2image.generic as generic


def routes(app: ASSAI, db):
    #
    # We need something to handle keeping models in VRAM/RAM
    # to reduce latency but also a way to move them if we need more VRAM/RAM
    #
    route = namespaced_route(app, '/text2image')
    default_model = "black-forest-labs/FLUX.1-dev"

    @route("/model/download")
    @route("/model/download/<string:name>")
    def download_model_t2i(name=default_model):
        """Download a new model"""
        # Neeed to spawn a long term running process
        # to start the download and have a way to measure progress as well
        # and resume previous downloads

    @route("/model/delete/<string:name>")
    def delete_model_t2i(name):
        """Delete a local model"""

    @route("/model/list")
    def list_model_t2i():
        """List local models the user can choose from"""
        return [
            default_model,
        ]

    @route("/model/settings")
    @route("/model/settings/<string:name>")
    def model_settings_t2i(name=default_model):
        return {
            "height": {
                "type": int,
                "min": 32,
                "max": None,
                "default": 256
            },
            "width": {
                "type": int,
                "min": 32,
                "max": None,
                "default": 256
            },
            "guidance_scale": {
                "type": float,
                "default": 2.5
            },
            "num_inference_steps": {
                "type": int,
                "min": 1,
                "max": None,
                "default": 25
            },
            "max_sequence_length": {
                "type": int,
                "min": None,
                "max": 512,
                "default": 512
            },
            "num_images_per_prompt": {
                "type": int,
                "min": 1,
                "max": None,
                "default": 1
            },
            "generator": {
                "type": int,
                "min": None,
                "max": None,
                "default": 256
            }
        }

    @route("/model/run", methods=['POST'])
    @route("/model/run/<string:model>", methods=['POST'])
    def run_t2i(model=default_model):
        """Execute the model from the provided input using Message format"""

        data = request.get_json()
        message = data.pop("message", {})
        session_id = data.pop("session_id", None)
        action_id = data.pop("action_id", 0)

        # Validate message
        if not message:
            return {"error": "No message provided"}, 400

        if message.get("role") != "user":
            return {"error": "Message must be from user"}, 400

        content_input = message.get("content", {})
        if content_input.get("kind") != "text":
            return {"error": "Text2Image expects text input"}, 400

        prompt = content_input.get("data", "")

        pusher = websocket_pusher(app, action_id)

        @cached("t2i")
        def load(): 
            with capture_progress_thread(pusher, action_id):
                # return qwen.load()
                # return chroma.load()
                # return pony.load()
                return flux.load()
 
        def seed():
            if seed := data.pop("seed"):
                return seed
            return int(time.time() * 1000)

        generation_args = {
            "height": 256,
            "width": 256,
            "guidance_scale": 3.5,
            "num_inference_steps": 50,
            "max_sequence_length": 512,
            "num_images_per_prompt": 5,
            "generator": torch.Generator(accelerator.device_type).manual_seed(seed())
        }

        generation_args.update(data)

        pipe = load()

        def on_step_end(pipe, step, timestep, callback_kwargs):
            latents = callback_kwargs["latents"]

            height, width = generation_args["height"], generation_args["width"]
 
            with torch.no_grad():
                latents = pipe._unpack_latents(latents, height, width, pipe.vae_scale_factor)
                latents = (latents / pipe.vae.config.scaling_factor) + pipe.vae.config.shift_factor
                image = pipe.vae.decode(latents, return_dict=False)[0]
                pil = pipe.image_processor.postprocess(image, output_type="pil")
                # pil = [to_pil_image(img) for img in images]
                image_data_url = [f"data:image/png;base64,{pil_to_base64_png(p)}" for p in pil]

                app.message("preview", {"id": action_id, "thread_id": 0, "images": image_data_url})
            return callback_kwargs

        with capture_progress_thread(pusher, action_id):
            output = pipe(prompt,
                callback_on_step_end_tensor_inputs = ["latents"],
                callback_on_step_end=on_step_end,
                **generation_args
            )

        # Convert images to Input format
        image_data_urls = [f"data:image/png;base64,{pil_to_base64_png(image)}" for image in output.images]

        # Return Message format - for multiple images, return first one in Message format
        # Frontend can handle multiple images via extension
        response_input: Input = {
            "kind": "image",
            "encoding": "data_url",
            "data": image_data_urls[0] if image_data_urls else ""
        }

        response_message: Message = {
            "id": int(time.time() * 1000),
            "action_id": action_id,
            "role": "assistant",
            "content": response_input,
            "timestamp": datetime.now().isoformat()
        }

        return {
            "message": response_message,
            "images": image_data_urls  # Include all images for frontend compatibility
        }


if __name__ == "__main__":
    routes(None)