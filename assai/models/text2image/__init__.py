from __future__ import annotations

import os
import sys
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


class SocketIOBuffer:
    def __init__(self, push, stdout=None):
        self.prev = []
        self.push = push

    def write(self, msg):
        msg = msg.replace("\x1b[A", "\n").replace("\r", "\n")

        if "\n" not in msg:
            self.prev.append(msg)
        else:
            head, _, tail = msg.partition("\n")
            self.prev.append(head)
            line = "".join(self.prev)
            self.push(line)

            self.prev = []
            self.write(tail)
            

    def flush(self):
        pass


@contextmanager
def capture_progress(app):
    old_out = sys.stdout
    old_err = sys.stderr 

    if not isinstance(sys.stdout, SocketIOBuffer):
        sys.stdout = SocketIOBuffer(push=lambda line: app.message("log", line))
        sys.stderr = SocketIOBuffer(push=lambda line: app.message("log", line))

        yield

        sys.stdout = old_out
        sys.stderr = old_err
    else:
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
        session_id = data.pop("session_id")

        @cached("t2i")
        def load():
            with capture_progress(app):
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

        pipe = load()

        with capture_progress(app):
            output: FluxPipelineOutput = pipe(prompt,
                # callback_on_step_end_tensor_inputs=[],
                # callback_on_step_end=self.on_step,
                **generation_args
            )

        return [f"data:image/png;base64,{pil_to_base64_png(image)}" for image in output.images]


if __name__ == "__main__":
    routes(None)