"""Tests for acai.orchestrator.input_queue."""

from __future__ import annotations

import asyncio

import pytest

from acai.orchestrator.input_queue import InputQueue, InputRequest


@pytest.fixture
def queue():
    return InputQueue()


@pytest.fixture
def sample_request():
    return InputRequest(
        conversation_id="conv-1",
        request_id="req-1",
        question="Pick a color",
        options=[{"id": "red", "label": "Red"}, {"id": "blue", "label": "Blue"}],
    )


class TestInputQueue:
    @pytest.mark.asyncio
    async def test_submit_resolves_wait(self, queue, sample_request):
        async def _submit_after_delay():
            await asyncio.sleep(0.05)
            queue.submit_input("conv-1", {"choice": "red", "text": ""})

        asyncio.create_task(_submit_after_delay())
        result = await queue.wait_for_input("conv-1", sample_request, timeout=2.0)
        assert result["choice"] == "red"

    @pytest.mark.asyncio
    async def test_timeout(self, queue, sample_request):
        with pytest.raises(TimeoutError, match="did not respond"):
            await queue.wait_for_input("conv-1", sample_request, timeout=0.05)

    @pytest.mark.asyncio
    async def test_duplicate_request_raises(self, queue, sample_request):
        async def _later():
            await asyncio.sleep(0.1)
            queue.submit_input("conv-1", {"choice": "x"})

        asyncio.create_task(_later())
        # Start first wait
        task = asyncio.create_task(
            queue.wait_for_input("conv-1", sample_request, timeout=1.0)
        )
        await asyncio.sleep(0.01)  # let it register

        req2 = InputRequest(
            conversation_id="conv-1", request_id="req-2", question="Again?",
        )
        with pytest.raises(RuntimeError, match="already has a pending"):
            await queue.wait_for_input("conv-1", req2, timeout=0.5)

        await task  # cleanup

    @pytest.mark.asyncio
    async def test_submit_no_pending_returns_false(self, queue):
        assert queue.submit_input("nonexistent", {"x": 1}) is False

    @pytest.mark.asyncio
    async def test_has_pending(self, queue, sample_request):
        assert not queue.has_pending("conv-1")

        async def _later():
            await asyncio.sleep(0.05)
            queue.submit_input("conv-1", {"choice": "blue"})

        asyncio.create_task(_later())
        task = asyncio.create_task(
            queue.wait_for_input("conv-1", sample_request, timeout=1.0)
        )
        await asyncio.sleep(0.01)
        assert queue.has_pending("conv-1")
        await task
        assert not queue.has_pending("conv-1")

    @pytest.mark.asyncio
    async def test_get_request(self, queue, sample_request):
        assert queue.get_request("conv-1") is None

        async def _later():
            await asyncio.sleep(0.05)
            queue.submit_input("conv-1", {"choice": "red"})

        asyncio.create_task(_later())
        task = asyncio.create_task(
            queue.wait_for_input("conv-1", sample_request, timeout=1.0)
        )
        await asyncio.sleep(0.01)
        req = queue.get_request("conv-1")
        assert req is not None
        assert req.question == "Pick a color"
        await task

    @pytest.mark.asyncio
    async def test_cancel(self, queue, sample_request):
        async def _cancel():
            await asyncio.sleep(0.05)
            result = queue.cancel("conv-1", reason="user navigated away")
            assert result is True

        asyncio.create_task(_cancel())
        result = await queue.wait_for_input("conv-1", sample_request, timeout=1.0)
        assert result["cancelled"] is True
        assert "navigated away" in result["reason"]

    @pytest.mark.asyncio
    async def test_cancel_no_pending(self, queue):
        assert queue.cancel("no-such") is False

    @pytest.mark.asyncio
    async def test_cancel_all(self, queue):
        req1 = InputRequest(conversation_id="c1", request_id="r1", question="Q1")
        req2 = InputRequest(conversation_id="c2", request_id="r2", question="Q2")

        task1 = asyncio.create_task(queue.wait_for_input("c1", req1, timeout=5.0))
        task2 = asyncio.create_task(queue.wait_for_input("c2", req2, timeout=5.0))
        await asyncio.sleep(0.01)

        count = queue.cancel_all()
        assert count == 2

        r1 = await task1
        r2 = await task2
        assert r1["cancelled"] is True
        assert r2["cancelled"] is True

    @pytest.mark.asyncio
    async def test_pending_count(self, queue, sample_request):
        assert queue.pending_count == 0
        async def _later():
            await asyncio.sleep(0.05)
            queue.submit_input("conv-1", {})
        asyncio.create_task(_later())
        task = asyncio.create_task(
            queue.wait_for_input("conv-1", sample_request, timeout=1.0)
        )
        await asyncio.sleep(0.01)
        assert queue.pending_count == 1
        await task
        assert queue.pending_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_after_timeout(self, queue, sample_request):
        """After timeout, the conversation is cleaned up."""
        with pytest.raises(TimeoutError):
            await queue.wait_for_input("conv-1", sample_request, timeout=0.02)
        assert not queue.has_pending("conv-1")
        assert queue.get_request("conv-1") is None
