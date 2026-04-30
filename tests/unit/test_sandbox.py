"""Unit tests for the sandbox subsystem (Podman / container backend).

Covers:
- ContainerSandbox lifecycle, image building, run-command construction
- Sandbox ABC and create_sandbox factory
- SandboxProxy decision logic and proxy plumbing
- SandboxConfig construction and serialisation helpers
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import fields
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from acai.orchestrator.config import SandboxConfig
from acai.worker.sandbox.base import Sandbox, create_sandbox, _BACKEND_ALIASES
from acai.worker.sandbox.container import (
    ContainerSandbox,
    _detect_runtime,
    _find_repo_root,
    _is_rootless,
)
from acai.worker.sandbox_proxy import SandboxProxy


# ======================================================================
# Helpers
# ======================================================================

def _mock_run(*, returncode=0, stdout="", stderr=""):
    """Build a MagicMock that quacks like subprocess.CompletedProcess."""
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


# ======================================================================
# _detect_runtime
# ======================================================================


class TestDetectRuntime:
    def test_podman_preferred(self):
        with patch("shutil.which", side_effect=lambda c: f"/usr/bin/{c}" if c == "podman" else None):
            assert _detect_runtime() == "podman"

    def test_docker_fallback(self):
        with patch("shutil.which", side_effect=lambda c: "/usr/bin/docker" if c == "docker" else None):
            assert _detect_runtime() == "docker"

    def test_neither_available(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="Neither podman nor docker"):
                _detect_runtime()


# ======================================================================
# _find_repo_root
# ======================================================================


class TestFindRepoRoot:
    def test_finds_containerfile(self, tmp_path):
        (tmp_path / "Containerfile").touch()
        inner = tmp_path / "a" / "b" / "c"
        inner.mkdir(parents=True)
        with patch("os.path.abspath", return_value=str(inner)):
            result = _find_repo_root()
        assert result == str(tmp_path)

    def test_returns_none_when_missing(self, tmp_path):
        inner = tmp_path / "a" / "b"
        inner.mkdir(parents=True)
        with patch("os.path.abspath", return_value=str(inner)):
            assert _find_repo_root() is None


# ======================================================================
# _is_rootless
# ======================================================================


class TestIsRootless:
    def test_podman_rootless_true(self):
        with patch("subprocess.run", return_value=_mock_run(stdout="true\n")):
            assert _is_rootless("podman") is True

    def test_podman_rootless_false(self):
        with patch("subprocess.run", return_value=_mock_run(stdout="false\n")):
            assert _is_rootless("podman") is False

    def test_docker_always_false(self):
        assert _is_rootless("docker") is False

    def test_podman_info_failure_falls_back_to_uid(self):
        with patch("subprocess.run", side_effect=Exception("timeout")), \
             patch("os.getuid", return_value=1000):
            assert _is_rootless("podman") is True

    def test_podman_info_failure_root_uid(self):
        with patch("subprocess.run", side_effect=Exception("timeout")), \
             patch("os.getuid", return_value=0):
            assert _is_rootless("podman") is False


# ======================================================================
# ContainerSandbox — construction
# ======================================================================


class TestContainerSandboxInit:
    def test_default_image(self):
        with patch("acai.worker.sandbox.container._detect_runtime", return_value="docker"), \
             patch("acai.worker.sandbox.container._is_rootless", return_value=False):
            sb = ContainerSandbox()
        assert sb.image == "acai-sandbox"
        assert sb.runtime == "docker"

    def test_podman_qualifies_image(self):
        with patch("acai.worker.sandbox.container._detect_runtime", return_value="podman"), \
             patch("acai.worker.sandbox.container._is_rootless", return_value=True):
            sb = ContainerSandbox(image="acai-sandbox")
        assert sb.image == "localhost/acai-sandbox"

    def test_podman_preserves_qualified_image(self):
        with patch("acai.worker.sandbox.container._detect_runtime", return_value="podman"), \
             patch("acai.worker.sandbox.container._is_rootless", return_value=True):
            sb = ContainerSandbox(image="ghcr.io/org/acai-sandbox")
        assert sb.image == "ghcr.io/org/acai-sandbox"

    def test_explicit_runtime_overrides_detection(self):
        sb = ContainerSandbox(runtime="podman", rootless=False)
        assert sb.runtime == "podman"

    def test_custom_port(self):
        sb = ContainerSandbox(runtime="docker", container_port=5555, rootless=False)
        assert sb.container_port == 5555

    def test_rootless_auto_detected_for_podman(self):
        with patch("acai.worker.sandbox.container._detect_runtime", return_value="podman"), \
             patch("acai.worker.sandbox.container._is_rootless", return_value=True):
            sb = ContainerSandbox()
        assert sb.rootless is True

    def test_rootless_false_for_docker(self):
        with patch("acai.worker.sandbox.container._detect_runtime", return_value="docker"), \
             patch("acai.worker.sandbox.container._is_rootless", return_value=False):
            sb = ContainerSandbox()
        assert sb.rootless is False

    def test_rootless_explicit_override(self):
        sb = ContainerSandbox(runtime="podman", rootless=False)
        assert sb.rootless is False
        sb2 = ContainerSandbox(runtime="docker", rootless=True)
        assert sb2.rootless is True


# ======================================================================
# ContainerSandbox — properties (before start)
# ======================================================================


class TestContainerSandboxProperties:
    def test_running_false_before_start(self):
        sb = ContainerSandbox(runtime="docker")
        assert sb.running is False

    def test_endpoint_raises_before_start(self):
        sb = ContainerSandbox(runtime="docker")
        with pytest.raises(RuntimeError, match="sandbox not started"):
            _ = sb.endpoint


# ======================================================================
# ContainerSandbox — _build_run_cmd
# ======================================================================


class TestBuildRunCmd:
    def _sandbox(self, rootless=False) -> ContainerSandbox:
        return ContainerSandbox(
            image="acai-sandbox",
            container_port=9200,
            runtime="podman",
            rootless=rootless,
        )

    def test_basic_command(self):
        sb = self._sandbox()
        cmd = sb._build_run_cmd("acai-sandbox-test", "/home/user/project", None)
        assert cmd[:3] == ["podman", "run", "-d"]
        assert "--name" in cmd
        assert "acai-sandbox-test" in cmd
        assert "-p" in cmd
        assert ":9200" in cmd[cmd.index("-p") + 1]
        assert cmd[-1] == sb.image

    def test_volume_mount_uses_absolute_path(self, tmp_path):
        sb = self._sandbox()
        cmd = sb._build_run_cmd("test", str(tmp_path), None)
        mount = f"{tmp_path}:{tmp_path}"
        assert "-v" in cmd
        idx = cmd.index("-v")
        assert cmd[idx + 1] == mount

    def test_memory_limit(self):
        sb = self._sandbox()
        cfg = SandboxConfig(memory_limit="8G")
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--memory" in cmd
        idx = cmd.index("--memory")
        assert cmd[idx + 1] == "8G"

    def test_gpu_device(self):
        sb = self._sandbox()
        cfg = SandboxConfig(gpu=True)
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--device" in cmd
        idx = cmd.index("--device")
        assert cmd[idx + 1] == "nvidia.com/gpu=all"

    def test_network_none(self):
        sb = self._sandbox()
        cfg = SandboxConfig(network=False)
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

    def test_network_enabled_by_default(self):
        sb = self._sandbox()
        cfg = SandboxConfig()
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--network" not in cmd

    def test_readonly_paths(self):
        sb = self._sandbox()
        cfg = SandboxConfig(readonly_paths=["/data/models"])
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "/data/models:/data/models:ro" in cmd

    def test_writable_paths(self):
        sb = self._sandbox()
        cfg = SandboxConfig(writable_paths=["/scratch"])
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "/scratch:/scratch" in cmd

    def test_combined_options(self):
        sb = self._sandbox()
        cfg = SandboxConfig(
            memory_limit="2G",
            gpu=True,
            network=False,
            readonly_paths=["/data"],
            writable_paths=["/out"],
        )
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--memory" in cmd
        assert "--device" in cmd
        assert "--network" in cmd
        assert "/data:/data:ro" in cmd
        assert "/out:/out" in cmd

    # -- Rootless-specific tests --

    def test_rootless_adds_userns_keep_id(self):
        sb = self._sandbox(rootless=True)
        cmd = sb._build_run_cmd("test", "/w", None)
        assert "--userns=keep-id" in cmd

    def test_rootful_no_userns(self):
        sb = self._sandbox(rootless=False)
        cmd = sb._build_run_cmd("test", "/w", None)
        assert "--userns=keep-id" not in cmd

    def test_rootless_with_sandbox_config(self):
        sb = self._sandbox(rootless=True)
        cfg = SandboxConfig(memory_limit="2G", network=False)
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--userns=keep-id" in cmd
        assert "--memory" in cmd
        assert "--network" in cmd

    def test_rootless_gpu_uses_cdi(self):
        sb = self._sandbox(rootless=True)
        cfg = SandboxConfig(gpu=True)
        cmd = sb._build_run_cmd("test", "/w", cfg)
        assert "--userns=keep-id" in cmd
        assert "--device" in cmd
        assert "nvidia.com/gpu=all" in cmd[cmd.index("--device") + 1]


# ======================================================================
# ContainerSandbox — _ensure_image
# ======================================================================


class TestEnsureImage:
    def test_image_exists_skips_build(self):
        sb = ContainerSandbox(runtime="podman", image="localhost/acai-sandbox")
        with patch("subprocess.run", return_value=_mock_run(returncode=0)) as mock:
            sb._ensure_image()
        mock.assert_called_once_with(
            ["podman", "image", "inspect", "localhost/acai-sandbox"],
            capture_output=True, text=True,
        )

    def test_image_missing_triggers_build(self, tmp_path):
        containerfile = tmp_path / "Containerfile"
        containerfile.touch()
        sb = ContainerSandbox(runtime="podman", image="localhost/acai-sandbox")

        side_effects = [
            _mock_run(returncode=1),   # image inspect fails
            _mock_run(returncode=0),   # build succeeds
        ]
        with patch("subprocess.run", side_effect=side_effects) as mock, \
             patch("acai.worker.sandbox.container._find_repo_root", return_value=str(tmp_path)):
            sb._ensure_image()

        build_call = mock.call_args_list[1]
        assert build_call[0][0][:2] == ["podman", "build"]
        assert "-t" in build_call[0][0]

    def test_image_missing_no_containerfile_raises(self):
        sb = ContainerSandbox(runtime="podman", image="localhost/acai-sandbox")
        with patch("subprocess.run", return_value=_mock_run(returncode=1)), \
             patch("acai.worker.sandbox.container._find_repo_root", return_value=None):
            with pytest.raises(RuntimeError, match="not found and could not locate"):
                sb._ensure_image()

    def test_build_failure_raises(self, tmp_path):
        containerfile = tmp_path / "Containerfile"
        containerfile.touch()
        sb = ContainerSandbox(runtime="podman", image="localhost/acai-sandbox")

        side_effects = [
            _mock_run(returncode=1),
            _mock_run(returncode=1, stderr="build error"),
        ]
        with patch("subprocess.run", side_effect=side_effects), \
             patch("acai.worker.sandbox.container._find_repo_root", return_value=str(tmp_path)):
            with pytest.raises(RuntimeError, match="Failed to build"):
                sb._ensure_image()


# ======================================================================
# ContainerSandbox — _is_alive
# ======================================================================


class TestIsAlive:
    def test_alive_returns_true(self):
        sb = ContainerSandbox(runtime="podman")
        with patch("subprocess.run", return_value=_mock_run(stdout="true\n")):
            assert sb._is_alive("container-1") is True

    def test_not_alive_returns_false(self):
        sb = ContainerSandbox(runtime="podman")
        with patch("subprocess.run", return_value=_mock_run(stdout="false\n")):
            assert sb._is_alive("container-1") is False

    def test_inspect_failure_returns_false(self):
        sb = ContainerSandbox(runtime="podman")
        with patch("subprocess.run", return_value=_mock_run(returncode=1, stdout="")):
            assert sb._is_alive("container-1") is False


# ======================================================================
# ContainerSandbox — _kill_stale
# ======================================================================


class TestKillStale:
    def test_removes_alive_container(self):
        sb = ContainerSandbox(runtime="podman")
        with patch.object(sb, "_is_alive", return_value=True), \
             patch("subprocess.run") as mock:
            sb._kill_stale("old-sandbox")

        calls = mock.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0][:3] == ["podman", "stop", "-t"]
        assert calls[1][0][0][:3] == ["podman", "rm", "-f"]

    def test_just_rm_when_not_alive(self):
        sb = ContainerSandbox(runtime="podman")
        with patch.object(sb, "_is_alive", return_value=False), \
             patch("subprocess.run") as mock:
            sb._kill_stale("old-sandbox")

        assert len(mock.call_args_list) == 1
        assert mock.call_args_list[0][0][0][:3] == ["podman", "rm", "-f"]


# ======================================================================
# ContainerSandbox — _resolve_host_port
# ======================================================================


class TestResolveHostPort:
    def test_parses_ipv4_mapping(self):
        sb = ContainerSandbox(runtime="podman", rootless=False)
        with patch("subprocess.run", return_value=_mock_run(stdout="0.0.0.0:32768\n")):
            assert sb._resolve_host_port("test") == 32768

    def test_parses_ipv6_mapping(self):
        sb = ContainerSandbox(runtime="podman", rootless=False)
        with patch("subprocess.run", return_value=_mock_run(stdout="[::]:45678\n")):
            assert sb._resolve_host_port("test") == 45678

    def test_failure_raises(self):
        sb = ContainerSandbox(runtime="podman", rootless=False)
        with patch("subprocess.run", return_value=_mock_run(returncode=1, stderr="not found")):
            with pytest.raises(RuntimeError, match="failed to resolve port"):
                sb._resolve_host_port("test")

    def test_multiline_output_takes_first(self):
        """Rootless Podman returns both IPv4 and IPv6 lines."""
        sb = ContainerSandbox(runtime="podman", rootless=True)
        with patch("subprocess.run", return_value=_mock_run(
            stdout="0.0.0.0:43210\n[::]:43210\n"
        )):
            assert sb._resolve_host_port("test") == 43210

    def test_rootless_ipv6_only(self):
        sb = ContainerSandbox(runtime="podman", rootless=True)
        with patch("subprocess.run", return_value=_mock_run(stdout="[::]:55555\n")):
            assert sb._resolve_host_port("test") == 55555


# ======================================================================
# ContainerSandbox — _configure_git_identity
# ======================================================================


class TestConfigureGitIdentity:
    def test_sets_name_and_email(self):
        sb = ContainerSandbox(runtime="podman")
        sb._container_name = "sandbox-1"
        with patch("subprocess.run", return_value=_mock_run()) as mock:
            sb._configure_git_identity("coder")

        assert mock.call_count == 2
        name_cmd = mock.call_args_list[0][0][0]
        email_cmd = mock.call_args_list[1][0][0]
        assert name_cmd == [
            "podman", "exec", "sandbox-1",
            "git", "config", "--global", "user.name", "coder",
        ]
        assert email_cmd == [
            "podman", "exec", "sandbox-1",
            "git", "config", "--global", "user.email", "coder@acai.localhost",
        ]


# ======================================================================
# ContainerSandbox — _container_logs
# ======================================================================


class TestContainerLogs:
    def test_returns_logs(self):
        sb = ContainerSandbox(runtime="podman")
        sb._container_name = "sandbox-1"
        with patch("subprocess.run", return_value=_mock_run(stdout="line1\nline2\n")):
            assert sb._container_logs() == "line1\nline2"

    def test_no_container_returns_placeholder(self):
        sb = ContainerSandbox(runtime="podman")
        assert sb._container_logs() == "(no container)"


# ======================================================================
# ContainerSandbox — start (full flow, mocked)
# ======================================================================


class TestContainerSandboxStart:
    def _make_sandbox(self) -> ContainerSandbox:
        return ContainerSandbox(
            image="acai-sandbox",
            container_port=9200,
            runtime="podman",
        )

    def _patch_start_deps(self, sb, host_port=12345):
        """Patch internal methods called during start()."""
        return (
            patch.object(sb, "_kill_stale"),
            patch.object(sb, "_ensure_image"),
            patch.object(sb, "_resolve_host_port", return_value=host_port),
            patch.object(sb, "_configure_git_identity"),
            patch.object(sb, "_wait_healthy"),
            patch("subprocess.run", return_value=_mock_run()),
        )

    def test_start_sets_state(self):
        sb = self._make_sandbox()
        patches = self._patch_start_deps(sb)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            sb.start("/project", session_id="s1", agent_name="coder")

        assert sb._container_name == "acai-sandbox-s1"
        assert sb._host_port == 12345
        assert sb._project_path == "/project"

    def test_start_calls_subprocess_run(self):
        sb = self._make_sandbox()
        patches = self._patch_start_deps(sb)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5] as run_mock:
            sb.start("/project", session_id="s1")

        run_cmd = run_mock.call_args[0][0]
        assert run_cmd[:3] == ["podman", "run", "-d"]

    def test_start_run_failure_raises(self):
        sb = self._make_sandbox()
        with patch.object(sb, "_kill_stale"), \
             patch.object(sb, "_ensure_image"), \
             patch("subprocess.run", return_value=_mock_run(returncode=1, stderr="oom")):
            with pytest.raises(RuntimeError, match="podman run failed"):
                sb.start("/project", session_id="s1")

    def test_start_idempotent_same_path(self):
        sb = self._make_sandbox()
        sb._container_name = "acai-sandbox-s1"
        sb._project_path = "/project"
        with patch.object(sb, "_is_alive", return_value=True):
            sb.start("/project", session_id="s1")
        # should return without doing anything

    def test_start_restarts_on_path_change(self):
        sb = self._make_sandbox()
        sb._container_name = "acai-sandbox-s1"
        sb._project_path = "/old-project"
        patches = self._patch_start_deps(sb)
        with patch.object(sb, "_is_alive", return_value=True), \
             patch.object(sb, "stop") as stop_mock, \
             patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            sb.start("/new-project", session_id="s1")
        stop_mock.assert_called_once()


# ======================================================================
# ContainerSandbox — stop
# ======================================================================


class TestContainerSandboxStop:
    def test_stop_cleans_up(self):
        sb = ContainerSandbox(runtime="podman")
        sb._container_name = "sandbox-1"
        sb._host_port = 12345
        sb._project_path = "/project"
        with patch("subprocess.run") as mock:
            sb.stop()

        assert sb._container_name is None
        assert sb._host_port is None
        assert sb._project_path is None

        calls = mock.call_args_list
        assert calls[0][0][0] == ["podman", "stop", "-t", "5", "sandbox-1"]
        assert calls[1][0][0] == ["podman", "rm", "-f", "sandbox-1"]

    def test_stop_noop_when_not_started(self):
        sb = ContainerSandbox(runtime="podman")
        with patch("subprocess.run") as mock:
            sb.stop()
        mock.assert_not_called()


# ======================================================================
# ContainerSandbox — _wait_healthy
# ======================================================================


class TestWaitHealthy:
    def test_healthy_on_first_check(self):
        sb = ContainerSandbox(runtime="podman")
        sb._host_port = 9200
        mock_resp = MagicMock(status_code=200)
        with patch("requests.get", return_value=mock_resp):
            sb._wait_healthy(retries=3, interval=0.01)

    def test_healthy_after_retries(self):
        sb = ContainerSandbox(runtime="podman")
        sb._host_port = 9200
        sb._container_name = "test"
        import requests as _req
        side_effects = [
            _req.ConnectionError(),
            _req.ConnectionError(),
            MagicMock(status_code=200),
        ]
        with patch("requests.get", side_effect=side_effects), \
             patch.object(sb, "_is_alive", return_value=True), \
             patch("time.sleep"):
            sb._wait_healthy(retries=5, interval=0.01)

    def test_container_exits_before_healthy(self):
        sb = ContainerSandbox(runtime="podman")
        sb._host_port = 9200
        sb._container_name = "test"
        import requests as _req
        with patch("requests.get", side_effect=_req.ConnectionError()), \
             patch.object(sb, "_is_alive", return_value=False), \
             patch.object(sb, "_container_logs", return_value="crash log"):
            with pytest.raises(RuntimeError, match="exited before becoming healthy"):
                sb._wait_healthy(retries=3, interval=0.01)

    def test_timeout_with_logs(self):
        sb = ContainerSandbox(runtime="podman")
        sb._host_port = 9200
        sb._container_name = "test"
        import requests as _req
        with patch("requests.get", side_effect=_req.ConnectionError()), \
             patch.object(sb, "_is_alive", return_value=True), \
             patch.object(sb, "_container_logs", return_value="some logs"), \
             patch("time.sleep"):
            with pytest.raises(RuntimeError, match="did not become healthy"):
                sb._wait_healthy(retries=2, interval=0.01)


# ======================================================================
# ContainerSandbox — endpoint
# ======================================================================


class TestContainerEndpoint:
    def test_returns_correct_url(self):
        sb = ContainerSandbox(runtime="podman")
        sb._host_port = 32768
        assert sb.endpoint == "http://127.0.0.1:32768"

    def test_running_delegates_to_is_alive(self):
        sb = ContainerSandbox(runtime="podman")
        sb._container_name = "test"
        with patch.object(sb, "_is_alive", return_value=True):
            assert sb.running is True
        with patch.object(sb, "_is_alive", return_value=False):
            assert sb.running is False


# ======================================================================
# create_sandbox factory
# ======================================================================


class TestCreateSandbox:
    def test_podman_creates_container_sandbox(self):
        sb = create_sandbox(SandboxConfig(type="podman", image="test"))
        assert isinstance(sb, ContainerSandbox)
        assert sb.runtime == "podman"

    def test_docker_creates_container_sandbox(self):
        sb = create_sandbox(SandboxConfig(type="docker", image="test", runtime="docker"))
        assert isinstance(sb, ContainerSandbox)
        assert sb.runtime == "docker"

    def test_container_alias(self):
        with patch("acai.worker.sandbox.container._detect_runtime", return_value="podman"):
            sb = create_sandbox(SandboxConfig(type="container", image="test"))
        assert isinstance(sb, ContainerSandbox)

    def test_bubblewrap_alias(self):
        from acai.worker.sandbox.experimental.bubblewrap import BubblewrapSandbox
        for name in ("bubblewrap", "bwrap"):
            sb = create_sandbox(SandboxConfig(type=name))
            assert isinstance(sb, BubblewrapSandbox)

    def test_nsjail_alias(self):
        from acai.worker.sandbox.experimental.nsjail import NsjailSandbox
        sb = create_sandbox(SandboxConfig(type="nsjail"))
        assert isinstance(sb, NsjailSandbox)

    def test_firecracker_alias(self):
        from acai.worker.sandbox.experimental.firecracker import FirecrackerSandbox
        sb = create_sandbox(SandboxConfig(type="firecracker"))
        assert isinstance(sb, FirecrackerSandbox)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown sandbox backend"):
            create_sandbox(SandboxConfig(type="magic"))

    def test_case_insensitive(self):
        sb = create_sandbox(SandboxConfig(type="PODMAN", image="test"))
        assert isinstance(sb, ContainerSandbox)

    def test_mcp_port_forwarded(self):
        sb = create_sandbox(SandboxConfig(type="podman", image="test", mcp_port=5555))
        assert isinstance(sb, ContainerSandbox)
        assert sb.container_port == 5555

    def test_rootless_forwarded(self):
        sb = create_sandbox(SandboxConfig(type="podman", image="test", rootless=True))
        assert isinstance(sb, ContainerSandbox)
        assert sb.rootless is True

    def test_rootless_false_forwarded(self):
        sb = create_sandbox(SandboxConfig(type="podman", image="test", rootless=False))
        assert isinstance(sb, ContainerSandbox)
        assert sb.rootless is False


# ======================================================================
# _BACKEND_ALIASES
# ======================================================================


class TestBackendAliases:
    def test_all_aliases_present(self):
        expected = {"docker", "podman", "container", "bubblewrap", "bwrap", "nsjail", "firecracker"}
        assert set(_BACKEND_ALIASES) == expected

    def test_docker_podman_map_to_container(self):
        assert _BACKEND_ALIASES["docker"] == "container"
        assert _BACKEND_ALIASES["podman"] == "container"
        assert _BACKEND_ALIASES["container"] == "container"

    def test_bwrap_alias(self):
        assert _BACKEND_ALIASES["bwrap"] == "bubblewrap"


# ======================================================================
# SandboxConfig
# ======================================================================


class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.type == "podman"
        assert cfg.network is True
        assert cfg.gpu is False
        assert cfg.timeout == 120
        assert cfg.memory_limit == "4G"
        assert cfg.image == "acai-sandbox"
        assert cfg.runtime == "podman"
        assert cfg.rootless is True
        assert cfg.mcp_port == 9200

    def test_from_dict(self):
        cfg = SandboxConfig.from_dict({
            "type": "docker",
            "image": "custom-image",
            "memory_limit": "8G",
            "gpu": True,
        })
        assert cfg.type == "docker"
        assert cfg.image == "custom-image"
        assert cfg.memory_limit == "8G"
        assert cfg.gpu is True

    def test_from_dict_ignores_unknown_keys(self):
        cfg = SandboxConfig.from_dict({
            "type": "podman",
            "nonexistent_field": "ignored",
        })
        assert cfg.type == "podman"
        assert not hasattr(cfg, "nonexistent_field")

    def test_fields_have_backends_metadata(self):
        for f in fields(SandboxConfig):
            assert "backends" in f.metadata, f"{f.name} missing backends metadata"

    def test_container_specific_fields(self):
        container_fields = SandboxConfig.fields_for_backend("container")
        assert "image" in container_fields
        assert "runtime" in container_fields
        assert "rootless" in container_fields

    def test_list_defaults_are_independent(self):
        cfg1 = SandboxConfig()
        cfg2 = SandboxConfig()
        cfg1.writable_paths.append("/foo")
        assert cfg2.writable_paths == []

    def test_network_disabled(self):
        cfg = SandboxConfig(network=False)
        assert cfg.network is False

    def test_rootless_from_dict(self):
        cfg = SandboxConfig.from_dict({"rootless": False})
        assert cfg.rootless is False

    def test_rootless_default_true(self):
        cfg = SandboxConfig()
        assert cfg.rootless is True


# ======================================================================
# SandboxProxy — should_proxy
# ======================================================================


class TestSandboxProxyShouldProxy:
    def _proxy(self, type_="podman", predicate=None) -> SandboxProxy:
        cfg = SandboxConfig(type=type_)
        return SandboxProxy(default_config=cfg, sandbox_predicate=predicate)

    def test_type_none_always_false(self):
        proxy = self._proxy(type_="none")
        assert proxy.should_proxy("shell.run", {"uses_sandbox": True}) is False

    def test_no_predicate_returns_false(self):
        proxy = self._proxy()
        assert proxy.should_proxy("shell.run", {"uses_sandbox": True}) is False

    def test_predicate_false_returns_false(self):
        proxy = self._proxy(predicate=lambda _: False)
        assert proxy.should_proxy("shell.run", {"uses_sandbox": True}) is False

    def test_predicate_true_and_uses_sandbox(self):
        proxy = self._proxy(predicate=lambda _: True)
        assert proxy.should_proxy("shell.run", {"uses_sandbox": True}) is True

    def test_predicate_true_no_uses_sandbox(self):
        proxy = self._proxy(predicate=lambda _: True)
        assert proxy.should_proxy("shell.run", {}) is False
        assert proxy.should_proxy("shell.run", None) is False

    def test_already_running_bypasses_ctx_check(self):
        proxy = self._proxy(predicate=lambda _: True)
        mock_sb = MagicMock()
        mock_sb.running = True
        proxy._sandbox = mock_sb
        assert proxy.should_proxy("shell.run", {}) is True


# ======================================================================
# SandboxProxy — lifecycle
# ======================================================================


class TestSandboxProxyLifecycle:
    def test_initial_state(self):
        proxy = SandboxProxy(
            default_config=SandboxConfig(type="podman"),
            sandbox_predicate=lambda _: True,
        )
        assert proxy.running is False
        assert proxy.endpoint is None
        assert proxy.active_project is None

    def test_stop_calls_sandbox_stop(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        mock_sb = MagicMock()
        proxy._sandbox = mock_sb
        proxy._active_project = "/projects/a"
        proxy.stop()
        mock_sb.stop.assert_called_once()
        assert proxy._sandbox is None
        assert proxy.active_project is None

    def test_stop_noop_when_no_sandbox(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        proxy.stop()

    def test_ensure_started_skips_when_running_same_project(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        mock_sb = MagicMock()
        mock_sb.running = True
        proxy._sandbox = mock_sb
        proxy._active_project = os.path.abspath("/projects/a")
        proxy._ensure_started({"project_path": "/projects/a"})
        mock_sb.start.assert_not_called()

    def test_ensure_started_skips_type_none(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="none"))
        proxy._ensure_started({})
        assert proxy._sandbox is None

    def test_ensure_started_creates_and_starts(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        mock_sb = MagicMock()
        with patch("acai.worker.sandbox.create_sandbox", return_value=mock_sb):
            proxy._ensure_started({
                "conversation": "conv-1",
                "agent_name": "coder",
                "project_path": "/projects/a",
            })
        mock_sb.start.assert_called_once()
        call_kwargs = mock_sb.start.call_args
        assert call_kwargs.kwargs["session_id"] == "conv-1"
        assert call_kwargs.kwargs["agent_name"] == "coder"
        assert proxy.active_project == os.path.abspath("/projects/a")

    def test_ensure_started_recycles_on_project_change(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        old_sb = MagicMock()
        old_sb.running = True
        proxy._sandbox = old_sb
        proxy._active_project = os.path.abspath("/projects/a")

        new_sb = MagicMock()
        with patch("acai.worker.sandbox.create_sandbox", return_value=new_sb):
            proxy._ensure_started({"project_path": "/projects/b"})

        old_sb.stop.assert_called_once()
        new_sb.start.assert_called_once()
        assert new_sb.start.call_args[0][0] == os.path.abspath("/projects/b")
        assert proxy.active_project == os.path.abspath("/projects/b")

    def test_ensure_started_keeps_running_for_same_project(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        mock_sb = MagicMock()
        mock_sb.running = True
        proxy._sandbox = mock_sb
        proxy._active_project = os.path.abspath("/projects/x")
        proxy._ensure_started({"project_path": "/projects/x"})
        mock_sb.stop.assert_not_called()
        mock_sb.start.assert_not_called()


# ======================================================================
# SandboxProxy — proxy_call
# ======================================================================


class TestSandboxProxyCall:
    @pytest.mark.asyncio
    async def test_startup_failure_returns_error_stream(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        with patch.object(proxy, "_ensure_started", side_effect=RuntimeError("boom")):
            resp = await proxy.proxy_call("shell.run", {"command": "ls"})

        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = "".join(chunks)
        assert "error" in body
        assert "boom" in body

    @pytest.mark.asyncio
    async def test_not_running_after_start_returns_error(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        with patch.object(proxy, "_ensure_started"):
            resp = await proxy.proxy_call("shell.run", {"command": "ls"})

        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        body = "".join(chunks)
        assert "not running" in body

    @pytest.mark.asyncio
    async def test_successful_proxy_relays_response(self):
        proxy = SandboxProxy(default_config=SandboxConfig(type="podman"))
        mock_sb = MagicMock()
        mock_sb.running = True
        mock_sb.endpoint = "http://127.0.0.1:9200"
        proxy._sandbox = mock_sb

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content.iter_any = AsyncMock(return_value=AsyncIterator([
            b'event: result\ndata: {"result": "ok"}\n\n',
        ]))

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=mock_response)
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        with patch.object(proxy, "_ensure_started"), \
             patch("aiohttp.ClientSession", return_value=mock_session):
            resp = await proxy.proxy_call("shell.run", {"command": "ls"}, {"uses_sandbox": True})

        assert resp.media_type == "text/event-stream"


# ======================================================================
# Experimental backends — minimal smoke tests
# ======================================================================


class TestExperimentalBackends:
    """Bubblewrap, nsjail, and firecracker are experimental.

    These tests just verify construction and that start() raises
    NotImplementedError where applicable.
    """

    def test_bubblewrap_start_raises(self):
        from acai.worker.sandbox.experimental.bubblewrap import BubblewrapSandbox
        sb = BubblewrapSandbox()
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            sb.start("/project")

    def test_bubblewrap_not_running_by_default(self):
        from acai.worker.sandbox.experimental.bubblewrap import BubblewrapSandbox
        sb = BubblewrapSandbox()
        assert sb.running is False

    def test_bubblewrap_endpoint_raises_when_not_running(self):
        from acai.worker.sandbox.experimental.bubblewrap import BubblewrapSandbox
        sb = BubblewrapSandbox()
        with pytest.raises(RuntimeError, match="not started"):
            _ = sb.endpoint

    def test_nsjail_start_raises(self):
        from acai.worker.sandbox.experimental.nsjail import NsjailSandbox
        sb = NsjailSandbox()
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            sb.start("/project")

    def test_nsjail_not_running_by_default(self):
        from acai.worker.sandbox.experimental.nsjail import NsjailSandbox
        sb = NsjailSandbox()
        assert sb.running is False

    def test_firecracker_construction(self):
        from acai.worker.sandbox.experimental.firecracker import FirecrackerSandbox
        sb = FirecrackerSandbox(vcpu_count=4, mem_size_mib=2048)
        assert sb.vcpu_count == 4
        assert sb.mem_size_mib == 2048

    def test_firecracker_not_running_by_default(self):
        from acai.worker.sandbox.experimental.firecracker import FirecrackerSandbox
        sb = FirecrackerSandbox()
        assert sb.running is False

    def test_firecracker_endpoint_raises_when_not_started(self):
        from acai.worker.sandbox.experimental.firecracker import FirecrackerSandbox
        sb = FirecrackerSandbox()
        with pytest.raises(RuntimeError, match="not started"):
            _ = sb.endpoint


# ======================================================================
# Firecracker — _parse_memory_mib
# ======================================================================


class TestParseMemoryMib:
    def test_gigabytes(self):
        from acai.worker.sandbox.experimental.firecracker import _parse_memory_mib
        assert _parse_memory_mib("4G") == 4096
        assert _parse_memory_mib("1.5G") == 1536

    def test_megabytes(self):
        from acai.worker.sandbox.experimental.firecracker import _parse_memory_mib
        assert _parse_memory_mib("512M") == 512

    def test_plain_integer(self):
        from acai.worker.sandbox.experimental.firecracker import _parse_memory_mib
        assert _parse_memory_mib("2048") == 2048

    def test_fallback(self):
        from acai.worker.sandbox.experimental.firecracker import _parse_memory_mib
        assert _parse_memory_mib("invalid") == 1024

    def test_case_insensitive(self):
        from acai.worker.sandbox.experimental.firecracker import _parse_memory_mib
        assert _parse_memory_mib("4g") == 4096
        assert _parse_memory_mib("512m") == 512


# ======================================================================
# Async helpers
# ======================================================================


class AsyncIterator:
    """Minimal async iterator for testing aiohttp streaming."""
    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration
