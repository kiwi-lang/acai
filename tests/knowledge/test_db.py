"""Tests for acai.knowledge.db — KnowledgeDB with FTS5 search."""

from __future__ import annotations

import os
import tempfile

import pytest

from acai.knowledge.db import KnowledgeDB, _STOP_WORDS


@pytest.fixture
def db(tmp_path):
    """Fresh KnowledgeDB backed by a temp directory."""
    return KnowledgeDB(str(tmp_path / ".knowledge.db"))


@pytest.fixture
def populated_db(db):
    """DB with a few documents for search testing."""
    db.upsert("hobbies", "games", "uno-weekend",
              tags=["games", "weekend", "social"],
              facets={"personality": "hobbies", "energy": "recreation", "time": "weekend"},
              updated_at=1000.0,
              content="I enjoy playing UNO on weekends with friends and family.")
    db.upsert("cooking", "recipes", "pasta-sunday",
              tags=["cooking", "italian"],
              facets={"personality": "food", "energy": "cooking", "time": "sunday"},
              updated_at=2000.0,
              content="Making fresh pasta from scratch every Sunday afternoon.")
    db.upsert("work", "python", "async-patterns",
              tags=["python", "async", "programming"],
              facets={"personality": "python", "matter": "generators", "energy": "async-iteration"},
              updated_at=3000.0,
              content="Python async generators allow streaming data with yield in async functions.")
    return db


class TestUpsert:

    def test_insert_new_document(self, db):
        db.upsert("topic", "sub", "title", tags=["t1"], updated_at=100.0)
        result = db.get("topic/sub/title")
        assert result is not None
        assert result["path"] == "topic/sub/title"
        assert result["subject"] == "topic"
        assert result["subsubject"] == "sub"
        assert result["title"] == "title"
        assert result["tags"] == ["t1"]

    def test_update_existing_document(self, db):
        db.upsert("a", "b", "c", tags=["old"], updated_at=1.0)
        db.upsert("a", "b", "c", tags=["new"], updated_at=2.0)
        result = db.get("a/b/c")
        assert result["tags"] == ["new"]
        assert result["updated_at"] == 2.0

    def test_upsert_with_facets(self, db):
        db.upsert("x", "y", "z",
                  facets={"personality": "python", "energy": "testing"},
                  updated_at=1.0)
        result = db.get("x/y/z")
        assert result["facets"]["personality"] == "python"
        assert result["facets"]["energy"] == "testing"
        assert result["facets"]["matter"] == ""

    def test_upsert_with_content_populates_fts(self, db):
        db.upsert("a", "b", "doc", tags=["tag1"],
                  updated_at=1.0, content="searchable text here")
        results = db.fts_search("searchable")
        assert len(results) == 1
        assert results[0]["path"] == "a/b/doc"


class TestGet:

    def test_get_existing(self, db):
        db.upsert("s", "ss", "t", tags=[], updated_at=1.0)
        assert db.get("s/ss/t") is not None

    def test_get_missing(self, db):
        assert db.get("nonexistent/path/here") is None


class TestQuery:

    def test_query_all(self, populated_db):
        results = populated_db.query()
        assert len(results) == 3

    def test_query_by_subject(self, populated_db):
        results = populated_db.query(subject="hobbies")
        assert len(results) == 1
        assert results[0]["path"] == "hobbies/games/uno-weekend"

    def test_query_by_tag(self, populated_db):
        results = populated_db.query(tag="python")
        assert len(results) == 1
        assert results[0]["path"] == "work/python/async-patterns"

    def test_query_by_facet(self, populated_db):
        results = populated_db.query(personality="food")
        assert len(results) == 1
        assert results[0]["path"] == "cooking/recipes/pasta-sunday"

    def test_query_by_multiple_filters(self, populated_db):
        results = populated_db.query(subject="hobbies", tag="games")
        assert len(results) == 1

    def test_query_empty_result(self, populated_db):
        results = populated_db.query(subject="nonexistent")
        assert len(results) == 0

    def test_query_limit(self, populated_db):
        results = populated_db.query(limit=2)
        assert len(results) == 2


class TestRemove:

    def test_remove_existing(self, db):
        db.upsert("a", "b", "c", updated_at=1.0, content="hello")
        assert db.remove("a/b/c") is True
        assert db.get("a/b/c") is None
        assert db.fts_search("hello") == []

    def test_remove_nonexistent(self, db):
        assert db.remove("does/not/exist") is False


class TestFTSSearch:

    def test_basic_search(self, populated_db):
        results = populated_db.fts_search("UNO")
        assert len(results) == 1
        assert results[0]["path"] == "hobbies/games/uno-weekend"

    def test_or_mode_matches_any_term(self, populated_db):
        results = populated_db.fts_search("UNO pasta", mode="or")
        assert len(results) == 2

    def test_and_mode_requires_all_terms(self, populated_db):
        results = populated_db.fts_search("UNO pasta", mode="and")
        assert len(results) == 0

    def test_stop_words_are_removed(self, populated_db):
        results = populated_db.fts_search("What games do I like to play on weekends")
        assert len(results) >= 1
        assert results[0]["path"] == "hobbies/games/uno-weekend"

    def test_empty_query_returns_nothing(self, populated_db):
        assert populated_db.fts_search("") == []

    def test_only_stop_words_returns_nothing(self, populated_db):
        assert populated_db.fts_search("the is a an") == []

    def test_results_have_snippet(self, populated_db):
        results = populated_db.fts_search("pasta")
        assert len(results) == 1
        assert "snippet" in results[0]
        assert "pasta" in results[0]["snippet"].lower()

    def test_results_have_rank(self, populated_db):
        results = populated_db.fts_search("python async")
        assert len(results) >= 1
        assert "rank" in results[0]
        assert isinstance(results[0]["rank"], float)

    def test_limit_restricts_results(self, populated_db):
        results = populated_db.fts_search("playing making streaming", mode="or", limit=1)
        assert len(results) == 1


class TestSync:

    def test_sync_adds_new_files(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "topic" / "sub").mkdir(parents=True)
        (knowledge_dir / "topic" / "sub" / "doc.md").write_text("document content here")

        db = KnowledgeDB(str(knowledge_dir / ".knowledge.db"))
        result = db.sync(str(knowledge_dir))

        assert result["added"] == 1
        assert result["total"] == 1
        assert db.get("topic/sub/doc") is not None
        assert db.fts_search("document content") != []

    def test_sync_removes_deleted_files(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "a" / "b").mkdir(parents=True)
        doc_path = knowledge_dir / "a" / "b" / "c.md"
        doc_path.write_text("temp content")

        db = KnowledgeDB(str(knowledge_dir / ".knowledge.db"))
        db.sync(str(knowledge_dir))
        assert db.get("a/b/c") is not None

        doc_path.unlink()
        doc_path.parent.rmdir()
        result = db.sync(str(knowledge_dir))
        assert result["removed"] == 1
        assert db.get("a/b/c") is None

    def test_sync_updates_modified_files(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        (knowledge_dir / "x" / "y").mkdir(parents=True)
        doc = knowledge_dir / "x" / "y" / "z.md"
        doc.write_text("original")

        db = KnowledgeDB(str(knowledge_dir / ".knowledge.db"))
        db.sync(str(knowledge_dir))

        import time
        time.sleep(0.05)
        doc.write_text("updated content")

        result = db.sync(str(knowledge_dir))
        assert result["updated"] == 1
        hits = db.fts_search("updated")
        assert len(hits) == 1

    def test_sync_preserves_tags_on_update(self, tmp_path):
        knowledge_dir = tmp_path / "knowledge"
        (knowledge_dir / "a" / "b").mkdir(parents=True)
        doc = knowledge_dir / "a" / "b" / "c.md"
        doc.write_text("v1")

        db = KnowledgeDB(str(knowledge_dir / ".knowledge.db"))
        db.sync(str(knowledge_dir))
        db.upsert("a", "b", "c", tags=["important"], updated_at=0)

        import time
        time.sleep(0.05)
        doc.write_text("v2")
        db.sync(str(knowledge_dir))

        meta = db.get("a/b/c")
        assert meta["tags"] == ["important"]

    def test_sync_empty_directory(self, tmp_path):
        db = KnowledgeDB(str(tmp_path / ".knowledge.db"))
        result = db.sync(str(tmp_path / "nonexistent"))
        assert result == {"added": 0, "updated": 0, "removed": 0, "total": 0}


class TestStopWords:

    def test_stop_words_set_contains_common_words(self):
        for word in ["the", "is", "a", "an", "i", "you", "what", "how", "do"]:
            assert word in _STOP_WORDS

    def test_stop_words_does_not_contain_content_words(self):
        for word in ["python", "game", "cooking", "async", "weekend"]:
            assert word not in _STOP_WORDS
