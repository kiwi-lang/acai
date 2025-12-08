from __future__ import annotations

import os
import sys
import time
import base64
from io import BytesIO
from threading import Lock, RLock
import threading
from collections import defaultdict

import torch
from diffusers import FluxPipeline
from flask import request
from contextlib import contextmanager
import torchcompat.core as accelerator




def namespaced_route(app, namespace):
    if not namespace.startswith("/"):
        namespace = "/" + namespace

    # Handle both ASSAI instance and Flask app
    flask_app = app.app if hasattr(app, 'app') else app

    def route(url_pat, *args, **kwargs):
        if not url_pat.startswith("/"):
            url_pat = "/" + url_pat
        return flask_app.route(namespace + url_pat, *args, **kwargs)
    return route


def pil_to_base64_png(img):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


cached_pipeline = {}

_LOADING = object()


def cached(key):
    global cached_pipeline

    def decorator(fun):
        def _(*args, **kwargs):
            if pipe := cached_pipeline.get(key): 
                if pipe is _LOADING:
                    raise RuntimeError("Pipe already loading")
 
                return pipe
            
            cached_pipeline[key] = _LOADING
            pipe = fun(*args, **kwargs)
            cached_pipeline[key] = pipe
            return pipe

        return _

    return decorator


original_stdout = sys.stdout


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

            if line != "":
                self.push(line)

            self.prev = []
            self.write(tail)
            
    def flush(self):
        pass


class StreamRouter:
    def __init__(self, push):
        self.route = push
        self._local = threading.local()

    @property
    def prev(self):
        if not hasattr(self._local, "prev"):
            self._local.prev = []   # each thread gets its own list object
        return self._local.prev

    def write(self, msg):
        thread_id = threading.get_ident()
        msg = msg.replace("\x1b[A", "\n").replace("\r", "\n")

        if "\n" not in msg:
            self.prev.append(msg)
        else:
            while msg:
                head, _, msg = msg.partition("\n")
                self.prev.append(head)
                line = "".join(self.prev)

                if line != "":
                    self.route(thread_id, line)

                self.prev.clear()
            
    def flush(self):
        pass

socket_io_lock = Lock()


@contextmanager
def capture_progress(app, action_id=0):
    global socket_io_lock

    old_out = sys.stdout
    old_err = sys.stderr 
    was_replaced = False

    with socket_io_lock:
        if not isinstance(sys.stdout, SocketIOBuffer):
            sys.stdout = SocketIOBuffer(push=lambda line: app.message("stdout", {"id": action_id, "line": line}))
            sys.stderr = SocketIOBuffer(push=lambda line: app.message("stderr", {"id": action_id, "line": line}))
            was_replaced = True

    yield

    if was_replaced:
        with socket_io_lock:
            sys.stdout = old_out
            sys.stderr = old_err


def websocket_pusher(app, action_id):
    def channel_push(chanel):
        def push(thread_id, line):
            app.message(chanel, {"id": action_id, "thread_id": thread_id, "line": line})
        return push
    return channel_push

def stdout_pusher(file, action_id):
    def channel_push(chanel):
        def push(thread_id, line):
            print(chanel, {"id": action_id, "thread_id": thread_id, "line": line}, file=file)
        return push
    return channel_push


@contextmanager
def capture_progress_thread(pusher_factory, action_id=0):
    global socket_io_lock

    old_out = sys.stdout
    old_err = sys.stderr 
    was_replaced = False


    with socket_io_lock:
        if not isinstance(sys.stdout, SocketIOBuffer):
            sys.stdout = StreamRouter(push=pusher_factory("stdout"))
            sys.stderr = StreamRouter(push=pusher_factory("stderr"))
            was_replaced = True

    yield

    if was_replaced:
        with socket_io_lock:
            sys.stdout = old_out
            sys.stderr = old_err


def test_routing_stream():
    from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

    def worker_sim(i):
        for j in range(10):
            print(f"Worker {i}")

    pusher = stdout_pusher(sys.stdout, 1)

    with capture_progress_thread(pusher):
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = []
            for i in range(10):
                futures.append(ex.submit(worker_sim, i))
            wait(futures, return_when=ALL_COMPLETED, timeout=10)


if __name__ == "__main__":
    test_routing_stream()
