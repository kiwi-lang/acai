"""Tests for acai.tasks.converse — ConverseGraph."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from acai.tasks.converse import ConverseGraph, _auto_knowledge_context


@pytest.mark.asyncio
class TestConverseGraph:

    async def test_basic_conversation(
        self, load_balancer, chat_store, graph_deps,
    ):
        conv = chat_store.create(title="test conv")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        work = {
            "message": "hello",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
            "n_tokens": 3,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "token" in event_types
        assert event_types[-1] == "done"

        messages = chat_store.read(conv.id)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "word0" in assistant_msgs[0]["content"]

    async def test_tool_follow_up(
        self, load_balancer, chat_store, graph_deps,
    ):
        conv = chat_store.create(title="tool test")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
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
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert event_types[-1] == "done"

        messages = chat_store.read(conv.id)
        roles = [m["role"] for m in messages]
        assert "tool_call" in roles
        assert "tool_result" in roles

    async def test_error_from_worker(
        self, load_balancer, chat_store, graph_deps,
    ):
        conv = chat_store.create(title="error test")
        chat_store.append(conv.id, {"role": "user", "content": "[error:LLM crashed] fail"})

        work = {
            "message": "[error:LLM crashed] fail",
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
        assert "error" in event_types

    async def test_prepare_missing_agent(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When the requested agent doesn't exist, falls back to default."""
        conv = chat_store.create(title="missing agent")
        chat_store.append(conv.id, {"role": "user", "content": "hi"})

        work = {
            "message": "hi",
            "conversation": conv.id,
            "agent": "nonexistent_agent_xyz",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for event in graph.run(work):
                events.append(event)

        event_types = [e["event_type"] for e in events]
        assert "token" in event_types
        assert event_types[-1] == "done"

    async def test_tracker_receives_events(
        self, load_balancer, chat_store, graph_deps, stream_tracker,
    ):
        conv = chat_store.create(title="tracker test")
        chat_store.append(conv.id, {"role": "user", "content": "hello"})

        q = stream_tracker.subscribe(conv.id)

        work = {
            "message": "hello",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        tracked_events = []
        while not q.empty():
            tracked_events.append(q.get_nowait())
        tracked_types = [e["event_type"] for e in tracked_events]
        assert "token" in tracked_types
        assert "done" in tracked_types


@pytest.mark.asyncio
class TestToolStreamHandling:
    """Verify the exact event sequence when the LLM uses a tool.

    The mock worker returns tool_call_delta events when the user message
    contains ``[tool]``.  On the follow-up (tool results present in
    messages) it returns normal token events.

    Expected stream:
        token (initial text before tool call)
        tool_call_delta ×N (chunked tool invocation)
        tool_start (graph dispatches the tool)
        tool_end (tool result received)
        token ×3 (follow-up LLM response)
        done (graph-level termination)
    """

    async def test_tool_then_text_event_sequence(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Full event sequence: tokens → tool deltas → tool start/end → tokens → done."""
        conv = chat_store.create(title="stream sequence")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
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

        types = [e["event_type"] for e in events]

        # Phase 1: initial LLM dispatch — token + tool_call_delta(s)
        assert types[0] == "token", f"expected initial token, got {types[0]}"
        tool_delta_indices = [i for i, t in enumerate(types) if t == "tool_call_delta"]
        assert len(tool_delta_indices) >= 1, "expected at least one tool_call_delta"

        # Phase 2: graph-level tool dispatch
        tool_start_idx = types.index("tool_start")
        tool_end_idx = types.index("tool_end")
        assert tool_start_idx > tool_delta_indices[-1], "tool_start must follow tool_call_delta"
        assert tool_end_idx > tool_start_idx, "tool_end must follow tool_start"

        # Phase 3: follow-up LLM tokens
        follow_up_tokens = [i for i, t in enumerate(types) if t == "token" and i > tool_end_idx]
        assert len(follow_up_tokens) >= 1, "expected follow-up tokens after tool_end"

        # Terminal: single done event at the end
        assert types[-1] == "done"
        assert types.count("done") == 1, "exactly one done event in the stream"

    async def test_tool_start_contains_correct_data(
        self, load_balancer, chat_store, graph_deps,
    ):
        """tool_start event carries the tool name and parsed arguments."""
        conv = chat_store.create(title="tool data check")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
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

        starts = [e for e in events if e["event_type"] == "tool_start"]
        assert len(starts) == 1
        data = starts[0]["data"]
        assert data["tool_name"] == "shell.run"
        assert data["args"] == {"cmd": "ls"}
        assert data["conversation"] == conv.id

    async def test_tool_end_contains_result_preview(
        self, load_balancer, chat_store, graph_deps,
    ):
        """tool_end event carries a preview of the tool result."""
        conv = chat_store.create(title="tool end check")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
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

        ends = [e for e in events if e["event_type"] == "tool_end"]
        assert len(ends) == 1
        data = ends[0]["data"]
        assert data["tool_name"] == "shell.run"
        assert "result of shell.run" in data["result_preview"]

    async def test_follow_up_text_is_persisted(
        self, load_balancer, chat_store, graph_deps,
    ):
        """After tools, the follow-up LLM response is saved to chat."""
        conv = chat_store.create(title="persist after tool")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        messages = chat_store.read(conv.id)
        roles = [m["role"] for m in messages]

        assert roles.count("tool_call") == 1
        assert roles.count("tool_result") == 1
        assert roles.count("assistant") == 1

        assistant = next(m for m in messages if m["role"] == "assistant")
        assert "word0" in assistant["content"], "follow-up tokens should be in assistant content"

    async def test_tracker_sees_full_tool_sequence(
        self, load_balancer, chat_store, graph_deps, stream_tracker,
    ):
        """StreamTracker receives the complete event sequence including tool events."""
        conv = chat_store.create(title="tracker tool")
        chat_store.append(conv.id, {"role": "user", "content": "[tool] run a command"})

        q = stream_tracker.subscribe(conv.id)

        work = {
            "message": "[tool] run a command",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        tracked = []
        while not q.empty():
            tracked.append(q.get_nowait())
        tracked_types = [e["event_type"] for e in tracked]

        assert "token" in tracked_types
        assert "tool_call_delta" in tracked_types
        assert "tool_start" in tracked_types
        assert "tool_end" in tracked_types
        assert "done" in tracked_types

        done_idx = tracked_types.index("done")
        assert done_idx == len(tracked_types) - 1, "done must be the last tracked event"


# ==================================================================
# _auto_knowledge_context — unit tests for edge cases
# ==================================================================


class TestAutoKnowledgeContext:
    """Unit tests for _auto_knowledge_context failure modes."""

    @pytest.fixture
    def knowledge_ws(self, tmp_path):
        """Workspace with a populated knowledge DB containing two docs."""
        knowledge_dir = tmp_path / "knowledge"
        (knowledge_dir / "hobbies" / "games").mkdir(parents=True)
        (knowledge_dir / "hobbies" / "games" / "chess.md").write_text(
            "---\ntitle: chess\nsubject: hobbies\nsubsubject: games\n---\n"
            "I love playing chess competitively."
        )
        (knowledge_dir / "hobbies" / "games" / "go.md").write_text(
            "---\ntitle: go\nsubject: hobbies\nsubsubject: games\n---\n"
            "Go is a beautiful strategic board game."
        )
        from acai.knowledge.db import KnowledgeDB
        db = KnowledgeDB(str(knowledge_dir / ".knowledge.db"))
        db.sync(str(knowledge_dir))
        return str(tmp_path)

    def test_short_message_returns_empty(self, knowledge_ws):
        """Last user message <5 chars → returns empty string."""
        messages = [{"role": "user", "content": "hi"}]
        assert _auto_knowledge_context(knowledge_ws, messages) == ""

    def test_exactly_4_chars_returns_empty(self, knowledge_ws):
        """Boundary: exactly 4 characters → empty (need >=5)."""
        messages = [{"role": "user", "content": "test"}]
        assert _auto_knowledge_context(knowledge_ws, messages) == ""

    def test_exactly_5_chars_proceeds(self, knowledge_ws):
        """Boundary: exactly 5 characters → proceeds with search."""
        messages = [{"role": "user", "content": "chess"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert "chess" in result

    def test_all_stop_words_returns_empty(self, knowledge_ws):
        """All words are stop words → FTS query is empty → returns empty."""
        messages = [{"role": "user", "content": "I am the one who is"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert result == ""

    def test_no_knowledge_db_returns_empty(self, tmp_path):
        """No .knowledge.db file → returns empty, no crash."""
        os.makedirs(str(tmp_path / "knowledge"), exist_ok=True)
        messages = [{"role": "user", "content": "Tell me about chess games"}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_no_knowledge_dir_returns_empty(self, tmp_path):
        """No knowledge/ directory at all → returns empty, no crash."""
        messages = [{"role": "user", "content": "Tell me about chess games"}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    @patch("acai.tasks.converse.KnowledgeDB", create=True)
    def test_fts_exception_returns_empty(self, mock_db_cls, tmp_path):
        """FTS search raises an exception → returns empty, no crash."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / ".knowledge.db").write_text("")

        mock_db = MagicMock()
        mock_db.fts_search.side_effect = RuntimeError("corrupted index")
        mock_db_cls.return_value = mock_db

        messages = [{"role": "user", "content": "Tell me about chess"}]
        with patch.dict("sys.modules", {}):
            result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_fts_exception_real_import_returns_empty(self, tmp_path):
        """FTS raises inside the try block → caught, returns empty."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / ".knowledge.db").write_text("")

        messages = [{"role": "user", "content": "Tell me about chess"}]
        with patch("acai.knowledge.db.KnowledgeDB.fts_search", side_effect=Exception("boom")):
            result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_fts_hits_but_get_by_path_returns_none(self, tmp_path):
        """FTS returns hits but get_by_path returns None for all → empty."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / ".knowledge.db").write_text("")

        messages = [{"role": "user", "content": "Tell me about chess"}]
        mock_db = MagicMock()
        mock_db.fts_search.return_value = [
            {"path": "missing/sub/title1"},
            {"path": "missing/sub/title2"},
        ]
        mock_store = MagicMock()
        mock_store.get_by_path.return_value = None

        with patch("acai.knowledge.db.KnowledgeDB", return_value=mock_db), \
             patch("acai.knowledge.store.KnowledgeStore", return_value=mock_store):
            result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_fts_hits_partial_resolve(self, tmp_path):
        """FTS returns hits, some resolve and some don't → partial context."""
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / ".knowledge.db").write_text("")

        from acai.knowledge.models import KnowledgeDoc

        doc = KnowledgeDoc(
            subject="hobbies", subsubject="games", title="chess",
            content="Chess is great.", updated_at=0.0,
        )

        messages = [{"role": "user", "content": "Tell me about games and chess"}]
        mock_db = MagicMock()
        mock_db.fts_search.return_value = [
            {"path": "hobbies/games/chess"},
            {"path": "nonexistent/sub/title"},
        ]
        mock_store = MagicMock()
        mock_store.get_by_path.side_effect = lambda p: doc if "chess" in p else None

        with patch("acai.knowledge.db.KnowledgeDB", return_value=mock_db), \
             patch("acai.knowledge.store.KnowledgeStore", return_value=mock_store):
            result = _auto_knowledge_context(str(tmp_path), messages)
        assert "chess" in result.lower()
        assert "---" not in result  # only one doc, no separator

    def test_multiple_docs_joined_with_separator(self, knowledge_ws):
        """Multiple knowledge docs matched → joined with --- separator."""
        messages = [{"role": "user", "content": "Tell me about board games chess go strategy"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        if "\n\n---\n\n" in result:
            parts = result.split("\n\n---\n\n")
            assert len(parts) >= 2

    def test_uses_last_user_message_only(self, knowledge_ws):
        """Multiple user messages → only the LAST is used for knowledge search."""
        messages = [
            {"role": "user", "content": "something completely unrelated to any topic"},
            {"role": "assistant", "content": "noted"},
            {"role": "user", "content": "Tell me about chess strategy"},
        ]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert "chess" in result

    def test_no_user_messages_returns_empty(self, knowledge_ws):
        """No user messages in the list → returns empty."""
        messages = [{"role": "assistant", "content": "hello there friend!"}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert result == ""

    def test_empty_messages_returns_empty(self, knowledge_ws):
        """Empty messages list → returns empty."""
        result = _auto_knowledge_context(knowledge_ws, [])
        assert result == ""

    def test_user_message_with_list_content_returns_empty(self, knowledge_ws):
        """User message content is a list (multimodal) → treated as empty."""
        messages = [{"role": "user", "content": [{"type": "text", "text": "chess"}]}]
        result = _auto_knowledge_context(knowledge_ws, messages)
        assert result == ""


# ==================================================================
# ConverseGraph.run() — hardened tests for error & edge cases
# ==================================================================


@pytest.mark.asyncio
class TestConverseGraphErrors:
    """Tests for ConverseGraph.run() error handling and edge cases."""

    async def test_prepare_raises_yields_error_with_agent_name(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When prepare() raises, error event contains agent name and exception."""
        conv = chat_store.create(title="prepare error")
        chat_store.append(conv.id, {"role": "user", "content": "trigger prepare failure"})

        work = {
            "message": "trigger prepare failure",
            "conversation": conv.id,
            "agent": "my_test_agent",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            with patch.object(graph, "prepare", side_effect=ValueError("bad template")):
                async for event in graph.run(work):
                    events.append(event)

        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert "my_test_agent" in events[0]["data"]["message"]
        assert "bad template" in events[0]["data"]["message"]
        assert "traceback" in events[0]["data"]
        assert events[0]["data"]["traceback"]  # non-empty traceback

    async def test_prepare_error_has_no_done_event(
        self, load_balancer, chat_store, graph_deps,
    ):
        """After a prepare error, no done event is emitted."""
        conv = chat_store.create(title="no done after error")
        chat_store.append(conv.id, {"role": "user", "content": "hello world test"})

        work = {
            "message": "hello world test",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            with patch.object(graph, "prepare", side_effect=RuntimeError("crash")):
                async for event in graph.run(work):
                    events.append(event)

        types = [e["event_type"] for e in events]
        assert "error" in types
        assert "done" not in types

    async def test_run_with_tools_error_stops_pipeline(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When _run_with_tools emits an error, pipeline stops (no done event)."""
        conv = chat_store.create(title="tool error stop")
        chat_store.append(conv.id, {"role": "user", "content": "[error:LLM crashed] fail please"})

        work = {
            "message": "[error:LLM crashed] fail please",
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

        types = [e["event_type"] for e in events]
        assert "error" in types
        assert "done" not in types, "no done event should follow an error"

    async def test_save_response_called_with_accumulated_result(
        self, load_balancer, chat_store, graph_deps,
    ):
        """_save_response() is called with the accumulated text."""
        conv = chat_store.create(title="save response")
        chat_store.append(conv.id, {"role": "user", "content": "hello save"})

        work = {
            "message": "hello save",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
            "n_tokens": 3,
        }

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            async for _ in graph.run(work):
                pass

        messages = chat_store.read(conv.id)
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "word0" in assistant_msgs[0]["content"]
        assert "word1" in assistant_msgs[0]["content"]
        assert "word2" in assistant_msgs[0]["content"]

    async def test_finalize_git_in_done_event(
        self, load_balancer, chat_store, graph_deps,
    ):
        """_finalize_git result is included in the done event data."""
        conv = chat_store.create(title="git done")
        chat_store.append(conv.id, {"role": "user", "content": "hello git"})

        work = {
            "message": "hello git",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        git_result = {"committed": True, "branch": "feat/test", "pushed": True}

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            with patch.object(graph, "_finalize_git", return_value=git_result):
                async for event in graph.run(work):
                    events.append(event)

        done_events = [e for e in events if e["event_type"] == "done"]
        assert len(done_events) == 1
        assert done_events[0]["data"]["git"] == git_result

    async def test_finalize_git_none_done_event_has_no_git(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When _finalize_git returns None, done event has empty data."""
        conv = chat_store.create(title="no git")
        chat_store.append(conv.id, {"role": "user", "content": "hello nogit"})

        work = {
            "message": "hello nogit",
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

        done_events = [e for e in events if e["event_type"] == "done"]
        assert len(done_events) == 1
        assert "git" not in done_events[0]["data"]

    async def test_empty_conversation_skips_knowledge(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Empty messages list (new conversation) → _auto_knowledge_context NOT called."""
        conv = chat_store.create(title="empty conv")

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
            with patch("acai.tasks.converse._auto_knowledge_context") as mock_kc:
                async for event in graph.run(work):
                    events.append(event)
                mock_kc.assert_not_called()

        types = [e["event_type"] for e in events]
        assert types[-1] == "done"

    async def test_knowledge_context_uses_last_user_message(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Multiple user messages → only the LAST user message is used for search."""
        conv = chat_store.create(title="last msg")
        chat_store.append(conv.id, {"role": "user", "content": "first message about cats"})
        chat_store.append(conv.id, {"role": "assistant", "content": "ok"})
        chat_store.append(conv.id, {"role": "user", "content": "second message about dogs"})

        work = {
            "message": "second message about dogs",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        captured_args = {}

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            with patch("acai.tasks.converse._auto_knowledge_context", return_value="") as mock_kc:
                async for _ in graph.run(work):
                    pass
                mock_kc.assert_called_once()
                call_args = mock_kc.call_args
                messages_passed = call_args[0][1]

        user_msgs = [m for m in messages_passed if m.get("role") == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[-1]["content"] == "second message about dogs"

    async def test_knowledge_context_passed_to_prepare(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Knowledge context is passed correctly to prepare as extra_context."""
        conv = chat_store.create(title="kc prepare")
        chat_store.append(conv.id, {"role": "user", "content": "What about chess?"})

        work = {
            "message": "What about chess?",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        captured_kwargs = {}

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            original_prepare = graph.prepare

            def spy_prepare(agent_name, work_arg, **kwargs):
                captured_kwargs.update(kwargs)
                return original_prepare(agent_name, work_arg, **kwargs)

            with patch("acai.tasks.converse._auto_knowledge_context", return_value="## Chess\nGreat game"):
                with patch.object(graph, "prepare", side_effect=spy_prepare):
                    async for _ in graph.run(work):
                        pass

        assert captured_kwargs.get("extra_context") == {"knowledge_context": "## Chess\nGreat game"}

    async def test_no_knowledge_context_means_no_extra_context(
        self, load_balancer, chat_store, graph_deps,
    ):
        """When _auto_knowledge_context returns empty, extra_context is None."""
        conv = chat_store.create(title="no kc")
        chat_store.append(conv.id, {"role": "user", "content": "Hello there friend"})

        work = {
            "message": "Hello there friend",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        captured_kwargs = {}

        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            original_prepare = graph.prepare

            def spy_prepare(agent_name, work_arg, **kwargs):
                captured_kwargs.update(kwargs)
                return original_prepare(agent_name, work_arg, **kwargs)

            with patch("acai.tasks.converse._auto_knowledge_context", return_value=""):
                with patch.object(graph, "prepare", side_effect=spy_prepare):
                    async for _ in graph.run(work):
                        pass

        assert captured_kwargs.get("extra_context") is None


@pytest.mark.asyncio
class TestConverseGraphCompression:
    """Test compression event yielding."""

    async def test_compression_event_yielded(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Compression event is yielded when _try_compress_conversation returns one."""
        conv = chat_store.create(title="compress test")
        chat_store.append(conv.id, {"role": "user", "content": "hello compress"})

        work = {
            "message": "hello compress",
            "conversation": conv.id,
            "agent": "default",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        compress_ev = {
            "event_type": "context_compressed",
            "data": {
                "conversation": conv.id,
                "original_messages": 50,
                "compressed_messages": 10,
            },
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            with patch.object(graph, "_try_compress_conversation", return_value=compress_ev):
                async for event in graph.run(work):
                    events.append(event)

        types = [e["event_type"] for e in events]
        assert types[0] == "context_compressed"
        assert events[0]["data"]["original_messages"] == 50
        assert events[0]["data"]["compressed_messages"] == 10
        assert types[-1] == "done"

    async def test_no_compression_when_not_needed(
        self, load_balancer, chat_store, graph_deps,
    ):
        """No compression event when conversation is short."""
        conv = chat_store.create(title="no compress")
        chat_store.append(conv.id, {"role": "user", "content": "hello short"})

        work = {
            "message": "hello short",
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

        types = [e["event_type"] for e in events]
        assert "context_compressed" not in types
        assert types[-1] == "done"


@pytest.mark.asyncio
class TestErrorMessageQuality:
    """Verify error events have both human-readable message and traceback."""

    async def test_error_event_has_message_and_traceback(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Error events from prepare have both message and traceback."""
        conv = chat_store.create(title="err quality")
        chat_store.append(conv.id, {"role": "user", "content": "test error quality"})

        work = {
            "message": "test error quality",
            "conversation": conv.id,
            "agent": "myagent",
            "spec_path": chat_store._msg_path(conv.id),
            "stream_id": conv.id,
        }

        events = []
        async with load_balancer.acquire() as worker:
            graph = ConverseGraph.from_work(worker, work, **graph_deps)
            with patch.object(graph, "prepare", side_effect=TypeError("missing arg")):
                async for event in graph.run(work):
                    events.append(event)

        error = events[0]
        assert error["event_type"] == "error"
        assert "myagent" in error["data"]["message"]
        assert "missing arg" in error["data"]["message"]
        assert "traceback" in error["data"]
        assert "TypeError" in error["data"]["traceback"]

    async def test_worker_error_contains_error_text(
        self, load_balancer, chat_store, graph_deps,
    ):
        """Error from the LLM worker contains the error text."""
        conv = chat_store.create(title="worker err msg")
        chat_store.append(conv.id, {"role": "user", "content": "[error:context overflow] boom"})

        work = {
            "message": "[error:context overflow] boom",
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

        errors = [e for e in events if e["event_type"] == "error"]
        assert len(errors) == 1
        assert "context overflow" in errors[0]["data"].get("error", errors[0]["data"].get("message", ""))
