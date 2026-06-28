"""Tests for acai.orchestrator.chat — ChatStore and ConversationMeta."""

from __future__ import annotations

import json
import os

import pytest

from acai.orchestrator.chat import ChatStore, ConversationMeta


@pytest.fixture
def chat(tmp_path):
    """ChatStore backed by a temp directory."""
    return ChatStore(str(tmp_path))


class TestConversationMeta:

    def test_defaults(self):
        meta = ConversationMeta(id="abc123")
        assert meta.id == "abc123"
        assert meta.title == "abc123"
        assert meta.provider == "auto"
        assert meta.budget == 0.0
        assert meta.spent == 0.0

    def test_remaining_budget_unlimited(self):
        meta = ConversationMeta(id="x", budget=0.0)
        assert meta.remaining_budget is None

    def test_remaining_budget_with_budget(self):
        meta = ConversationMeta(id="x", budget=10.0, spent=3.5)
        assert meta.remaining_budget == pytest.approx(6.5)

    def test_to_dict(self):
        meta = ConversationMeta(id="t", title="Test", budget=5.0, spent=1.0)
        d = meta.to_dict()
        assert d["id"] == "t"
        assert d["title"] == "Test"
        assert d["budget"] == 5.0
        assert d["spent"] == 1.0

    def test_from_dict(self):
        d = {"id": "x", "title": "Hello", "budget": 2.0, "spent": 0.5,
             "project": "p", "agent": "default"}
        meta = ConversationMeta.from_dict(d)
        assert meta.id == "x"
        assert meta.title == "Hello"
        assert meta.budget == 2.0
        assert meta.spent == 0.5


class TestChatStoreCreate:

    def test_create_conversation(self, chat):
        conv = chat.create(title="test conv")
        assert conv.id
        assert conv.title == "test conv"
        assert len(conv.id) == 12

    def test_create_with_project(self, chat):
        conv = chat.create(title="proj conv", project="my-project")
        assert conv.project == "my-project"

    def test_create_writes_files(self, chat):
        conv = chat.create(title="disk check")
        meta_path = chat._meta_path(conv.id)
        msg_path = chat._msg_path(conv.id)
        assert os.path.isfile(meta_path)
        assert os.path.isfile(msg_path)


class TestChatStoreReadWrite:

    def test_read_empty_conversation(self, chat):
        conv = chat.create(title="empty")
        messages = chat.read(conv.id)
        assert messages == []

    def test_append_and_read(self, chat):
        conv = chat.create(title="rw")
        chat.append(conv.id, {"role": "user", "content": "hello"})
        chat.append(conv.id, {"role": "assistant", "content": "hi back"})

        messages = chat.read(conv.id)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["content"] == "hi back"

    def test_read_nonexistent(self, chat):
        assert chat.read("nonexistent_id") == []

    def test_append_multiple(self, chat):
        conv = chat.create(title="multi")
        for i in range(10):
            chat.append(conv.id, {"role": "user", "content": f"msg {i}"})
        messages = chat.read(conv.id)
        assert len(messages) == 10


class TestChatStoreMetadata:

    def test_get_meta(self, chat):
        conv = chat.create(title="meta test", agent="default")
        meta = chat.get_meta(conv.id)
        assert meta is not None
        assert meta["title"] == "meta test"
        assert meta["agent"] == "default"

    def test_get_meta_nonexistent(self, chat):
        assert chat.get_meta("fake_id_123") is None


class TestRecordSpend:

    def test_record_spend_basic(self, chat):
        conv = chat.create(title="budget test")
        total = chat.record_spend(conv.id, 1.5)
        assert total == pytest.approx(1.5)

    def test_record_spend_accumulates(self, chat):
        conv = chat.create(title="accumulate")
        chat.record_spend(conv.id, 1.0)
        chat.record_spend(conv.id, 2.0)
        total = chat.record_spend(conv.id, 0.5)
        assert total == pytest.approx(3.5)

    def test_record_spend_persists(self, chat):
        conv = chat.create(title="persist spend")
        chat.record_spend(conv.id, 5.0)

        meta = chat.get_meta(conv.id)
        assert meta["spent"] == pytest.approx(5.0)

    def test_record_spend_nonexistent(self, chat):
        result = chat.record_spend("no_such_conv", 1.0)
        assert result == 0.0


class TestListConversations:

    def test_list_empty(self, chat):
        convs = chat.list()
        assert convs == []

    def test_list_returns_created(self, chat):
        chat.create(title="c1")
        chat.create(title="c2")
        convs = chat.list()
        assert len(convs) == 2
        titles = {c["title"] for c in convs}
        assert "c1" in titles
        assert "c2" in titles

    def test_list_by_project(self, chat):
        chat.create(title="proj-a", project="alpha")
        chat.create(title="proj-b", project="alpha")
        chat.create(title="other", project="beta")
        convs = chat.list(project="alpha")
        titles = {c["title"] for c in convs}
        assert "proj-a" in titles
        assert "proj-b" in titles
        assert "other" not in titles

    def test_list_by_project_and_task(self, chat):
        conv = chat.create(title="task-conv", project="p", task_id="t1")
        convs = chat.list(project="p", task_id="t1")
        assert any(c["id"] == conv.id for c in convs)

    def test_list_project_skips_dotfiles_and_special_entries(self, chat):
        """Entries like .git, definition.json, conversations are skipped."""
        chat.create(title="proj-conv", project="proj1")
        proj_dir = os.path.join(chat._projects_root, "proj1")
        os.makedirs(os.path.join(proj_dir, ".hidden"), exist_ok=True)
        open(os.path.join(proj_dir, "definition.json"), "w").close()
        convs = chat.list(project="proj1")
        assert len(convs) >= 1

    def test_list_project_nonexistent_search_root(self, chat):
        """Listing a project with no task dirs doesn't error."""
        convs = chat.list(project="no-such-project")
        assert convs == []

    def test_list_project_with_task_subdirectories(self, chat):
        """Task-level subdirectories under a project are scanned."""
        chat.create(title="task-a", project="proj", task_id="task_1")
        chat.create(title="task-b", project="proj", task_id="task_2")
        convs = chat.list(project="proj")
        titles = {c["title"] for c in convs}
        assert "task-a" in titles
        assert "task-b" in titles


class TestConversationMetaEdgeCases:

    def test_to_dict_includes_task_id_when_set(self):
        meta = ConversationMeta(id="t", task_id="task99")
        d = meta.to_dict()
        assert d["task_id"] == "task99"

    def test_to_dict_omits_task_id_when_empty(self):
        meta = ConversationMeta(id="t", task_id="")
        d = meta.to_dict()
        assert "task_id" not in d

    def test_remaining_budget_clamps_to_zero(self):
        """Overspending doesn't produce negative remaining."""
        meta = ConversationMeta(id="x", budget=5.0, spent=10.0)
        assert meta.remaining_budget == 0.0

    def test_from_dict_minimal(self):
        """from_dict with only 'id' uses sane defaults."""
        meta = ConversationMeta.from_dict({"id": "bare"})
        assert meta.title == "bare"
        assert meta.provider == "auto"
        assert meta.tags == []
        assert meta.budget == 0.0

    def test_defaults_with_none_values(self):
        """None for optional string fields falls back to defaults."""
        meta = ConversationMeta(
            id="n", title=None, description=None,
            provider=None, agent=None, tags=None, created_at=None,
        )
        assert meta.title == "n"
        assert meta.description == ""
        assert meta.provider == "auto"
        assert meta.agent == ""
        assert meta.tags == []
        assert isinstance(meta.created_at, float)


class TestChatStoreDelete:

    def test_delete_existing_conversation(self, chat):
        conv = chat.create(title="doomed")
        assert chat.delete(conv.id) is True
        assert chat.get_meta(conv.id) is None
        assert chat.read(conv.id) == []

    def test_delete_nonexistent_returns_false(self, chat):
        assert chat.delete("does-not-exist") is False

    def test_delete_removes_from_index(self, chat):
        conv = chat.create(title="indexed")
        assert conv.id in chat._index
        chat.delete(conv.id)
        assert conv.id not in chat._index


class TestChatStoreWriteAndClear:

    def test_write_overwrites_messages(self, chat):
        conv = chat.create(title="overwrite")
        chat.append(conv.id, {"role": "user", "content": "first"})
        chat.write(conv.id, [{"role": "user", "content": "replaced"}])
        msgs = chat.read(conv.id)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "replaced"

    def test_clear_removes_messages(self, chat):
        conv = chat.create(title="clearable")
        chat.append(conv.id, {"role": "user", "content": "temp"})
        chat.clear(conv.id)
        assert chat.read(conv.id) == []

    def test_clear_nonexistent_no_error(self, chat):
        """Clearing a conversation that has no file doesn't raise."""
        chat.clear("ghost_id")


class TestChatStoreUpdateMeta:

    def test_update_meta_modifies_fields(self, chat):
        conv = chat.create(title="original")
        result = chat.update_meta(conv.id, title="updated", agent="gpt4")
        assert result is not None
        assert result["title"] == "updated"
        assert result["agent"] == "gpt4"

    def test_update_meta_nonexistent_returns_none(self, chat):
        assert chat.update_meta("missing_id", title="x") is None

    def test_update_meta_persists(self, chat):
        conv = chat.create(title="will-change")
        chat.update_meta(conv.id, title="changed")
        reloaded = chat.get_meta(conv.id)
        assert reloaded["title"] == "changed"


class TestCorruptedFiles:
    """Verify graceful handling of corrupted / invalid JSON on disk."""

    def test_read_corrupt_conversation_json(self, chat):
        conv = chat.create(title="corrupt-msg")
        msg_path = chat._msg_path(conv.id)
        with open(msg_path, "w") as f:
            f.write("{not valid json!!")
        assert chat.read(conv.id) == []

    def test_read_conversation_json_not_a_list(self, chat):
        """conversation.json containing a dict instead of a list returns []."""
        conv = chat.create(title="dict-msg")
        msg_path = chat._msg_path(conv.id)
        with open(msg_path, "w") as f:
            json.dump({"role": "user"}, f)
        assert chat.read(conv.id) == []

    def test_get_meta_corrupt_json(self, chat):
        conv = chat.create(title="corrupt-meta")
        meta_path = chat._meta_path(conv.id)
        with open(meta_path, "w") as f:
            f.write("<<<broken>>>")
        assert chat.get_meta(conv.id) is None

    def test_list_survives_corrupt_metadata(self, chat):
        """A corrupted metadata.json doesn't crash list(); a fallback entry is returned."""
        conv = chat.create(title="good")
        bad = chat.create(title="will-corrupt")
        meta_path = chat._meta_path(bad.id)
        with open(meta_path, "w") as f:
            f.write("not json")

        convs = chat.list()
        ids = {c["id"] for c in convs}
        assert conv.id in ids
        assert bad.id in ids
        bad_entry = next(c for c in convs if c["id"] == bad.id)
        assert bad_entry["title"] == bad.id

    def test_append_to_corrupt_conversation_recovers(self, chat):
        """Appending to a file with corrupt JSON starts fresh."""
        conv = chat.create(title="recover")
        msg_path = chat._msg_path(conv.id)
        with open(msg_path, "w") as f:
            f.write("NOT JSON")
        chat.append(conv.id, {"role": "user", "content": "fresh"})
        msgs = chat.read(conv.id)
        assert len(msgs) == 1
        assert msgs[0]["content"] == "fresh"

    def test_read_json_list_with_non_list_content(self, chat):
        """_read_json_list returns [] when file contains valid JSON that isn't a list."""
        conv = chat.create(title="non-list")
        msg_path = chat._msg_path(conv.id)
        with open(msg_path, "w") as f:
            json.dump("just a string", f)
        assert chat.read(conv.id) == []


class TestEphemeralAndIndexFallbacks:
    """Test _dir fallback paths: ephemeral IDs and index misses."""

    def test_ephemeral_conversation_path(self, chat):
        """Ephemeral IDs resolve to the tmp directory."""
        d = chat._dir("ephemeral-abc123")
        assert "tmp" in d
        assert d.endswith("ephemeral-abc123")

    def test_unknown_id_falls_back_to_base(self, chat):
        """Unknown non-ephemeral IDs resolve to the conversations base."""
        d = chat._dir("totally_unknown")
        assert d.endswith("totally_unknown")
        assert "conversations" in d

    def test_dir_finds_id_after_index_rebuild(self, chat, tmp_path):
        """If a conversation exists on disk but isn't indexed, _dir rebuilds and finds it."""
        conv_dir = os.path.join(str(tmp_path), "conversations", "manual123")
        os.makedirs(conv_dir, exist_ok=True)
        with open(os.path.join(conv_dir, "metadata.json"), "w") as f:
            json.dump({"id": "manual123", "title": "manual"}, f)
        result = chat._dir("manual123")
        assert result == conv_dir


class TestAutoTitle:

    def test_first_user_message_sets_title(self, chat):
        conv = chat.create(title="untitled")
        chat.append(conv.id, {"role": "user", "content": "Hello world"})
        meta = chat.get_meta(conv.id)
        assert meta["title"] == "Hello world"

    def test_auto_title_truncates_at_80_chars(self, chat):
        conv = chat.create(title="untitled")
        long_msg = "x" * 200
        chat.append(conv.id, {"role": "user", "content": long_msg})
        meta = chat.get_meta(conv.id)
        assert len(meta["title"]) == 80

    def test_auto_title_uses_first_line_only(self, chat):
        conv = chat.create(title="untitled")
        chat.append(conv.id, {"role": "user", "content": "First line\nSecond line"})
        meta = chat.get_meta(conv.id)
        assert meta["title"] == "First line"

    def test_auto_title_skipped_for_empty_content(self, chat):
        conv = chat.create(title="stays")
        chat.append(conv.id, {"role": "user", "content": "   "})
        meta = chat.get_meta(conv.id)
        assert meta["title"] == "stays"

    def test_auto_title_not_triggered_on_second_message(self, chat):
        conv = chat.create(title="untitled")
        chat.append(conv.id, {"role": "user", "content": "First"})
        chat.append(conv.id, {"role": "user", "content": "Second"})
        meta = chat.get_meta(conv.id)
        assert meta["title"] == "First"

    def test_auto_title_not_triggered_for_assistant(self, chat):
        """Non-user roles don't trigger auto-titling."""
        conv = chat.create(title="original")
        chat.append(conv.id, {"role": "assistant", "content": "Bot says hi"})
        meta = chat.get_meta(conv.id)
        assert meta["title"] == "original"


class TestTaskConversations:

    def test_task_history_empty(self, chat):
        assert chat.task_history("proj", "no_task") == []

    def test_save_and_read_task_conversation(self, chat):
        msgs = [{"role": "user", "content": "hi"}]
        path = chat.save_task_conversation("proj", "task1", msgs)
        assert path.endswith("conv_1.json")

        loaded = chat.read_task_conversation(path)
        assert loaded == msgs

    def test_task_history_ordering(self, chat):
        chat.save_task_conversation("p", "t", [{"role": "user", "content": "1"}])
        chat.save_task_conversation("p", "t", [{"role": "user", "content": "2"}])
        chat.save_task_conversation("p", "t", [{"role": "user", "content": "3"}])
        paths = chat.task_history("p", "t")
        assert len(paths) == 3
        assert paths[0].endswith("conv_1.json")
        assert paths[2].endswith("conv_3.json")

    def test_read_task_conversation_missing_file(self, chat):
        assert chat.read_task_conversation("/nonexistent/path.json") == []

    def test_read_task_conversation_corrupt_json(self, chat, tmp_path):
        bad = str(tmp_path / "bad.json")
        with open(bad, "w") as f:
            f.write("{{broken")
        assert chat.read_task_conversation(bad) == []

    def test_read_task_conversation_non_list(self, chat, tmp_path):
        """A task conversation file that doesn't contain a list returns []."""
        bad = str(tmp_path / "dict.json")
        with open(bad, "w") as f:
            json.dump({"not": "a list"}, f)
        assert chat.read_task_conversation(bad) == []

    def test_task_history_ignores_non_conv_files(self, chat):
        """Only conv_N.json files are returned; other files are ignored."""
        msgs = [{"role": "user", "content": "hi"}]
        chat.save_task_conversation("p", "t", msgs)
        td = chat._task_dir("p", "t")
        with open(os.path.join(td, "notes.txt"), "w") as f:
            f.write("not a conversation")
        with open(os.path.join(td, "metadata.json"), "w") as f:
            json.dump({}, f)
        paths = chat.task_history("p", "t")
        assert len(paths) == 1
        assert paths[0].endswith("conv_1.json")


class TestCreateWithTaskId:

    def test_create_with_project_and_task(self, chat):
        conv = chat.create(title="task conv", project="proj", task_id="task_1")
        assert conv.task_id == "task_1"
        d = chat._dir(conv.id)
        assert "task_1" in d


class TestRebuildIndex:
    """Verify the index rebuilds correctly across project/task structures."""

    def test_rebuild_finds_project_conversations(self, chat):
        conv = chat.create(title="in-proj", project="myproj")
        chat._index.clear()
        chat._rebuild_index()
        assert conv.id in chat._index

    def test_rebuild_finds_task_conversations(self, chat):
        conv = chat.create(title="in-task", project="myproj", task_id="t42")
        chat._index.clear()
        chat._rebuild_index()
        assert conv.id in chat._index

    def test_rebuild_skips_non_directory_entries(self, chat):
        """Files inside projects root don't cause errors."""
        os.makedirs(chat._projects_root, exist_ok=True)
        with open(os.path.join(chat._projects_root, "stray_file.txt"), "w") as f:
            f.write("not a dir")
        chat._rebuild_index()

    def test_rebuild_skips_dotdirs_in_projects(self, chat):
        conv = chat.create(title="legit", project="realproj")
        proj_dir = os.path.join(chat._projects_root, "realproj")
        os.makedirs(os.path.join(proj_dir, ".git"), exist_ok=True)
        os.makedirs(os.path.join(proj_dir, ".worktrees"), exist_ok=True)
        chat._rebuild_index()
        assert conv.id in chat._index


class TestAppendToNewConversation:

    def test_append_creates_directory_if_missing(self, chat):
        """Appending to a never-created ID auto-creates the directory."""
        chat.append("brand_new", {"role": "user", "content": "hello"})
        msgs = chat.read("brand_new")
        assert len(msgs) == 1
