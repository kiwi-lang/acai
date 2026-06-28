"""Tests for acai.worker.system_monitor — lightweight system telemetry."""

from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def reset_observe_util():
    """Reset the module-level singleton before each test."""
    import acai.worker.system_monitor as mod

    original = mod._observe_util
    mod._observe_util = None
    yield
    mod._observe_util = original


class TestSystemMonitor:

    @patch("acai.worker.system_monitor.time")
    def test_returns_callable(self, mock_time):
        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=4):

            mock_cpu.return_value = lambda: {"load": 80.0, "usage": 50.0}
            mock_gpu.return_value = lambda: [{"name": "GPU0", "memory": 8192}]
            mock_net.return_value = lambda: {"sent": 100, "recv": 200}
            mock_io.return_value = lambda: {"read": 1024, "write": 512}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            assert callable(observe)

    @patch("acai.worker.system_monitor.time")
    def test_observe_returns_dict_with_expected_keys(self, mock_time):
        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=4):

            mock_cpu.return_value = lambda: {"load": 80.0, "usage": 50.0}
            mock_gpu.return_value = lambda: [{"name": "GPU0", "memory": 8192}]
            mock_net.return_value = lambda: {"sent": 100, "recv": 200}
            mock_io.return_value = lambda: {"read": 1024, "write": 512}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()

            assert "cpu" in result
            assert "gpu" in result
            assert "time" in result
            assert "network" in result
            assert "disk" in result

    @patch("acai.worker.system_monitor.time")
    def test_cpu_load_normalized_by_cpu_count(self, mock_time):
        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=4):

            mock_cpu.return_value = lambda: {"load": 80.0, "usage": 50.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()

            assert result["cpu"]["load"] == 20.0  # 80 / 4

    @patch("acai.worker.system_monitor.time")
    def test_singleton_returns_same_callable(self, mock_time):
        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=8):

            mock_cpu.return_value = lambda: {"load": 16.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            first = system_monitor()
            second = system_monitor()
            assert first is second

    @patch("acai.worker.system_monitor.time")
    def test_gpu_data_passed_through(self, mock_time):
        mock_time.time.return_value = 1000.0

        gpu_data = [
            {"name": "RTX 4090", "memory_used": 4096, "memory_total": 24576},
            {"name": "RTX 4090", "memory_used": 2048, "memory_total": 24576},
        ]

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 10.0}
            mock_gpu.return_value = lambda: gpu_data
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()
            assert result["gpu"] == gpu_data

    @patch("acai.worker.system_monitor.time")
    def test_network_data_passed_through(self, mock_time):
        mock_time.time.return_value = 1000.0

        net_data = {"bytes_sent": 5000, "bytes_recv": 12000}

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 0.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: net_data
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()
            assert result["network"] == net_data

    @patch("acai.worker.system_monitor.time")
    def test_disk_io_data_passed_through(self, mock_time):
        mock_time.time.return_value = 1000.0

        io_data = {"read_bytes": 1048576, "write_bytes": 524288}

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 0.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: io_data

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()
            assert result["disk"] == io_data

    @patch("acai.worker.system_monitor.time")
    def test_time_field_uses_time_time(self, mock_time):
        mock_time.time.return_value = 9999.5

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 0.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()
            assert result["time"] == 9999.5

    def test_select_backend_called_during_init(self):
        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 0.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            system_monitor()
            mock_sel.assert_called_once()

    def test_no_gpu_returns_empty_list(self):
        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=2):

            mock_cpu.return_value = lambda: {"load": 4.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            observe = system_monitor()
            result = observe()
            assert result["gpu"] == []

    def test_gpu_monitor_raises_propagates(self):
        """If gpu_monitor() factory raises, system_monitor() propagates the error."""
        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 0.0}
            mock_gpu.side_effect = RuntimeError("nvidia-smi not found")
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            with pytest.raises(RuntimeError, match="nvidia-smi not found"):
                system_monitor()

    def test_select_backend_raises_propagates(self):
        """If select_backend() raises, system_monitor() propagates the error."""
        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = lambda: {"load": 0.0}
            mock_sel.side_effect = FileNotFoundError("No GPU backend available")
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import system_monitor

            with pytest.raises(FileNotFoundError, match="No GPU backend"):
                system_monitor()


class TestThrottledMonitor:

    @patch("acai.worker.system_monitor.time")
    def test_returns_callable(self, mock_time):
        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=4):

            mock_cpu.return_value = lambda: {"load": 40.0}
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import throttled_monitor

            throttled = throttled_monitor(interval=2)
            assert callable(throttled)

    @patch("acai.worker.system_monitor.time")
    def test_returns_cached_result_within_interval(self, mock_time):
        call_count = [0]

        def cpu_fn():
            call_count[0] += 1
            return {"load": float(call_count[0])}

        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = cpu_fn
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import throttled_monitor

            throttled = throttled_monitor(interval=5)

            # First call during init already consumed one cpu_fn call
            first_result = throttled()

            # Advance time, but still within interval
            mock_time.time.return_value = 1002.0
            second_result = throttled()

            assert first_result is second_result

    @patch("acai.worker.system_monitor.time")
    def test_refreshes_after_interval_elapsed(self, mock_time):
        call_count = [0]

        def cpu_fn():
            call_count[0] += 1
            return {"load": float(call_count[0])}

        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = cpu_fn
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import throttled_monitor

            throttled = throttled_monitor(interval=5)
            first_result = throttled()

            # Advance past interval
            mock_time.time.return_value = 1006.0
            second_result = throttled()

            assert second_result is not first_result
            assert second_result["cpu"]["load"] != first_result["cpu"]["load"]

    @patch("acai.worker.system_monitor.time")
    def test_custom_interval_respected(self, mock_time):
        call_count = [0]

        def cpu_fn():
            call_count[0] += 1
            return {"load": float(call_count[0])}

        mock_time.time.return_value = 1000.0

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = cpu_fn
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import throttled_monitor

            throttled = throttled_monitor(interval=10)
            first_result = throttled()

            # 9 seconds later — still within interval
            mock_time.time.return_value = 1009.0
            cached = throttled()
            assert cached is first_result

            # 11 seconds later — beyond interval
            mock_time.time.return_value = 1011.0
            refreshed = throttled()
            assert refreshed is not first_result

    @patch("acai.worker.system_monitor.time")
    def test_default_interval_is_one_second(self, mock_time):
        mock_time.time.return_value = 1000.0

        call_count = [0]

        def cpu_fn():
            call_count[0] += 1
            return {"load": float(call_count[0])}

        with patch("voir.instruments.cpu.cpu_monitor") as mock_cpu, \
             patch("voir.instruments.gpu.gpu_monitor") as mock_gpu, \
             patch("voir.instruments.gpu.select_backend") as mock_sel, \
             patch("voir.instruments.io.io_monitor") as mock_io, \
             patch("voir.instruments.network.network_monitor") as mock_net, \
             patch("multiprocessing.cpu_count", return_value=1):

            mock_cpu.return_value = cpu_fn
            mock_gpu.return_value = lambda: []
            mock_net.return_value = lambda: {}
            mock_io.return_value = lambda: {}

            from acai.worker.system_monitor import throttled_monitor

            throttled = throttled_monitor()  # default interval=1
            first_result = throttled()

            # 0.5s later — cached
            mock_time.time.return_value = 1000.5
            assert throttled() is first_result

            # 1.5s later — refreshed
            mock_time.time.return_value = 1001.5
            refreshed = throttled()
            assert refreshed is not first_result
