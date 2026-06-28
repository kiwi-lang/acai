"""Unit tests for acai/orchestrator/routes/knowledge.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from acai.orchestrator.routes import RouterDeps
from acai.orchestrator.routes.knowledge import create_knowledge_router


@pytest.fixture
def mock_deps():
    """Create a RouterDeps with mocked knowledge and knowledge_db."""
    deps = MagicMock(spec=RouterDeps)
    deps.knowledge = MagicMock()
    deps.knowledge_db = MagicMock()
    return deps


@pytest.fixture
def client(mock_deps):
    """TestClient wired to the knowledge router."""
    app = FastAPI()
    router = create_knowledge_router(mock_deps)
    app.include_router(router)
    return TestClient(app)


class TestListKnowledge:
    def test_list_all_returns_tree(self, client, mock_deps):
        mock_deps.knowledge.tree.return_value = {"subjects": ["math", "science"]}
        resp = client.get("/knowledge")
        assert resp.status_code == 200
        assert resp.json() == {"subjects": ["math", "science"]}

    def test_list_by_subject(self, client, mock_deps):
        doc = MagicMock()
        doc.summary.return_value = {"title": "intro", "subject": "math"}
        mock_deps.knowledge.list.return_value = [doc]

        resp = client.get("/knowledge?subject=math")
        assert resp.status_code == 200
        assert resp.json() == [{"title": "intro", "subject": "math"}]
        mock_deps.knowledge.list.assert_called_once_with(subject="math", subsubject="")


class TestSearchKnowledge:
    def test_missing_query(self, client):
        resp = client.get("/knowledge/search")
        assert resp.status_code == 400
        assert "q parameter" in resp.json()["error"]

    def test_fts_search(self, client, mock_deps):
        mock_deps.knowledge_db.fts_search.return_value = [
            {"path": "math/algebra/intro", "snippet": "...algebra...", "rank": 1.0}
        ]
        doc = MagicMock()
        doc.subject = "math"
        doc.subsubject = "algebra"
        doc.to_dict.return_value = {"title": "intro", "content": "algebra basics"}
        mock_deps.knowledge.get_by_path.return_value = doc

        resp = client.get("/knowledge/search?q=algebra&mode=fts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["snippet"] == "...algebra..."

    def test_fts_no_results_falls_back(self, client, mock_deps):
        mock_deps.knowledge_db.fts_search.return_value = []
        doc = MagicMock()
        doc.to_dict.return_value = {"title": "x"}
        mock_deps.knowledge.search.return_value = [doc]

        resp = client.get("/knowledge/search?q=test&mode=fts")
        assert resp.status_code == 200
        mock_deps.knowledge.search.assert_called_once()

    def test_non_fts_mode(self, client, mock_deps):
        doc = MagicMock()
        doc.to_dict.return_value = {"title": "result"}
        mock_deps.knowledge.search.return_value = [doc]

        resp = client.get("/knowledge/search?q=test&mode=keyword")
        assert resp.status_code == 200
        assert resp.json() == [{"title": "result"}]


class TestQueryKnowledge:
    def test_query(self, client, mock_deps):
        mock_deps.knowledge_db.query.return_value = [{"title": "doc1"}]
        resp = client.get("/knowledge/query?subject=math&tag=important")
        assert resp.status_code == 200
        mock_deps.knowledge_db.query.assert_called_once()


class TestListTags:
    def test_returns_tags(self, client, mock_deps):
        mock_deps.knowledge_db.list_tags.return_value = ["math", "physics"]
        resp = client.get("/knowledge/tags")
        assert resp.status_code == 200
        assert resp.json() == ["math", "physics"]


class TestListFacetValues:
    def test_valid_facet(self, client, mock_deps):
        mock_deps.knowledge_db.list_facet_values.return_value = ["high", "low"]
        resp = client.get("/knowledge/facets/energy")
        assert resp.status_code == 200
        assert resp.json() == ["high", "low"]

    def test_invalid_facet(self, client, mock_deps):
        mock_deps.knowledge_db.list_facet_values.side_effect = ValueError("unknown facet: foo")
        resp = client.get("/knowledge/facets/foo")
        assert resp.status_code == 400
        assert "unknown facet" in resp.json()["error"]


class TestCreateKnowledge:
    def test_create_success(self, client, mock_deps):
        doc = MagicMock()
        doc.subject = "math"
        doc.subsubject = "algebra"
        doc.title = "intro"
        doc.path = "math/algebra/intro"
        doc.content = "hello"
        doc.updated_at = "2024-01-01"
        doc.to_dict.return_value = {"title": "intro", "subject": "math"}
        mock_deps.knowledge.create.return_value = doc

        resp = client.post("/knowledge", json={
            "subject": "math",
            "subsubject": "algebra",
            "title": "intro",
            "content": "hello",
            "tags": ["basics"],
        })
        assert resp.status_code == 201
        mock_deps.knowledge.create.assert_called_once()

    def test_create_missing_fields(self, client):
        resp = client.post("/knowledge", json={"subject": "math"})
        assert resp.status_code == 400
        assert "required" in resp.json()["error"]


class TestGetKnowledge:
    def test_found(self, client, mock_deps):
        doc = MagicMock()
        doc.path = "math/algebra/intro"
        doc.to_dict.return_value = {"title": "intro"}
        mock_deps.knowledge.get.return_value = doc
        mock_deps.knowledge_db.get.return_value = {"tags": ["x"], "facets": {}}

        resp = client.get("/knowledge/math/algebra/intro")
        assert resp.status_code == 200

    def test_not_found(self, client, mock_deps):
        mock_deps.knowledge.get.return_value = None
        resp = client.get("/knowledge/math/algebra/missing")
        assert resp.status_code == 404


class TestUpdateKnowledge:
    def test_update_success(self, client, mock_deps):
        doc = MagicMock()
        doc.subject = "math"
        doc.subsubject = "algebra"
        doc.title = "intro"
        doc.path = "math/algebra/intro"
        doc.content = "updated"
        doc.updated_at = "2024-01-02"
        doc.to_dict.return_value = {"title": "intro", "content": "updated"}
        mock_deps.knowledge.update.return_value = doc
        mock_deps.knowledge_db.get.return_value = {"tags": [], "facets": {}}

        resp = client.patch("/knowledge/math/algebra/intro", json={"content": "updated"})
        assert resp.status_code == 200

    def test_update_no_content(self, client):
        resp = client.patch("/knowledge/math/algebra/intro", json={})
        assert resp.status_code == 400

    def test_update_not_found(self, client, mock_deps):
        mock_deps.knowledge.update.return_value = None
        resp = client.patch("/knowledge/math/algebra/intro", json={"content": "x"})
        assert resp.status_code == 404


class TestAppendKnowledge:
    def test_append_success(self, client, mock_deps):
        doc = MagicMock()
        doc.subject = "math"
        doc.subsubject = "algebra"
        doc.title = "intro"
        doc.path = "math/algebra/intro"
        doc.content = "old + new"
        doc.updated_at = "2024-01-02"
        doc.to_dict.return_value = {"content": "old + new"}
        mock_deps.knowledge.append_content.return_value = doc
        mock_deps.knowledge_db.get.return_value = {"tags": [], "facets": {}}

        resp = client.post("/knowledge/math/algebra/intro/append", json={"content": "new"})
        assert resp.status_code == 200

    def test_append_no_content(self, client):
        resp = client.post("/knowledge/math/algebra/intro/append", json={})
        assert resp.status_code == 400

    def test_append_not_found(self, client, mock_deps):
        mock_deps.knowledge.append_content.return_value = None
        resp = client.post("/knowledge/math/algebra/intro/append", json={"content": "x"})
        assert resp.status_code == 404


class TestDeleteKnowledge:
    def test_delete_success(self, client, mock_deps):
        mock_deps.knowledge.delete.return_value = True
        resp = client.post("/knowledge/math/algebra/intro/delete")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_not_found(self, client, mock_deps):
        mock_deps.knowledge.delete.return_value = False
        resp = client.post("/knowledge/math/algebra/intro/delete")
        assert resp.status_code == 404


# ==========================================================================
# Vector/semantic search endpoints
# ==========================================================================


class TestSearchModeVector:
    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_vector_mode_no_endpoint(self, mock_get_vs, client, mock_deps):
        """mode=vector with no embedding endpoint returns 503."""
        mock_get_vs.return_value = None
        resp = client.get("/knowledge/search?q=hello&mode=vector")
        assert resp.status_code == 503

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_semantic_mode_alias(self, mock_get_vs, client, mock_deps):
        """mode=semantic is treated same as mode=vector."""
        mock_get_vs.return_value = None
        resp = client.get("/knowledge/search?q=hello&mode=semantic")
        assert resp.status_code == 503

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_hybrid_mode_fallback_to_fts(self, mock_get_vs, client, mock_deps):
        """mode=hybrid with no vector endpoint falls back to FTS."""
        mock_get_vs.return_value = None
        mock_deps.knowledge_db.fts_search.return_value = []

        resp = client.get("/knowledge/search?q=hello&mode=hybrid")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_hybrid_mode_fts_with_docs(self, mock_get_vs, client, mock_deps):
        """mode=hybrid without vector returns FTS doc content."""
        mock_get_vs.return_value = None

        doc = MagicMock()
        doc.to_dict.return_value = {"path": "a/b/c", "content": "hello"}
        mock_deps.knowledge.get_by_path.return_value = doc
        mock_deps.knowledge_db.fts_search.return_value = [
            {"path": "a/b/c", "snippet": "hello...", "rank": 1}
        ]

        resp = client.get("/knowledge/search?q=hello&mode=hybrid")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["path"] == "a/b/c"

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_vector_mode_returns_results(self, mock_get_vs, client, mock_deps):
        """mode=vector with working endpoint returns vector results."""
        from acai.knowledge.vectors import VectorHit
        mock_vs = MagicMock()
        mock_vs.search.return_value = [
            VectorHit(path="x/y/z", chunk_index=0, chunk_text="found it", score=0.95, metadata={})
        ]
        mock_get_vs.return_value = mock_vs

        resp = client.get("/knowledge/search?q=hello&mode=vector")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["path"] == "x/y/z"
        assert data[0]["score"] == 0.95

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_hybrid_mode_with_vector(self, mock_get_vs, client, mock_deps):
        """mode=hybrid with working vector combines results."""
        from acai.knowledge.vectors import VectorHit
        mock_vs = MagicMock()
        mock_vs.hybrid_search.return_value = [
            VectorHit(path="a/b/c", chunk_index=0, chunk_text="combined", score=0.8, metadata={})
        ]
        mock_get_vs.return_value = mock_vs
        mock_deps.knowledge_db.fts_search.return_value = [
            {"path": "a/b/c", "snippet": "test"}
        ]

        resp = client.get("/knowledge/search?q=hello&mode=hybrid")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["score"] == 0.8


class TestVectorSync:
    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_sync_no_endpoint(self, mock_get_vs, client, mock_deps):
        mock_get_vs.return_value = None
        resp = client.post("/knowledge/vectors/sync")
        assert resp.status_code == 503

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_sync_success(self, mock_get_vs, client, mock_deps):
        mock_vs = MagicMock()
        mock_vs.sync.return_value = {"indexed": 5, "skipped": 0, "removed": 1, "errors": 0}
        mock_get_vs.return_value = mock_vs

        resp = client.post("/knowledge/vectors/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["indexed"] == 5
        assert data["removed"] == 1


class TestVectorStats:
    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_stats_no_endpoint(self, mock_get_vs, client, mock_deps):
        mock_get_vs.return_value = None
        resp = client.get("/knowledge/vectors/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["embedding_available"] is False

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_stats_with_endpoint(self, mock_get_vs, client, mock_deps):
        mock_vs = MagicMock()
        mock_vs.stats.return_value = {
            "total_chunks": 42,
            "total_documents": 10,
            "embedding_available": True,
        }
        mock_get_vs.return_value = mock_vs

        resp = client.get("/knowledge/vectors/stats")
        assert resp.status_code == 200
        assert resp.json()["total_chunks"] == 42


class TestVectorIndexSingle:
    def test_index_no_path(self, client, mock_deps):
        resp = client.post("/knowledge/vectors/index", json={})
        assert resp.status_code == 400

    def test_index_doc_not_found(self, client, mock_deps):
        mock_deps.knowledge.get_by_path.return_value = None
        resp = client.post("/knowledge/vectors/index", json={"path": "x/y/z"})
        assert resp.status_code == 404

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_index_no_endpoint(self, mock_get_vs, client, mock_deps):
        doc = MagicMock()
        doc.path = "a/b/c"
        doc.content = "content"
        mock_deps.knowledge.get_by_path.return_value = doc
        mock_get_vs.return_value = None

        resp = client.post("/knowledge/vectors/index", json={"path": "a/b/c"})
        assert resp.status_code == 503

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_index_success(self, mock_get_vs, client, mock_deps):
        doc = MagicMock()
        doc.path = "a/b/c"
        doc.content = "content"
        doc.subject = "a"
        doc.subsubject = "b"
        doc.title = "c"
        doc.tags = ["tag1"]
        mock_deps.knowledge.get_by_path.return_value = doc

        mock_vs = MagicMock()
        mock_vs.index_document.return_value = 3
        mock_get_vs.return_value = mock_vs

        resp = client.post("/knowledge/vectors/index", json={"path": "a/b/c"})
        assert resp.status_code == 200
        assert resp.json()["chunks_indexed"] == 3

    @patch("acai.orchestrator.routes.knowledge._get_vector_store")
    def test_index_embedding_error(self, mock_get_vs, client, mock_deps):
        from acai.knowledge.vectors import EmbeddingError

        doc = MagicMock()
        doc.path = "a/b/c"
        doc.content = "content"
        doc.subject = "a"
        doc.subsubject = "b"
        doc.title = "c"
        doc.tags = []
        mock_deps.knowledge.get_by_path.return_value = doc

        mock_vs = MagicMock()
        mock_vs.index_document.side_effect = EmbeddingError("server crashed")
        mock_get_vs.return_value = mock_vs

        resp = client.post("/knowledge/vectors/index", json={"path": "a/b/c"})
        assert resp.status_code == 500
        assert "server crashed" in resp.json()["error"]


class TestSearchMissingQuery:
    def test_search_no_q_param(self, client, mock_deps):
        resp = client.get("/knowledge/search")
        assert resp.status_code == 400
        assert "q parameter is required" in resp.json()["error"]
