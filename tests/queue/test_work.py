"""Tests for acai.queue.work — WorkQueue push/pop/update/list."""

from __future__ import annotations

import pytest

from acai.queue.work import Task, TaskStatus, WorkQueue


@pytest.fixture()
def queue(tmp_path):
    url = f"sqlite:///{tmp_path / 'test.db'}"
    return WorkQueue(url)


class TestPush:
    def test_push_returns_task(self, queue):
        t = queue.push("do stuff")
        assert isinstance(t, Task)
        assert t.title == "do stuff"
        assert t.status == TaskStatus.PENDING
        assert t.id

    def test_push_with_all_fields(self, queue):
        t = queue.push(
            "complex",
            description="desc",
            priority=5,
            kind="build",
            gpu=1,
            project="proj",
            agent="coder",
            max_retries=1,
            spec="spec-data",
            spec_path="/tmp/spec.json",
            enable_thinking=True,
            conversation="conv-1",
        )
        assert t.description == "desc"
        assert t.priority == 5
        assert t.kind == "build"
        assert t.gpu == 1
        assert t.project == "proj"
        assert t.agent == "coder"
        assert t.max_retries == 1
        assert t.spec == "spec-data"
        assert t.spec_path == "/tmp/spec.json"
        assert t.enable_thinking is True
        assert t.conversation == "conv-1"

    def test_push_with_dependencies(self, queue):
        t = queue.push("child", depends_on=["abc", "def"])
        assert t.depends_on == "abc,def"


class TestPop:
    def test_pop_empty(self, queue):
        assert queue.pop() is None

    def test_pop_returns_highest_priority(self, queue):
        queue.push("low", priority=1)
        high = queue.push("high", priority=10)
        queue.push("mid", priority=5)
        queue.pop()
        refreshed = queue.get(high.id)
        assert refreshed.title == "high"
        assert refreshed.status == TaskStatus.IN_PROGRESS
        assert refreshed.started_at is not None

    def test_pop_subtasks_before_root(self, queue):
        root = queue.push("root task")
        sub = queue.push("sub task", parent_task=root.id, root_task=root.id)
        queue.pop()
        refreshed_sub = queue.get(sub.id)
        assert refreshed_sub.status == TaskStatus.IN_PROGRESS

    def test_pop_respects_dependency(self, queue):
        dep = queue.push("dependency")
        child = queue.push("child", depends_on=[dep.id])
        queue.pop()
        refreshed_dep = queue.get(dep.id)
        assert refreshed_dep.status == TaskStatus.IN_PROGRESS
        # child should not pop until dep is completed
        assert queue.pop() is None
        queue.update(dep.id, status=TaskStatus.COMPLETED)
        queue.pop()
        refreshed_child = queue.get(child.id)
        assert refreshed_child.status == TaskStatus.IN_PROGRESS

    def test_pop_with_status_filter(self, queue):
        queue.push("t1")
        t2 = queue.push("t2")
        queue.update(t2.id, status=TaskStatus.READY)
        queue.pop(status=TaskStatus.READY)
        refreshed = queue.get(t2.id)
        assert refreshed.status == TaskStatus.IN_PROGRESS
        assert queue.pop(status=TaskStatus.READY) is None


class TestUpdate:
    def test_update_fields(self, queue):
        t = queue.push("task")
        queue.update(t.id, status=TaskStatus.COMPLETED, error_log="oops")
        updated = queue.get(t.id)
        assert updated.status == TaskStatus.COMPLETED
        assert updated.error_log == "oops"

    def test_update_nonexistent_noop(self, queue):
        queue.update("nonexistent-id", status="done")

    def test_update_to_in_progress_sets_started_at(self, queue):
        t = queue.push("task")
        assert t.started_at is None
        queue.update(t.id, status=TaskStatus.IN_PROGRESS)
        refreshed = queue.get(t.id)
        assert refreshed.started_at is not None


class TestGet:
    def test_get_existing(self, queue):
        t = queue.push("task")
        got = queue.get(t.id)
        assert got.title == "task"

    def test_get_nonexistent(self, queue):
        assert queue.get("no-such-id") is None


class TestList:
    def test_list_all(self, queue):
        queue.push("a")
        queue.push("b")
        assert len(queue.list()) == 2

    def test_list_by_status(self, queue):
        queue.push("pending")
        t = queue.push("completed")
        queue.update(t.id, status=TaskStatus.COMPLETED)
        pending = queue.list(status=TaskStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].title == "pending"

    def test_list_by_project(self, queue):
        queue.push("a", project="proj1")
        queue.push("b", project="proj2")
        result = queue.list(project="proj1")
        assert len(result) == 1
        assert result[0].project == "proj1"

    def test_list_root_only(self, queue):
        root = queue.push("root")
        queue.push("child", parent_task=root.id, root_task=root.id)
        roots = queue.list(root_only=True)
        assert len(roots) == 1
        assert roots[0].id == root.id


class TestListTree:
    def test_list_tree(self, queue):
        root = queue.push("root")
        child1 = queue.push("child1", parent_task=root.id, root_task=root.id)
        child2 = queue.push("child2", parent_task=root.id, root_task=root.id)
        tree = queue.list_tree(root.id)
        ids = {t.id for t in tree}
        assert root.id in ids
        assert child1.id in ids
        assert child2.id in ids


class TestResolveRoot:
    def test_resolve_root_for_direct_child(self, queue):
        root = queue.push("root")
        assert queue.resolve_root(root.id) == root.id

    def test_resolve_root_for_grandchild(self, queue):
        root = queue.push("root")
        child = queue.push("child", parent_task=root.id, root_task=root.id)
        assert queue.resolve_root(child.id) == root.id

    def test_resolve_root_nonexistent(self, queue):
        assert queue.resolve_root("nope") == ""


class TestMigration:
    def test_migration_is_idempotent(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'test.db'}"
        q1 = WorkQueue(url)
        q1.push("task")
        q2 = WorkQueue(url)
        assert q2.get(q1.list()[0].id) is not None
