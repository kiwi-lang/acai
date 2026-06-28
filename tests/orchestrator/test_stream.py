"""Tests for acai.orchestrator.stream — StreamTracker pub-sub hub."""

from __future__ import annotations

import queue
import threading
from unittest.mock import patch

import pytest

from acai.orchestrator.stream import StreamTracker


@pytest.fixture()
def tracker():
    return StreamTracker()


class TestRegister:
    def test_register_maps_task_to_stream(self, tracker):
        tracker.register("task-1", "stream-A")
        assert tracker.stream_for("task-1") == "stream-A"

    def test_register_multiple_tasks_same_stream(self, tracker):
        tracker.register("task-1", "stream-A")
        tracker.register("task-2", "stream-A")
        assert tracker.stream_for("task-1") == "stream-A"
        assert tracker.stream_for("task-2") == "stream-A"

    def test_register_overwrites(self, tracker):
        tracker.register("task-1", "stream-A")
        tracker.register("task-1", "stream-B")
        assert tracker.stream_for("task-1") == "stream-B"


class TestStreamFor:
    def test_returns_empty_string_for_unknown_task(self, tracker):
        assert tracker.stream_for("unknown") == ""

    def test_conv_for_is_alias(self, tracker):
        tracker.register("t1", "s1")
        assert tracker.conv_for("t1") == "s1"
        assert tracker.conv_for("missing") == ""


class TestPush:
    def test_push_token_accumulates_in_buffer(self, tracker):
        tracker.register("task-1", "stream-A")
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "hello"}})
        tracker.push("stream-A", {"event_type": "token", "data": {"token": " world"}})
        _, partial = tracker.get_partial("stream-A")
        assert partial == "hello world"

    def test_push_done_clears_buffer_and_mapping(self, tracker):
        tracker.register("task-1", "stream-A")
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "hi"}})
        tracker.push("stream-A", {"event_type": "done", "data": {"task_id": "task-1"}})
        assert tracker.stream_for("task-1") == ""
        _, partial = tracker.get_partial("stream-A")
        assert partial == ""

    def test_push_delivers_to_subscriber(self, tracker):
        q = tracker.subscribe("stream-A")
        event = {"event_type": "token", "data": {"token": "x"}}
        tracker.push("stream-A", event)
        assert q.get_nowait() == event

    def test_push_delivers_to_multiple_subscribers(self, tracker):
        q1 = tracker.subscribe("stream-A")
        q2 = tracker.subscribe("stream-A")
        event = {"event_type": "token", "data": {"token": "y"}}
        tracker.push("stream-A", event)
        assert q1.get_nowait() == event
        assert q2.get_nowait() == event

    def test_push_drops_event_when_queue_full(self, tracker):
        q = tracker.subscribe("stream-A", maxsize=1)
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "a"}})
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "b"}})
        assert q.get_nowait() == {"event_type": "token", "data": {"token": "a"}}
        assert q.empty()

    @patch("acai.orchestrator.stream.log")
    def test_push_logs_warning_on_full_queue(self, mock_log, tracker):
        tracker.subscribe("stream-A", maxsize=1)
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "a"}})
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "b"}})
        mock_log.warning.assert_called_once()
        assert "stream-A" in mock_log.warning.call_args[0][1]

    def test_push_token_with_missing_data_key(self, tracker):
        tracker.register("t1", "s1")
        tracker.push("s1", {"event_type": "token", "data": {}})
        _, partial = tracker.get_partial("s1")
        assert partial == ""

    def test_push_non_token_event_no_buffer(self, tracker):
        tracker.register("t1", "s1")
        tracker.push("s1", {"event_type": "status", "data": {"status": "running"}})
        _, partial = tracker.get_partial("s1")
        assert partial == ""


class TestSubscribe:
    def test_subscribe_returns_queue(self, tracker):
        q = tracker.subscribe("stream-X")
        assert isinstance(q, queue.Queue)

    def test_subscribe_custom_maxsize(self, tracker):
        q = tracker.subscribe("stream-X", maxsize=10)
        assert q.maxsize == 10


class TestUnsubscribe:
    def test_unsubscribe_removes_queue(self, tracker):
        q = tracker.subscribe("stream-A")
        tracker.unsubscribe("stream-A", q)
        event = {"event_type": "token", "data": {"token": "z"}}
        tracker.push("stream-A", event)
        assert q.empty()

    def test_unsubscribe_nonexistent_queue_no_error(self, tracker):
        fake_q = queue.Queue()
        tracker.unsubscribe("stream-A", fake_q)

    def test_unsubscribe_cleans_up_empty_subscriber_list(self, tracker):
        q = tracker.subscribe("stream-A")
        tracker.unsubscribe("stream-A", q)
        with tracker._lock:
            assert "stream-A" not in tracker._subscribers

    def test_unsubscribe_keeps_other_subscribers(self, tracker):
        q1 = tracker.subscribe("stream-A")
        q2 = tracker.subscribe("stream-A")
        tracker.unsubscribe("stream-A", q1)
        event = {"event_type": "token", "data": {"token": "m"}}
        tracker.push("stream-A", event)
        assert q1.empty()
        assert q2.get_nowait() == event


class TestGetPartial:
    def test_returns_none_empty_when_no_stream(self, tracker):
        task_id, text = tracker.get_partial("nonexistent")
        assert task_id is None
        assert text == ""

    def test_returns_task_id_and_empty_before_tokens(self, tracker):
        tracker.register("task-1", "stream-A")
        task_id, text = tracker.get_partial("stream-A")
        assert task_id == "task-1"
        assert text == ""

    def test_returns_accumulated_text(self, tracker):
        tracker.register("task-1", "stream-A")
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "foo"}})
        tracker.push("stream-A", {"event_type": "token", "data": {"token": "bar"}})
        task_id, text = tracker.get_partial("stream-A")
        assert task_id == "task-1"
        assert text == "foobar"


class TestThreadSafety:
    def test_concurrent_push_and_subscribe(self, tracker):
        tracker.register("t1", "s1")
        q = tracker.subscribe("s1")
        num_events = 100
        errors = []

        def pusher():
            try:
                for i in range(num_events):
                    tracker.push("s1", {"event_type": "token", "data": {"token": str(i)}})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=pusher) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        count = 0
        while not q.empty():
            q.get_nowait()
            count += 1
        assert count == num_events * 4
