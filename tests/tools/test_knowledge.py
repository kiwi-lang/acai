"""Tests for acai.tools.knowledge — CRUD tool functions."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from acai.tools import knowledge


@pytest.fixture()
def mock_client():
    client = MagicMock()
    with patch("acai.tools.knowledge.current_client", return_value=client):
        yield client


class TestCreate:
    def test_success(self, mock_client):
        mock_client.post.return_value = {"path": "a/b/c", "subject": "a"}
        result = json.loads(knowledge.create("a", "b", "c", "body"))
        assert result["path"] == "a/b/c"
        mock_client.post.assert_called_once_with("/knowledge", {
            "subject": "a", "subsubject": "b", "title": "c", "content": "body",
        })

    def test_with_tags_and_facets(self, mock_client):
        mock_client.post.return_value = {"ok": True}
        knowledge.create("s", "ss", "t", "text", tags=["x"], facets={"personality": "py"})
        call_args = mock_client.post.call_args[0]
        payload = call_args[1]
        assert payload["tags"] == ["x"]
        assert payload["facets"] == {"personality": "py"}

    def test_error(self, mock_client):
        mock_client.post.side_effect = RuntimeError("network")
        result = json.loads(knowledge.create("a", "b", "c", "body"))
        assert "error" in result

    def test_no_client(self):
        with patch("acai.tools.knowledge.current_client", return_value=None):
            result = json.loads(knowledge.create("a", "b", "c", "content"))
            assert "error" in result


class TestUpdate:
    def test_success(self, mock_client):
        mock_client.patch.return_value = {"updated": True}
        result = json.loads(knowledge.update("s", "ss", "t", "new body"))
        assert result["updated"] is True
        mock_client.patch.assert_called_once_with(
            "/knowledge/s/ss/t", {"content": "new body"},
        )

    def test_with_tags(self, mock_client):
        mock_client.patch.return_value = {}
        knowledge.update("s", "ss", "t", "body", tags=["a", "b"])
        payload = mock_client.patch.call_args[0][1]
        assert payload["tags"] == ["a", "b"]

    def test_error(self, mock_client):
        mock_client.patch.side_effect = Exception("oops")
        result = json.loads(knowledge.update("s", "ss", "t", "body"))
        assert "error" in result


class TestAppend:
    def test_success(self, mock_client):
        mock_client.post.return_value = {"appended": True}
        result = json.loads(knowledge.append("s", "ss", "t", "extra"))
        assert result["appended"] is True
        mock_client.post.assert_called_once_with(
            "/knowledge/s/ss/t/append", {"content": "extra"},
        )

    def test_error(self, mock_client):
        mock_client.post.side_effect = Exception("fail")
        result = json.loads(knowledge.append("s", "ss", "t", "x"))
        assert "error" in result


class TestGet:
    def test_success(self, mock_client):
        mock_client.get.return_value = {"content": "hello", "subject": "s"}
        result = json.loads(knowledge.get("s", "ss", "t"))
        assert result["content"] == "hello"
        mock_client.get.assert_called_once_with("/knowledge/s/ss/t")

    def test_error(self, mock_client):
        mock_client.get.side_effect = Exception("not found")
        result = json.loads(knowledge.get("s", "ss", "t"))
        assert "error" in result


class TestListDocuments:
    def test_no_filters(self, mock_client):
        mock_client.get.return_value = [{"path": "a/b/c"}]
        result = json.loads(knowledge.list_documents())
        assert len(result) == 1
        mock_client.get.assert_called_once_with("/knowledge", {})

    def test_with_subject(self, mock_client):
        mock_client.get.return_value = []
        knowledge.list_documents(subject="python")
        mock_client.get.assert_called_once_with("/knowledge", {"subject": "python"})

    def test_with_both(self, mock_client):
        mock_client.get.return_value = []
        knowledge.list_documents(subject="s", subsubject="ss")
        mock_client.get.assert_called_once_with(
            "/knowledge", {"subject": "s", "subsubject": "ss"},
        )

    def test_error(self, mock_client):
        mock_client.get.side_effect = Exception("fail")
        result = json.loads(knowledge.list_documents())
        assert "error" in result


class TestQueryDocuments:
    def test_no_filters(self, mock_client):
        mock_client.get.return_value = []
        knowledge.query_documents()
        mock_client.get.assert_called_once_with("/knowledge/query", {})

    def test_with_all_facets(self, mock_client):
        mock_client.get.return_value = []
        knowledge.query_documents(
            subject="s", subsubject="ss", tag="t",
            personality="p", matter="m", energy="e", space="sp", time="ti",
        )
        expected_params = {
            "subject": "s", "subsubject": "ss", "tag": "t",
            "personality": "p", "matter": "m", "energy": "e",
            "space": "sp", "time": "ti",
        }
        mock_client.get.assert_called_once_with("/knowledge/query", expected_params)


class TestSearch:
    def test_basic(self, mock_client):
        mock_client.get.return_value = [{"snippet": "match"}]
        result = json.loads(knowledge.search("hello"))
        assert result[0]["snippet"] == "match"
        mock_client.get.assert_called_once_with("/knowledge/search", {"q": "hello", "mode": "hybrid"})

    def test_with_filters(self, mock_client):
        mock_client.get.return_value = []
        knowledge.search("q", subject="s", subsubject="ss")
        mock_client.get.assert_called_once_with(
            "/knowledge/search", {"q": "q", "mode": "hybrid", "subject": "s", "subsubject": "ss"},
        )

    def test_error(self, mock_client):
        mock_client.get.side_effect = Exception("fail")
        result = json.loads(knowledge.search("q"))
        assert "error" in result


class TestDelete:
    def test_success(self, mock_client):
        mock_client.post.return_value = {"deleted": True}
        result = json.loads(knowledge.delete("s", "ss", "t"))
        assert result["deleted"] is True
        mock_client.post.assert_called_once_with(
            "/knowledge/s/ss/t/delete", {},
        )

    def test_error(self, mock_client):
        mock_client.post.side_effect = Exception("fail")
        result = json.loads(knowledge.delete("s", "ss", "t"))
        assert "error" in result
