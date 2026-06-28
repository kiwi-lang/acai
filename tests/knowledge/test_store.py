"""Tests for acai.knowledge.store — KnowledgeStore filesystem operations."""

from __future__ import annotations

import os
import time

import pytest

from acai.knowledge.store import KnowledgeStore, KnowledgeDoc, slugify


@pytest.fixture
def store(tmp_path):
    """Fresh KnowledgeStore backed by a temp directory."""
    return KnowledgeStore(str(tmp_path / "knowledge"))


class TestSlugify:

    def test_lowercase(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars_removed(self):
        assert slugify("foo!baz") == "foobaz"

    def test_spaces_become_hyphens(self):
        assert slugify("a   b") == "a-b"

    def test_strip_edges(self):
        assert slugify(" -hello- ") == "hello"

    def test_empty_becomes_untitled(self):
        assert slugify("") == "untitled"


class TestCreate:

    def test_creates_file(self, store):
        doc = store.create("topic", "sub", "my-doc", content="hello world")
        assert doc.subject == "topic"
        assert doc.subsubject == "sub"
        assert doc.title == "my-doc"
        assert doc.content == "hello world"

        path = os.path.join(store.base, "topic", "sub", "my-doc.md")
        assert os.path.isfile(path)
        with open(path) as f:
            assert f.read() == "hello world"

    def test_slugifies_components(self, store):
        doc = store.create("My Topic", "Sub Section", "A Title", content="x")
        assert doc.subject == "my-topic"
        assert doc.subsubject == "sub-section"
        assert doc.title == "a-title"

    def test_creates_nested_directories(self, store):
        store.create("deep", "nested", "doc", content="test")
        assert os.path.isdir(os.path.join(store.base, "deep", "nested"))

    def test_tags_stored(self, store):
        doc = store.create("a", "b", "c", content="x", tags=["t1", "t2"])
        assert doc.tags == ["t1", "t2"]


class TestGet:

    def test_get_existing(self, store):
        store.create("s", "ss", "t", content="the content")
        doc = store.get("s", "ss", "t")
        assert doc is not None
        assert doc.content == "the content"
        assert doc.subject == "s"

    def test_get_missing(self, store):
        assert store.get("no", "such", "doc") is None

    def test_get_by_path(self, store):
        store.create("x", "y", "z", content="by path")
        doc = store.get_by_path("x/y/z")
        assert doc is not None
        assert doc.content == "by path"

    def test_get_by_path_invalid(self, store):
        assert store.get_by_path("only/two") is None
        assert store.get_by_path("") is None
        assert store.get_by_path("a/b/c/d") is None


class TestUpdate:

    def test_update_replaces_content(self, store):
        store.create("a", "b", "c", content="old")
        doc = store.update("a", "b", "c", content="new")
        assert doc is not None
        assert doc.content == "new"
        assert store.get("a", "b", "c").content == "new"

    def test_update_nonexistent_returns_none(self, store):
        assert store.update("no", "such", "doc", content="x") is None


class TestDelete:

    def test_delete_existing(self, store):
        store.create("a", "b", "c", content="bye")
        assert store.delete("a", "b", "c") is True
        assert store.get("a", "b", "c") is None

    def test_delete_nonexistent(self, store):
        assert store.delete("no", "such", "doc") is False


class TestAppendContent:

    def test_append_adds_content(self, store):
        store.create("a", "b", "c", content="first")
        doc = store.append_content("a", "b", "c", content="\nsecond")
        assert doc is not None
        assert "first" in doc.content
        assert "second" in doc.content

    def test_append_nonexistent_returns_none(self, store):
        assert store.append_content("no", "such", "doc", content="x") is None


class TestTree:

    def test_empty_store(self, store):
        assert store.tree() == {}

    def test_single_doc(self, store):
        store.create("topic", "sub", "doc", content="x")
        tree = store.tree()
        assert tree == {"topic": {"sub": ["doc"]}}

    def test_multiple_docs(self, store):
        store.create("a", "b", "doc1", content="x")
        store.create("a", "b", "doc2", content="y")
        store.create("a", "c", "doc3", content="z")
        tree = store.tree()
        assert set(tree["a"]["b"]) == {"doc1", "doc2"}
        assert tree["a"]["c"] == ["doc3"]

    def test_multiple_subjects(self, store):
        store.create("alpha", "sub", "d", content="x")
        store.create("beta", "sub", "d", content="y")
        tree = store.tree()
        assert "alpha" in tree
        assert "beta" in tree


class TestList:

    def test_list_all(self, store):
        store.create("a", "b", "c", content="x")
        store.create("d", "e", "f", content="y")
        docs = store.list()
        assert len(docs) == 2

    def test_list_by_subject(self, store):
        store.create("target", "s", "d", content="x")
        store.create("other", "s", "d", content="y")
        docs = store.list(subject="target")
        assert len(docs) == 1
        assert docs[0].subject == "target"

    def test_list_by_subject_and_subsubject(self, store):
        store.create("a", "b", "d1", content="x")
        store.create("a", "c", "d2", content="y")
        docs = store.list(subject="a", subsubject="b")
        assert len(docs) == 1
        assert docs[0].title == "d1"


class TestSubjects:

    def test_subjects_empty(self, store):
        assert store.subjects() == []

    def test_subjects_sorted(self, store):
        store.create("zebra", "s", "d", content="x")
        store.create("alpha", "s", "d", content="y")
        assert store.subjects() == ["alpha", "zebra"]

    def test_subsubjects(self, store):
        store.create("a", "beta", "d", content="x")
        store.create("a", "alpha", "d", content="y")
        assert store.subsubjects("a") == ["alpha", "beta"]
