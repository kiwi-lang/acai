"""Tests for acai.orchestrator.task_runner."""

from __future__ import annotations

import asyncio

import pytest

from acai.orchestrator.task_runner import TaskRunner, TaskStatus, RunningTask


@pytest.fixture
def runner():
    return TaskRunner()


class TestRegistration:
    def test_register_and_list(self, runner):
        async def my_task(**kwargs):
            return "done"

        runner.register_task("my_task", my_task)
        assert "my_task" in runner.list_registered()

    def test_list_empty(self, runner):
        assert runner.list_registered() == []


class TestBackgroundTasks:
    @pytest.mark.asyncio
    async def test_run_success(self, runner):
        async def compute(**kwargs):
            await asyncio.sleep(0.01)
            return f"result: {kwargs.get('x', 0)}"

        runner.register_task("compute", compute)
        task_id = await runner.run_background("compute", x=42)
        assert task_id

        task = await runner.wait_for_task(task_id, timeout=2.0)
        assert task.status == TaskStatus.COMPLETED
        assert "42" in task.result

    @pytest.mark.asyncio
    async def test_run_failure(self, runner):
        async def failing(**kwargs):
            raise ValueError("oops")

        runner.register_task("failing", failing)
        task_id = await runner.run_background("failing")
        await asyncio.sleep(0.1)

        task = runner.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "oops" in task.error

    @pytest.mark.asyncio
    async def test_run_unregistered(self, runner):
        with pytest.raises(KeyError, match="not registered"):
            await runner.run_background("ghost")

    @pytest.mark.asyncio
    async def test_check_task(self, runner):
        async def slow(**kwargs):
            await asyncio.sleep(0.5)
            return "done"

        runner.register_task("slow", slow)
        task_id = await runner.run_background("slow")
        await asyncio.sleep(0.01)

        task = runner.get_task(task_id)
        assert task.status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_cancel_task(self, runner):
        async def forever(**kwargs):
            await asyncio.sleep(100)

        runner.register_task("forever", forever)
        task_id = await runner.run_background("forever")
        await asyncio.sleep(0.01)

        success = runner.cancel_task(task_id)
        assert success
        task = runner.get_task(task_id)
        assert task.status == TaskStatus.CANCELLED


class TestSubagent:
    @pytest.mark.asyncio
    async def test_run_blocking_success(self, runner):
        async def factory(*, agent_name, message, conversation, **kw):
            return f"Hello from {agent_name}: {message}"

        task = await runner.run_subagent(
            agent_name="helper",
            message="do something",
            graph_factory=factory,
        )
        assert task.status == TaskStatus.COMPLETED
        assert "Hello from helper" in task.result

    @pytest.mark.asyncio
    async def test_run_blocking_no_factory(self, runner):
        task = await runner.run_subagent(
            agent_name="helper",
            message="do something",
            graph_factory=None,
        )
        assert task.status == TaskStatus.FAILED
        assert "No graph factory" in task.error

    @pytest.mark.asyncio
    async def test_run_blocking_factory_error(self, runner):
        async def factory(*, agent_name, message, conversation, **kw):
            raise RuntimeError("boom")

        task = await runner.run_subagent(
            agent_name="helper",
            message="do something",
            graph_factory=factory,
        )
        assert task.status == TaskStatus.FAILED
        assert "boom" in task.error

    @pytest.mark.asyncio
    async def test_run_async(self, runner):
        async def factory(*, agent_name, message, conversation, **kw):
            await asyncio.sleep(0.02)
            return "async result"

        task_id = await runner.run_subagent_async(
            agent_name="bg_agent",
            message="background work",
            graph_factory=factory,
        )
        assert task_id

        task = await runner.wait_for_task(task_id, timeout=2.0)
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "async result"

    @pytest.mark.asyncio
    async def test_run_async_failure(self, runner):
        async def factory(*, agent_name, message, conversation, **kw):
            raise ValueError("async fail")

        task_id = await runner.run_subagent_async(
            agent_name="bg_agent",
            message="will fail",
            graph_factory=factory,
        )
        await asyncio.sleep(0.1)
        task = runner.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert "async fail" in task.error


class TestWaitForTask:
    @pytest.mark.asyncio
    async def test_wait_not_found(self, runner):
        with pytest.raises(KeyError, match="not found"):
            await runner.wait_for_task("fake-id")

    @pytest.mark.asyncio
    async def test_wait_already_complete(self, runner):
        async def factory(*, agent_name, message, conversation, **kw):
            return "instant"

        task = await runner.run_subagent(
            agent_name="a", message="m", graph_factory=factory,
        )
        # Task is already done; wait should return immediately
        result = await runner.wait_for_task(task.task_id, timeout=0.1)
        assert result.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_wait_timeout(self, runner):
        async def slow(**kwargs):
            await asyncio.sleep(10)

        runner.register_task("slow", slow)
        task_id = await runner.run_background("slow")

        with pytest.raises(TimeoutError):
            await runner.wait_for_task(task_id, timeout=0.05)


class TestListTasks:
    @pytest.mark.asyncio
    async def test_list_all(self, runner):
        async def quick(**kwargs):
            return "ok"

        runner.register_task("q", quick)
        await runner.run_background("q")
        await asyncio.sleep(0.05)

        tasks = runner.list_tasks()
        assert len(tasks) >= 1

    @pytest.mark.asyncio
    async def test_list_by_status(self, runner):
        async def quick(**kwargs):
            return "ok"

        runner.register_task("q", quick)
        await runner.run_background("q")
        await asyncio.sleep(0.05)

        completed = runner.list_tasks(status=TaskStatus.COMPLETED)
        assert all(t.status == TaskStatus.COMPLETED for t in completed)

        running = runner.list_tasks(status=TaskStatus.RUNNING)
        assert all(t.status == TaskStatus.RUNNING for t in running)


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_oldest(self, runner):
        runner._max_completed = 3

        async def quick(**kwargs):
            return "ok"

        runner.register_task("q", quick)
        for _ in range(5):
            await runner.run_background("q")

        await asyncio.sleep(0.1)
        # Should have cleaned up to max_completed
        completed = [
            t for t in runner._tasks.values()
            if t.status == TaskStatus.COMPLETED
        ]
        assert len(completed) <= 3
