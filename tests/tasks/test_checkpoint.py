"""Tests for tool-loop checkpoint and resume."""

from __future__ import annotations

import json

import pytest

from acai.tasks.converse import ConverseGraph


@pytest.mark.asyncio
class TestCheckpointSave:
    """Verify that checkpoints are written during the tool loop."""

    async def test_checkpoint_saved_after_tool_round(
        self, load_balancer, chat_store, graph_deps,
    ):
        """A checkpoint.json is written after each tool round and cleared on completion."""
        conv = chat_store.create(title="checkpoint save")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        checkpoints_seen = []

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                # Peek at checkpoint during execution
                cp = chat_store.load_checkpoint(conv.id)
                if cp and cp not in checkpoints_seen:
                    checkpoints_seen.append(cp)

        # Checkpoint should have been written during the tool loop
        assert len(checkpoints_seen) >= 1, "No checkpoint was saved during tool loop"
        cp = checkpoints_seen[0]
        assert "payload" in cp
        assert "tool_round" in cp

        # After completion, checkpoint should be cleared
        assert chat_store.load_checkpoint(conv.id) is None


@pytest.mark.asyncio
class TestCheckpointResume:
    """Verify that a graph resumes from a checkpoint."""

    async def test_checkpoint_saved_before_first_tool_executes(
        self, load_balancer, chat_store, graph_deps,
    ):
        """A checkpoint is written at the START of a tool round (before tool execution).
        This means even if a tool crashes in round 0, we can resume."""
        conv = chat_store.create(title="early checkpoint")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        first_checkpoint = None

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                if event.get("event_type") == "tool_start" and first_checkpoint is None:
                    # Right after tool_start, a checkpoint should already exist
                    first_checkpoint = chat_store.load_checkpoint(conv.id)

        # The checkpoint should have existed before any tool executed
        assert first_checkpoint is not None, "Checkpoint should be saved before tool execution"
        assert "payload" in first_checkpoint
        # The payload should contain the assistant's tool_calls in its messages
        msgs = first_checkpoint["payload"]["messages"]
        assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
        assert any(m.get("tool_calls") for m in assistant_msgs)

    async def test_resume_from_regular_tool_checkpoint(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Simulate crash after tool round: manually write a checkpoint, then resume."""
        conv = chat_store.create(title="resume regular")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        # Build a checkpoint payload that looks like a complete tool round.
        # The payload messages contain: system + user + assistant(tool_calls) + tool(result)
        # When resumed, _run_with_tools dispatches this to the LLM which responds with tokens.
        checkpoint_payload = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "function": {"name": "shell.run", "arguments": '{"cmd": "ls"}'}}
                ]},
                {"role": "tool", "tool_call_id": "call_1", "content": "file1.txt\nfile2.txt"},
            ],
            "model": "test",
        }
        chat_store.save_checkpoint(conv.id, {
            "tool_round": 1,
            "payload": checkpoint_payload,
        })

        work = {
            "message": "hello",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        # Should have produced tokens (LLM response) and a done event
        assert "token" in event_types, f"Expected tokens from resumed loop. Events: {event_types}"
        assert "done" in event_types

        # Checkpoint should be cleared after successful resume
        assert chat_store.load_checkpoint(conv.id) is None

    async def test_resume_from_interaction_checkpoint(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Simulate crash while waiting for user input: checkpoint has pending_interaction."""
        from acai.orchestrator.input_queue import InputQueue

        conv = chat_store.create(title="resume interaction")
        chat_store.append(conv.id, {"role": "user", "content": "ask me"})
        # The tool_call was persisted before blocking
        chat_store.append(conv.id, {
            "role": "tool_call",
            "content": json.dumps({"tool": "interaction_ask_user", "args": {"question": "Pick one", "options": "[]"}}),
            "name": "interaction_ask_user",
        })

        checkpoint_payload = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "ask me"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_int", "function": {
                        "name": "interaction_ask_user",
                        "arguments": json.dumps({"question": "Pick one", "options": "[]", "allow_free_text": True}),
                    }}
                ]},
            ],
            "model": "test",
        }
        chat_store.save_checkpoint(conv.id, {
            "tool_round": 0,
            "payload": checkpoint_payload,
            "pending_interaction": {
                "tool_name": "interaction_ask_user",
                "tool_args": {"question": "Pick one", "options": "[]", "allow_free_text": True},
                "call_id": "call_int",
            },
        })

        input_queue = InputQueue()

        work = {
            "message": "ask me",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        import asyncio

        async def provide_answer():
            """Simulate user answering the re-emitted interaction after a short delay."""
            for _ in range(50):
                await asyncio.sleep(0.05)
                if input_queue.has_pending(conv.id):
                    input_queue.submit_input(conv.id, {"text": "Option A"})
                    return
            raise AssertionError("InputQueue never had a pending request")

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(
                worker, work,
                input_queue=input_queue,
                **graph_deps,
            )
            answer_task = asyncio.create_task(provide_answer())
            async for event in graph.run(work):
                events.append(event)
            await answer_task

        event_types = [e["event_type"] for e in events]

        # The resumed interaction should emit the widget and wait
        assert "interaction" in event_types, f"Missing interaction event. Events: {event_types}"
        assert "waiting_for_input" in event_types

        # After user answers, should produce tool_end and continue to LLM (tokens + done)
        assert "tool_end" in event_types
        assert "token" in event_types
        assert "done" in event_types

        # tool_result should be persisted with the user's answer
        messages = chat_store.read(conv.id)
        tool_results = [m for m in messages if m["role"] == "tool_result"]
        assert len(tool_results) >= 1
        assert tool_results[0]["content"] == "Option A"

        # Checkpoint should be cleared
        assert chat_store.load_checkpoint(conv.id) is None


@pytest.mark.asyncio
class TestCheckpointStaleHandling:
    """Verify that stale checkpoints are handled correctly when a user sends a new message."""

    async def test_pending_interaction_route_feeds_answer_into_checkpoint(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Simulates the conversation route logic: when the user sends a message
        and there's a pending_interaction checkpoint, the message is used as the
        answer, the checkpoint is updated, and the graph resumes from it."""
        conv = chat_store.create(title="route interaction resume")
        chat_store.append(conv.id, {"role": "user", "content": "ask me"})
        chat_store.append(conv.id, {
            "role": "tool_call",
            "content": json.dumps({"tool": "interaction_ask_user", "args": {"question": "Pick", "options": "[]"}}),
            "name": "interaction_ask_user",
        })

        checkpoint_payload = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "ask me"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_x", "function": {
                        "name": "interaction_ask_user",
                        "arguments": json.dumps({"question": "Pick", "options": "[]", "allow_free_text": True}),
                    }}
                ]},
            ],
            "model": "test",
        }
        chat_store.save_checkpoint(conv.id, {
            "tool_round": 0,
            "payload": checkpoint_payload,
            "pending_interaction": {
                "tool_name": "interaction_ask_user",
                "tool_args": {"question": "Pick", "options": "[]", "allow_free_text": True},
                "call_id": "call_x",
            },
        })

        user_reply = "My answer"

        # --- Replicate the conversation route logic ---
        _cp = chat_store.load_checkpoint(conv.id)
        assert _cp is not None
        assert "pending_interaction" in _cp

        _pending = _cp["pending_interaction"]
        _cp["payload"]["messages"].append({
            "role": "tool",
            "tool_call_id": _pending.get("call_id", ""),
            "content": user_reply,
        })
        del _cp["pending_interaction"]
        chat_store.save_checkpoint(conv.id, _cp)
        chat_store.append(conv.id, {
            "role": "tool_result",
            "content": user_reply,
            "name": _pending.get("tool_name", ""),
        })
        # --- End route logic ---

        work = {
            "message": user_reply,
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        # Should have resumed and produced tokens from the LLM
        assert "token" in event_types, f"Expected LLM response. Events: {event_types}"
        assert "done" in event_types

        # Checkpoint cleared
        assert chat_store.load_checkpoint(conv.id) is None

        # tool_result is in the conversation with the user's answer
        messages = chat_store.read(conv.id)
        tool_results = [m for m in messages if m["role"] == "tool_result"]
        assert any(tr["content"] == "My answer" for tr in tool_results)

    async def test_no_pending_interaction_clears_checkpoint(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When a user sends a message and there's a checkpoint WITHOUT
        pending_interaction, the checkpoint is cleared and a fresh turn starts."""
        conv = chat_store.create(title="stale no interaction")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        # Save a regular end-of-round checkpoint (no pending_interaction)
        chat_store.save_checkpoint(conv.id, {
            "tool_round": 1,
            "payload": {
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "hello"},
                ],
                "model": "test",
            },
        })

        # Simulate the route clearing it
        _cp = chat_store.load_checkpoint(conv.id)
        assert _cp is not None
        assert "pending_interaction" not in _cp

        # Route logic: no pending interaction → clear and start fresh
        chat_store.clear_checkpoint(conv.id)
        chat_store.append(conv.id, {"role": "user", "content": "new question"})

        assert chat_store.load_checkpoint(conv.id) is None
        messages = chat_store.read(conv.id)
        assert messages[-1] == {"role": "user", "content": "new question"}
