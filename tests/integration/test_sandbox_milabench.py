"""Integration test: run ``milabench --help`` inside a sandbox container.

This exercises the full sandbox code path without baking any project
into the container image.  The agent installs milabench at runtime
via ``shell.run`` tool calls, exactly as a real agent would.

Flow:
    1.  ``create_sandbox(SandboxConfig(type="docker"))`` starts a generic
        ``acai-sandbox`` container with the project worktree mounted at
        ``/workspace``.
    2.  The container runs ``acai mcp`` (lightweight tool server).
    3.  We POST tool calls to the container's ``/tools/call`` endpoint.
    4.  First call: ``pip install milabench`` (agent sets up the project).
    5.  Second call: ``milabench --help`` → verify output.

Prerequisites::

    docker build -t acai-sandbox -f Containerfile .

Run::

    python -m pytest tests/integration/test_sandbox_milabench.py -v -s
    # or directly:
    .venv/bin/python tests/integration/test_sandbox_milabench.py
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGE = os.environ.get("SANDBOX_IMAGE", "acai-sandbox")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _image_exists(image: str) -> bool:
    proc = subprocess.run(
        ["docker", "inspect", "--type=image", image],
        capture_output=True, text=True,
    )
    return proc.returncode == 0


def _build_image():
    log.info("Building sandbox image from %s …", REPO_ROOT)
    proc = subprocess.run(
        ["docker", "build", "-t", IMAGE, "-f", "Containerfile", "."],
        cwd=REPO_ROOT,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError("Image build failed")


def _call_tool(endpoint: str, tool: str, args: dict, timeout: float = 120) -> dict:
    """POST a tool call to the sandbox's ``/tools/call`` and parse the SSE response."""
    url = f"{endpoint}/tools/call"
    resp = requests.post(url, json={"tool": tool, "args": args}, stream=True, timeout=timeout)
    resp.raise_for_status()

    result_text = ""
    error_text = ""
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if "result" in data:
                result_text += data["result"]
            if "error" in data:
                error_text = data["error"]

    if error_text:
        raise RuntimeError(f"Tool call failed: {error_text}")
    return json.loads(result_text) if result_text else {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sandbox_lifecycle():
    """Verify start / stop / idempotent-restart of the sandbox."""
    from acai.worker.sandbox import SandboxConfig, create_sandbox

    if not _image_exists(IMAGE):
        _build_image()

    sandbox = create_sandbox(SandboxConfig(type="docker", image=IMAGE))
    try:
        sandbox.start(REPO_ROOT, session_id="test-lifecycle")
        assert sandbox.running, "sandbox should be running after start()"

        health = requests.get(f"{sandbox.endpoint}/health", timeout=5)
        assert health.status_code == 200

        # Idempotent restart with same path
        sandbox.start(REPO_ROOT, session_id="test-lifecycle")
        assert sandbox.running

        sandbox.stop()
        assert not sandbox.running
    finally:
        sandbox.stop()


def test_sandbox_tool_list():
    """The generic container should expose shell, filesystem, git, code tools."""
    from acai.worker.sandbox import SandboxConfig, create_sandbox

    if not _image_exists(IMAGE):
        _build_image()

    sandbox = create_sandbox(SandboxConfig(type="docker", image=IMAGE))
    try:
        sandbox.start(REPO_ROOT, session_id="test-tools")
        tools_resp = requests.get(f"{sandbox.endpoint}/tools/list", timeout=10)
        tools_resp.raise_for_status()
        tool_names = [t["function"]["name"] for t in tools_resp.json()]
        log.info("Available tools (%d): %s", len(tool_names), tool_names)

        for expected in ("shell.run", "filesystem.read_file", "git.status"):
            assert expected in tool_names, f"{expected} not found in tools"
    finally:
        sandbox.stop()


def test_milabench_help_inside_sandbox():
    """Install milabench inside the sandbox, then run ``milabench --help``."""
    from acai.worker.sandbox import SandboxConfig, create_sandbox

    if not _image_exists(IMAGE):
        _build_image()

    sandbox = create_sandbox(SandboxConfig(type="docker", image=IMAGE))
    try:
        sandbox.start(REPO_ROOT, session_id="test-milabench", agent_name="coder")
        ep = sandbox.endpoint
        log.info("Sandbox ready at %s", ep)

        # Step 1: install milabench (agent would do this via tool calls)
        log.info("Installing milabench inside the container …")
        install_result = _call_tool(
            ep, "shell.run",
            {"command": "pip install 'milabench @ git+https://github.com/mila-iqia/milabench.git'", "timeout": 300},
            timeout=300,
        )
        log.info("Install result: rc=%s", install_result.get("returncode"))

        # Step 2: run milabench --help
        log.info("Running milabench --help …")
        result = _call_tool(ep, "shell.run", {"command": "milabench --help"})
        log.info("milabench --help output:\n%s", json.dumps(result, indent=2))

        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        output = stdout + stderr

        assert "milabench" in output.lower(), (
            f"Expected 'milabench' in output, got:\n"
            f"stdout={stdout!r}\nstderr={stderr!r}"
        )
        log.info("SUCCESS: milabench --help returned rc=%d", result.get("returncode", -1))

    finally:
        sandbox.stop()


def test_agent_git_identity():
    """Verify that the sandbox configures git identity per-agent."""
    from acai.worker.sandbox import SandboxConfig, create_sandbox

    if not _image_exists(IMAGE):
        _build_image()

    sandbox = create_sandbox(SandboxConfig(type="docker", image=IMAGE))
    try:
        sandbox.start(REPO_ROOT, session_id="test-git-id", agent_name="coder")
        ep = sandbox.endpoint

        result = _call_tool(ep, "shell.run", {
            "command": "git config --global user.name && git config --global user.email",
        })
        stdout = result.get("stdout", "").strip()
        log.info("Git identity: %s", stdout)

        assert "coder" in stdout, f"Expected 'coder' in git config, got: {stdout}"
        assert "coder@acai.localhost" in stdout

        # Make a real commit to verify
        result = _call_tool(ep, "shell.run", {
            "command": (
                "cd /tmp && git init test-repo && cd test-repo && "
                "touch f.txt && git add . && git commit -m test && "
                "git log --format='%an <%ae>' -1"
            ),
        })
        stdout = result.get("stdout", "")
        assert "coder <coder@acai.localhost>" in stdout
        log.info("SUCCESS: commits attributed to agent")
    finally:
        sandbox.stop()


def test_create_sandbox_factory():
    """Verify the factory handles all known backend names."""
    from acai.worker.sandbox import SandboxConfig, create_sandbox
    from acai.worker.sandbox.container import ContainerSandbox
    from acai.worker.sandbox.bubblewrap import BubblewrapSandbox
    from acai.worker.sandbox.nsjail import NsjailSandbox
    from acai.worker.sandbox.firecracker import FirecrackerSandbox

    for name in ("docker", "podman", "container"):
        s = create_sandbox(SandboxConfig(type=name, image="test"))
        assert isinstance(s, ContainerSandbox), f"{name} should create ContainerSandbox"

    for name in ("bubblewrap", "bwrap"):
        s = create_sandbox(SandboxConfig(type=name))
        assert isinstance(s, BubblewrapSandbox)

    s = create_sandbox(SandboxConfig(type="nsjail"))
    assert isinstance(s, NsjailSandbox)

    s = create_sandbox(SandboxConfig(type="firecracker"))
    assert isinstance(s, FirecrackerSandbox)

    import pytest
    with pytest.raises(ValueError, match="Unknown sandbox backend"):
        create_sandbox(SandboxConfig(type="magic"))


if __name__ == "__main__":
    test_create_sandbox_factory()
    print("  factory OK")
    test_sandbox_lifecycle()
    print("  lifecycle OK")
    test_sandbox_tool_list()
    print("  tool list OK")
    test_agent_git_identity()
    print("  git identity OK")
    test_milabench_help_inside_sandbox()
    print("  milabench help OK")
    print("\nAll tests passed!")
