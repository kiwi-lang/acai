"""Tests for acai.orchestrator.load_balancer."""

from __future__ import annotations

import asyncio
import time

import pytest

from acai.orchestrator.load_balancer import LoadBalancer, WorkerInfo, WorkerStatus


class TestLoadBalancerRegistration:
    def test_register_returns_id(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")
        assert wid
        assert len(wid) == 12

    def test_register_with_capabilities(self):
        lb = LoadBalancer()
        caps = {"model": "qwen3", "tools": ["shell.run"]}
        wid = lb.register("http://w1:8000/worker", caps)
        w = lb.get(wid)
        assert w is not None
        assert w.capabilities == caps

    def test_list_workers(self):
        lb = LoadBalancer()
        lb.register("http://w1:8000/worker")
        lb.register("http://w2:8000/worker")
        assert len(lb.list_workers()) == 2

    def test_unregister(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")
        assert lb.unregister(wid) is True
        assert lb.get(wid) is None
        assert lb.unregister(wid) is False

    def test_unregister_unknown(self):
        lb = LoadBalancer()
        assert lb.unregister("nonexistent") is False


class TestLoadBalancerSelection:
    def test_select_returns_idle_worker(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")
        selected = lb.select()
        assert selected is not None
        assert selected.worker_id == wid

    def test_select_returns_none_when_empty(self):
        lb = LoadBalancer()
        assert lb.select() is None

    def test_select_skips_busy_workers(self):
        lb = LoadBalancer()
        w1 = lb.register("http://w1:8000/worker")
        w2 = lb.register("http://w2:8000/worker")
        lb.mark_busy(w1, "task-1")
        selected = lb.select()
        assert selected is not None
        assert selected.worker_id == w2

    def test_select_returns_none_when_all_busy(self):
        lb = LoadBalancer()
        w1 = lb.register("http://w1:8000/worker")
        lb.mark_busy(w1, "task-1")
        assert lb.select() is None

    def test_mark_idle_makes_worker_available(self):
        lb = LoadBalancer()
        w1 = lb.register("http://w1:8000/worker")
        lb.mark_busy(w1, "task-1")
        assert lb.select() is None
        lb.mark_idle(w1)
        selected = lb.select()
        assert selected is not None
        assert selected.worker_id == w1

    def test_round_robin_distribution(self):
        lb = LoadBalancer()
        w1 = lb.register("http://w1:8000/worker")
        w2 = lb.register("http://w2:8000/worker")

        seen = set()
        for _ in range(4):
            w = lb.select()
            assert w is not None
            seen.add(w.worker_id)

        assert seen == {w1, w2}


class TestLoadBalancerHealth:
    def test_heartbeat_updates_timestamp(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")
        w = lb.get(wid)
        old_hb = w.last_heartbeat

        time.sleep(0.01)
        lb.heartbeat(wid, {"cpu": 50})
        w = lb.get(wid)
        assert w.last_heartbeat > old_hb
        assert w.telemetry == {"cpu": 50}

    def test_heartbeat_unknown_worker(self):
        lb = LoadBalancer()
        assert lb.heartbeat("nonexistent") is False

    def test_heartbeat_revives_offline_worker(self):
        lb = LoadBalancer(heartbeat_timeout=0.01)
        wid = lb.register("http://w1:8000/worker")
        time.sleep(0.02)
        lb._reap()
        w = lb.get(wid)
        assert w.status == WorkerStatus.OFFLINE

        lb.heartbeat(wid)
        w = lb.get(wid)
        assert w.status == WorkerStatus.IDLE

    def test_reaper_marks_stale_workers_offline(self):
        lb = LoadBalancer(heartbeat_timeout=0.01)
        wid = lb.register("http://w1:8000/worker")
        time.sleep(0.02)
        lb._reap()
        w = lb.get(wid)
        assert w.status == WorkerStatus.OFFLINE

    def test_reaper_does_not_touch_fresh_workers(self):
        lb = LoadBalancer(heartbeat_timeout=60)
        wid = lb.register("http://w1:8000/worker")
        lb._reap()
        w = lb.get(wid)
        assert w.status == WorkerStatus.IDLE


class TestLoadBalancerAcquire:
    @pytest.mark.asyncio
    async def test_acquire_returns_worker_and_auto_releases(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")

        async with lb.acquire(task_id="t1") as worker:
            assert worker.worker_id == wid
            assert worker.status == WorkerStatus.BUSY
            assert worker.current_task == "t1"

        w = lb.get(wid)
        assert w.status == WorkerStatus.IDLE
        assert w.current_task == ""

    @pytest.mark.asyncio
    async def test_acquire_releases_on_exception(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")

        with pytest.raises(ValueError):
            async with lb.acquire() as worker:
                assert worker.status == WorkerStatus.BUSY
                raise ValueError("boom")

        w = lb.get(wid)
        assert w.status == WorkerStatus.IDLE

    @pytest.mark.asyncio
    async def test_acquire_waits_for_worker(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")
        lb.mark_busy(wid, "other")

        async def free_later():
            await asyncio.sleep(0.1)
            lb.release(wid)

        asyncio.create_task(free_later())

        async with lb.acquire(task_id="t2", timeout=5) as worker:
            assert worker.worker_id == wid

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        lb = LoadBalancer()
        wid = lb.register("http://w1:8000/worker")
        lb.mark_busy(wid, "stuck")

        with pytest.raises(TimeoutError):
            async with lb.acquire(timeout=0.2):
                pass

    @pytest.mark.asyncio
    async def test_acquire_no_workers_registered(self):
        lb = LoadBalancer()

        with pytest.raises(TimeoutError):
            async with lb.acquire(timeout=0.2):
                pass

    @pytest.mark.asyncio
    async def test_acquire_picks_up_newly_registered_worker(self):
        lb = LoadBalancer()

        async def register_later():
            await asyncio.sleep(0.1)
            lb.register("http://w1:8000/worker")

        asyncio.create_task(register_later())

        async with lb.acquire(timeout=5) as worker:
            assert worker.url == "http://w1:8000/worker"


class TestWorkerInfo:
    def test_to_dict(self):
        w = WorkerInfo(
            worker_id="abc123",
            url="http://w1:8000/worker",
            capabilities={"model": "qwen3"},
        )
        d = w.to_dict()
        assert d["worker_id"] == "abc123"
        assert d["url"] == "http://w1:8000/worker"
        assert d["capabilities"] == {"model": "qwen3"}
        assert d["status"] == "idle"
        assert d["current_task"] == ""
