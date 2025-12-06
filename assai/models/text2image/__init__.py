from __future__ import annotations

import os

import torch
from diffusers import FluxPipeline
from flask import request
from contextlib import contextmanager
from assai.tools import namespaced_route
import torchcompat.core as accelerator
import base64
from io import BytesIO

def pil_to_base64_png(img):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def capture_progress():
    yield



cached_pipeline = {}


def cached(key):
    global cached_pipeline

    def decorator(fun):
        def _(*args, **kwargs):
            if pipe := cached_pipeline.get(key):
                return pipe

            pipe = fun(*args, **kwargs)
            cached_pipeline[key] = pipe
            return pipe

        return _

    return decorator


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

    @route("/model/list>")
    def list_model_t2i(name):
        """List local models the user can choose from"""
        return [
            default_model,
        ]

    @route("/model/run", methods=['POST'])
    @route("/model/run/<string:model>", methods=['POST'])
    def run_t2i(model=default_model):
        """Execute the model from the provided input"""

        data = request.get_json()
        prompt = data.pop("prompt")

        @cached("t2i")
        def load():
            pipe = FluxPipeline.from_pretrained(
                model,
                torch_dtype=torch.bfloat16,
                device_map="cuda"
            )
            return pipe

        generation_args = {
            "height": 256,
            "width": 256,
            "guidance_scale": 3.5,
            "num_inference_steps": 50,
            "max_sequence_length": 512,
            "generator": torch.Generator(accelerator.device_type).manual_seed(data.pop("seed", 0))
        }

        generation_args.update(data)

        output: FluxPipelineOutput = load()(prompt,
            # callback_on_step_end_tensor_inputs=[],
            # callback_on_step_end=self.on_step,
            **generation_args
        )

        return [f"data:image/png;base64,{pil_to_base64_png(image)}" for image in output.images]


if __name__ == "__main__":
    routes(None)