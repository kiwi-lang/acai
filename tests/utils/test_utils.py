"""Tests for acai.utils.utils — utility classes and functions."""

from __future__ import annotations

import sys
import threading
import time
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from acai.utils.utils import (
    ModelModule,
    ModelModules,
    load_model_override,
    namespaced_route,
    SocketIOBuffer,
    StreamRouter,
    SystemMetric,
    ModelCacheEntry,
    ThreadSafeModel,
    capture_progress,
    capture_progress_thread,
    websocket_pusher,
    stdout_pusher,
    _make_stat,
    socket_io_lock,
)


# ---------------------------------------------------------------------------
# ModelModule
# ---------------------------------------------------------------------------
class TestModelModule:

    def test_getattr_delegates_to_model_module(self):
        mod = SimpleNamespace(foo="from_mod")
        default = SimpleNamespace(foo="from_default", bar="default_bar")
        mm = ModelModule(mod, default)
        assert mm.foo == "from_mod"

    def test_getattr_falls_back_to_default(self):
        mod = SimpleNamespace()
        default = SimpleNamespace(bar="default_bar")
        mm = ModelModule(mod, default)
        assert mm.bar == "default_bar"

    def test_getattr_raises_when_both_miss(self):
        mod = SimpleNamespace()
        default = SimpleNamespace()
        mm = ModelModule(mod, default)
        with pytest.raises(AttributeError):
            _ = mm.nonexistent

    def test_model_module_prefers_model_over_default(self):
        mod = SimpleNamespace(x=1)
        default = SimpleNamespace(x=2)
        mm = ModelModule(mod, default)
        assert mm.x == 1


# ---------------------------------------------------------------------------
# ModelModules
# ---------------------------------------------------------------------------
class TestModelModules:

    def _make(self):
        mod_a = SimpleNamespace(name="a")
        mod_b = SimpleNamespace(name="b")
        default = SimpleNamespace(name="default", fallback=True)
        plugins = {"model_a": mod_a, "model_b": mod_b}
        return ModelModules(plugins, default), default

    def test_getitem_known_key(self):
        mm, _ = self._make()
        entry = mm["model_a"]
        assert isinstance(entry, ModelModule)
        assert entry.name == "a"

    def test_getitem_unknown_key_returns_default(self):
        mm, default = self._make()
        entry = mm["unknown"]
        assert entry.name == "default"

    def test_call_known_key(self):
        mm, _ = self._make()
        entry = mm("model_b")
        assert entry.name == "b"

    def test_call_unknown_key_returns_default(self):
        mm, _ = self._make()
        entry = mm("missing")
        assert entry.name == "default"

    def test_keys(self):
        mm, _ = self._make()
        assert set(mm.keys()) == {"model_a", "model_b"}

    def test_empty_plugins(self):
        default = SimpleNamespace(val=99)
        mm = ModelModules({}, default)
        assert list(mm.keys()) == []
        assert mm["anything"].val == 99


# ---------------------------------------------------------------------------
# load_model_override
# ---------------------------------------------------------------------------
class TestLoadModelOverride:

    def test_loads_modules_with_model_id(self):
        parent = SimpleNamespace(__path__=["/fake"], __name__="pkg")
        default = SimpleNamespace()
        mod_with_ids = SimpleNamespace(model_id=["llama", "gpt"])
        mod_no_ids = SimpleNamespace()

        with patch("acai.utils.utils.pkgutil.iter_modules") as mock_iter, \
             patch("acai.utils.utils.importlib.import_module") as mock_import:
            mock_iter.return_value = [
                (None, "pkg.mod_a", False),
                (None, "pkg.mod_b", False),
            ]
            mock_import.side_effect = lambda name: {
                "pkg.mod_a": mod_with_ids,
                "pkg.mod_b": mod_no_ids,
            }[name]

            result = load_model_override(parent, default)

        assert isinstance(result, ModelModules)
        assert "llama" in result.keys()
        assert "gpt" in result.keys()
        assert result["llama"].model_id == ["llama", "gpt"]

    def test_empty_path_yields_no_plugins(self):
        parent = SimpleNamespace(__path__=[], __name__="empty")
        default = SimpleNamespace()

        with patch("acai.utils.utils.pkgutil.iter_modules", return_value=[]):
            result = load_model_override(parent, default)

        assert list(result.keys()) == []


# ---------------------------------------------------------------------------
# namespaced_route
# ---------------------------------------------------------------------------
class TestNamespacedRoute:

    def _flask_app(self):
        """Return a plain object with a mock route — no .app attribute."""
        app = SimpleNamespace(route=MagicMock())
        return app

    def test_prepends_namespace(self):
        app = self._flask_app()
        route = namespaced_route(app, "/api")
        route("/users")
        app.route.assert_called_once_with("/api/users")

    def test_auto_adds_leading_slash_to_namespace(self):
        app = self._flask_app()
        route = namespaced_route(app, "api")
        route("/items")
        app.route.assert_called_once_with("/api/items")

    def test_auto_adds_leading_slash_to_url(self):
        app = self._flask_app()
        route = namespaced_route(app, "/v1")
        route("health")
        app.route.assert_called_once_with("/v1/health")

    def test_passes_extra_args_and_kwargs(self):
        app = self._flask_app()
        route = namespaced_route(app, "/ns")
        route("/ep", "extra", methods=["POST"])
        app.route.assert_called_once_with("/ns/ep", "extra", methods=["POST"])

    def test_unwraps_acai_instance(self):
        """When app has an .app attribute, use that as the flask app."""
        inner_app = MagicMock()
        wrapper = SimpleNamespace(app=inner_app)
        route = namespaced_route(wrapper, "/ns")
        route("/test")
        inner_app.route.assert_called_once_with("/ns/test")


# ---------------------------------------------------------------------------
# SystemMetric (dataclass structure)
# ---------------------------------------------------------------------------
class TestSystemMetric:

    def test_can_instantiate_cpu_metric(self):
        cpu = SystemMetric.CPUMetric(load=0.5, memory=[1024, 2048])
        assert cpu.load == 0.5
        assert cpu.memory == [1024, 2048]

    def test_can_instantiate_gpu_item(self):
        item = SystemMetric.GPUMetric.GPUItem(
            load=0.8, memory=[4000, 8000], power=250.0, temperature=72.0
        )
        assert item.load == 0.8
        assert item.power == 250.0

    def test_can_instantiate_gpu_metric(self):
        gpu = SystemMetric.GPUMetric(gpus={"0": "item"})
        assert "0" in gpu.gpus

    def test_can_instantiate_network_metric(self):
        net = SystemMetric.NetworkMetric(
            bytes_sent=100, bytes_recv=200,
            packets_sent=10, packets_recv=20,
            errin=0, errout=0, dropin=0, dropout=0,
        )
        assert net.bytes_sent == 100

    def test_full_system_metric(self):
        cpu = SystemMetric.CPUMetric(load=0.3, memory=[512, 1024])
        gpu = SystemMetric.GPUMetric(gpus={})
        net = SystemMetric.NetworkMetric(0, 0, 0, 0, 0, 0, 0, 0)
        sm = SystemMetric(time=1.0, cpu=cpu, gpu=gpu, netowrk=net)
        assert sm.time == 1.0


# ---------------------------------------------------------------------------
# system_monitor — error path when heavy deps missing
# ---------------------------------------------------------------------------
class TestSystemMonitor:

    def test_raises_without_heavy_deps(self):
        import acai.utils.utils as mod
        old = mod._observe_util
        mod._observe_util = None
        old_flag = mod._HAS_HEAVY_DEPS
        mod._HAS_HEAVY_DEPS = False
        try:
            with pytest.raises(
                RuntimeError,
                match="system_monitor requires torch/voir",
            ):
                mod.system_monitor()
        finally:
            mod._HAS_HEAVY_DEPS = old_flag
            mod._observe_util = old

    def test_returns_cached_observer(self):
        import acai.utils.utils as mod
        sentinel = lambda: "metrics"
        old = mod._observe_util
        mod._observe_util = sentinel
        try:
            assert mod.system_monitor() is sentinel
        finally:
            mod._observe_util = old


# ---------------------------------------------------------------------------
# _make_stat
# ---------------------------------------------------------------------------
class TestMakeStat:

    def test_returns_none_without_heavy_deps(self):
        import acai.utils.utils as mod
        old = mod._HAS_HEAVY_DEPS
        mod._HAS_HEAVY_DEPS = False
        try:
            assert mod._make_stat() is None
        finally:
            mod._HAS_HEAVY_DEPS = old


# ---------------------------------------------------------------------------
# ModelCacheEntry
# ---------------------------------------------------------------------------
class TestModelCacheEntry:

    def _entry(self, **kw):
        defaults = dict(
            model=MagicMock(),
            lock=threading.Lock(),
            load_time_stat=None,
            mem_stat=None,
            inference_stat=None,
        )
        defaults.update(kw)
        return ModelCacheEntry(**defaults)

    def test_memory_returns_neg1_without_gpu_data(self):
        e = self._entry()
        assert e.memory() == -1

    def test_memory_returns_neg1_when_before_is_none(self):
        e = self._entry(after={"gpu": {}})
        assert e.memory() == -1

    def test_memory_returns_neg1_when_after_is_none(self):
        e = self._entry(before={"gpu": {}})
        assert e.memory() == -1

    def test_memory_computes_diff(self):
        before = {"gpu": {"0": {"memory": [1000, 8000]}}}
        after = {"gpu": {"0": {"memory": [3000, 8000]}}}
        e = self._entry(before=before, after=after)
        assert e.memory() == 2000

    def test_memory_multi_gpu(self):
        before = {"gpu": {"0": {"memory": [1000, 8000]}, "1": {"memory": [500, 8000]}}}
        after = {"gpu": {"0": {"memory": [2000, 8000]}, "1": {"memory": [1500, 8000]}}}
        e = self._entry(before=before, after=after)
        assert e.memory() == 2000  # (2000-1000) + (1500-500)

    def test_load_time_returns_neg1_without_data(self):
        e = self._entry()
        assert e.load_time() == -1

    def test_load_time_returns_neg1_when_before_only(self):
        e = self._entry(before={"time": 1.0})
        assert e.load_time() == -1

    def test_load_time_computes_diff(self):
        e = self._entry(before={"time": 10.0}, after={"time": 12.5})
        assert e.load_time() == pytest.approx(2.5)

    def test_inference_context_manager_tracks_time(self):
        mock_stat = MagicMock()
        e = self._entry(inference_stat=mock_stat)
        with e.inference():
            time.sleep(0.01)
        assert e.last_inference_time > 0
        mock_stat.update.assert_called_once()

    def test_json_output(self):
        before = {"gpu": {"0": {"memory": [1000, 8000]}}, "time": 1.0}
        after = {"gpu": {"0": {"memory": [2000, 8000]}}, "time": 3.0}
        e = self._entry(before=before, after=after, last_used=100.0)
        e.last_inference_time = 0.5
        j = e.__json__()
        assert j["memory_usage"] == 1000
        assert j["load_time"] == pytest.approx(2.0)
        assert j["last_used"] == 100.0
        assert j["last_inference_time"] == 0.5

    def test_state_dict(self):
        mock_stat = MagicMock()
        mock_stat.state_dict.return_value = {"n": 1}
        e = self._entry(
            load_time_stat=mock_stat,
            mem_stat=mock_stat,
            inference_stat=mock_stat,
            model_info={"name": "test"},
        )
        sd = e.state_dict()
        assert sd["model_info"] == {"name": "test"}
        assert sd["load_time_stat"] == {"n": 1}

    def test_load_state_dict(self):
        mock_cls = MagicMock()
        mock_cls.from_dict.return_value = "restored"
        e = self._entry()
        state = {
            "model_info": {"x": 1},
            "load_time_stat": {"a": 1},
            "mem_stat": {"b": 2},
            "inference_stat": {"c": 3},
        }
        with patch("acai.utils.utils.StatStream", mock_cls):
            e.load_state_dict(state)
        assert e.model_info == {"x": 1}


# ---------------------------------------------------------------------------
# ThreadSafeModel
# ---------------------------------------------------------------------------
class TestThreadSafeModel:

    def test_calls_model_under_lock(self):
        model_fn = MagicMock(return_value="result")
        entry = ModelCacheEntry(
            model=model_fn,
            lock=threading.Lock(),
            load_time_stat=None,
            mem_stat=None,
            inference_stat=MagicMock(),
        )
        cb = MagicMock()
        tsm = ThreadSafeModel(entry, cb)

        result = tsm("arg1", key="val")
        assert result == "result"
        model_fn.assert_called_once_with("arg1", key="val")
        cb.assert_called_once()

    def test_updates_last_used(self):
        model_fn = MagicMock(return_value=None)
        entry = ModelCacheEntry(
            model=model_fn,
            lock=threading.Lock(),
            load_time_stat=None,
            mem_stat=None,
            inference_stat=MagicMock(),
        )
        tsm = ThreadSafeModel(entry, lambda: None)
        tsm()
        assert entry.last_used is not None
        assert entry.last_used > 0

    def test_thread_safety(self):
        """Multiple threads calling the model don't corrupt state."""
        call_count = 0
        call_lock = threading.Lock()

        def model_fn():
            nonlocal call_count
            with call_lock:
                call_count += 1
            time.sleep(0.001)

        entry = ModelCacheEntry(
            model=model_fn,
            lock=threading.Lock(),
            load_time_stat=None,
            mem_stat=None,
            inference_stat=MagicMock(),
        )
        tsm = ThreadSafeModel(entry, lambda: None)

        threads = [threading.Thread(target=tsm) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert call_count == 5


# ---------------------------------------------------------------------------
# SocketIOBuffer
# ---------------------------------------------------------------------------
class TestSocketIOBuffer:

    def test_simple_line(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("hello\n")
        assert lines == ["hello"]

    def test_partial_writes(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("hel")
        buf.write("lo\n")
        assert lines == ["hello"]

    def test_multiple_lines(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("a\nb\nc\n")
        assert lines == ["a", "b", "c"]

    def test_empty_lines_not_pushed(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("\n")
        assert lines == []

    def test_carriage_return_replaced(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("progress\r100%\n")
        assert "progress" in lines
        assert "100%" in lines

    def test_ansi_escape_replaced(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("line1\x1b[Aline2\n")
        assert "line1" in lines
        assert "line2" in lines

    def test_flush_is_noop(self):
        buf = SocketIOBuffer(push=lambda x: None)
        buf.flush()

    def test_no_newline_buffers(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("partial")
        assert lines == []

    def test_mixed_empty_and_content(self):
        lines = []
        buf = SocketIOBuffer(push=lines.append)
        buf.write("x\n\ny\n")
        assert "x" in lines
        assert "y" in lines


# ---------------------------------------------------------------------------
# StreamRouter
# ---------------------------------------------------------------------------
class TestStreamRouter:

    def test_simple_line(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))
        router.write("hello\n")
        assert len(messages) == 1
        assert messages[0][1] == "hello"

    def test_thread_id_is_captured(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))
        router.write("test\n")
        assert messages[0][0] == threading.get_ident()

    def test_partial_writes(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))
        router.write("hel")
        router.write("lo\n")
        assert messages[-1][1] == "hello"

    def test_multi_line(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))
        router.write("a\nb\nc\n")
        lines = [m[1] for m in messages]
        assert lines == ["a", "b", "c"]

    def test_empty_lines_not_pushed(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))
        router.write("\n")
        assert messages == []

    def test_carriage_return_replaced(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))
        router.write("a\rb\n")
        lines = [m[1] for m in messages]
        assert "a" in lines
        assert "b" in lines

    def test_thread_local_isolation(self):
        messages = []
        router = StreamRouter(push=lambda tid, line: messages.append((tid, line)))

        def worker(val):
            router.write(f"w{val}")
            router.write(f"_{val}\n")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        lines = [m[1] for m in messages]
        assert len(lines) == 4
        for i in range(4):
            assert f"w{i}_{i}" in lines

    def test_flush_is_noop(self):
        router = StreamRouter(push=lambda tid, line: None)
        router.flush()

    def test_prev_is_thread_local(self):
        router = StreamRouter(push=lambda tid, line: None)
        assert isinstance(router.prev, list)
        router.prev.append("test")
        assert router.prev == ["test"]


# ---------------------------------------------------------------------------
# capture_progress
# ---------------------------------------------------------------------------
class TestCaptureProgress:

    def test_replaces_and_restores_stdout_stderr(self):
        original_out = sys.stdout
        original_err = sys.stderr
        app = MagicMock()

        with capture_progress(app, action_id=42):
            assert isinstance(sys.stdout, SocketIOBuffer)
            assert isinstance(sys.stderr, SocketIOBuffer)

        assert sys.stdout is original_out
        assert sys.stderr is original_err

    def test_push_sends_to_app_message(self):
        app = MagicMock()
        original_out = sys.stdout
        original_err = sys.stderr

        try:
            with capture_progress(app, action_id=7):
                sys.stdout.write("hello\n")

            app.message.assert_any_call("stdout", {"id": 7, "line": "hello"})
        finally:
            sys.stdout = original_out
            sys.stderr = original_err

    def test_does_not_double_replace(self):
        app = MagicMock()
        original_out = sys.stdout
        original_err = sys.stderr

        try:
            with capture_progress(app, action_id=1):
                first_stdout = sys.stdout
                with capture_progress(app, action_id=2):
                    assert sys.stdout is first_stdout
        finally:
            sys.stdout = original_out
            sys.stderr = original_err


# ---------------------------------------------------------------------------
# capture_progress_thread
# ---------------------------------------------------------------------------
class TestCaptureProgressThread:

    def test_replaces_and_restores(self):
        original_out = sys.stdout
        original_err = sys.stderr
        messages = []

        def factory(channel):
            def push(tid, line):
                messages.append((channel, tid, line))
            return push

        with capture_progress_thread(factory, action_id=1):
            assert isinstance(sys.stdout, StreamRouter)
            assert isinstance(sys.stderr, StreamRouter)

        assert sys.stdout is original_out
        assert sys.stderr is original_err

    def test_restores_on_exception(self):
        original_out = sys.stdout
        original_err = sys.stderr

        def factory(channel):
            return lambda tid, line: None

        with pytest.raises(ValueError):
            with capture_progress_thread(factory):
                raise ValueError("boom")

        assert sys.stdout is original_out
        assert sys.stderr is original_err

    def test_routes_messages(self):
        original_out = sys.stdout
        original_err = sys.stderr
        messages = []

        def factory(channel):
            def push(tid, line):
                messages.append((channel, line))
            return push

        try:
            with capture_progress_thread(factory):
                sys.stdout.write("out_msg\n")
                sys.stderr.write("err_msg\n")

            stdout_msgs = [m for m in messages if m[0] == "stdout"]
            stderr_msgs = [m for m in messages if m[0] == "stderr"]
            assert any("out_msg" in m[1] for m in stdout_msgs)
            assert any("err_msg" in m[1] for m in stderr_msgs)
        finally:
            sys.stdout = original_out
            sys.stderr = original_err


# ---------------------------------------------------------------------------
# websocket_pusher
# ---------------------------------------------------------------------------
class TestWebsocketPusher:

    def test_creates_channel_push(self):
        app = MagicMock()
        factory = websocket_pusher(app, action_id=10)
        push = factory("stdout")
        push(12345, "hello")
        app.message.assert_called_once_with(
            "stdout", {"id": 10, "thread_id": 12345, "line": "hello"}
        )

    def test_different_channels(self):
        app = MagicMock()
        factory = websocket_pusher(app, action_id=1)
        factory("stdout")(1, "out")
        factory("stderr")(2, "err")
        assert app.message.call_count == 2


# ---------------------------------------------------------------------------
# stdout_pusher
# ---------------------------------------------------------------------------
class TestStdoutPusher:

    def test_prints_to_file(self):
        output = StringIO()
        factory = stdout_pusher(output, action_id=5)
        push = factory("stdout")
        push(999, "test_line")
        printed = output.getvalue()
        assert "stdout" in printed
        assert "test_line" in printed
        assert "999" in printed

    def test_stderr_channel(self):
        output = StringIO()
        factory = stdout_pusher(output, action_id=3)
        push = factory("stderr")
        push(111, "error")
        printed = output.getvalue()
        assert "stderr" in printed
        assert "error" in printed


# ---------------------------------------------------------------------------
# pil_to_base64_png (mocked PIL)
# ---------------------------------------------------------------------------
class TestPilToBase64Png:

    def test_converts_image(self):
        from acai.utils.utils import pil_to_base64_png
        import base64

        mock_img = MagicMock()
        def save_side_effect(buf, format=None):
            buf.write(b"fakepngdata")
        mock_img.save.side_effect = save_side_effect

        result = pil_to_base64_png(mock_img)
        assert isinstance(result, str)
        decoded = base64.b64decode(result)
        assert decoded == b"fakepngdata"

    def test_empty_image(self):
        from acai.utils.utils import pil_to_base64_png
        import base64

        mock_img = MagicMock()
        mock_img.save.side_effect = lambda buf, format=None: buf.write(b"")

        result = pil_to_base64_png(mock_img)
        assert result == base64.b64encode(b"").decode("ascii")
