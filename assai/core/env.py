"""Environment setup for cache paths and hardware-specific defaults.

Call :func:`apply_env` early (e.g. at CLI startup) to set the env vars
for the current process.  :func:`build_env` returns a copy of the env
suitable for passing to ``subprocess.Popen(env=…)``.
"""

from __future__ import annotations

import os

_MILABENCH_VARS: dict[str, str] = {
    "XDG_CACHE_HOME": "/opt/milabench/cache",
    "HF_HOME": "/opt/milabench",
    "HF_HUB_CACHE": "/opt/milabench/data/hub",
    "HF_DATASETS_CACHE": "/opt/milabench/data",
    "FLASHINFER_CACHE_DIR": "/opt/milabench/cache/flashinfer",
}


def apply_env() -> None:
    """Set cache env vars in the current process if milabench paths exist."""
    if os.path.isdir("/opt/milabench"):
        for key, value in _MILABENCH_VARS.items():
            os.environ.setdefault(key, value)


def build_env() -> dict[str, str]:
    """Return a copy of ``os.environ`` with cache vars injected."""
    env = os.environ.copy()
    if os.path.isdir("/opt/milabench"):
        for key, value in _MILABENCH_VARS.items():
            env.setdefault(key, value)
    return env
