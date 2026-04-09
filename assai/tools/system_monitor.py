"""Lightweight system telemetry using ``voir`` instruments.

This module is intentionally kept free of heavy dependencies (torch,
PIL, etc.) so it can be imported by the worker without pulling in the
full legacy ``assai.tools`` package.
"""

from __future__ import annotations

import time

_observe_util = None


def system_monitor():
    """Return a callable that produces a telemetry snapshot dict.

    On first call, initializes the ``voir`` monitoring instruments.
    Subsequent calls return the same callable.
    """
    global _observe_util
    if _observe_util is not None:
        return _observe_util

    import multiprocessing as mp

    from voir.instruments.cpu import cpu_monitor
    from voir.instruments.gpu import gpu_monitor, select_backend
    from voir.instruments.io import io_monitor
    from voir.instruments.network import network_monitor

    cpu_fn = cpu_monitor()
    select_backend()
    gpu_fn = gpu_monitor()
    n_cpu = mp.cpu_count()
    network_fn = network_monitor()
    io_fn = io_monitor()

    def observe():
        cpu = cpu_fn()
        cpu["load"] = cpu["load"] / n_cpu
        return {
            "cpu": cpu,
            "gpu": gpu_fn(),
            "time": time.time(),
            "network": network_fn(),
            "disk": io_fn(),
        }

    _observe_util = observe
    return observe
