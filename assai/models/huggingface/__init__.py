from __future__ import annotations

import os
import sys
import time
import base64
from io import BytesIO
from threading import Lock
import threading
from dataclasses import asdict

import torch
from diffusers import FluxPipeline
from flask import request
from contextlib import contextmanager
import torchcompat.core as accelerator

from huggingface_hub import scan_cache_dir
from huggingface_hub import HfApi

from assai.tools import namespaced_route, capture_progress_thread, pil_to_base64_png, cached, websocket_pusher


def routes(app: ASSAI, db):

    route = namespaced_route(app, '/huggingface')
    api = HfApi()

    @route("/search/<string:name>")
    @route("/search/<string:name>/<string:filter>")
    def search_model(name, filter=None):
        return api.list_models(search=name, filter=filter, limit=20)

    @route("/info/<string:name>")
    def model_info(name):
        return api.model_info(search=name, filter=filter, limit=20)
    
    @route("/list")
    def available():
        cache_info = scan_cache_dir()
        d = asdict(cache_info)
        return d
