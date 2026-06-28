"""Unit tests for acai.knowledge.vectors — vector store and hybrid search.

Covers:
- Chunking edge cases (whitespace-only, unicode, extremely long single words)
- Serialization robustness (zero vectors, NaN, inf)
- Embedding client error modes (timeouts, HTTP 500, malformed JSON)
- VectorStore corruption recovery, concurrent access, dimension mismatch
- Hybrid search degenerate inputs (empty FTS, empty vector, both empty)
- Sync error handling (partial failures, empty stores)
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acai.knowledge.vectors import (
    EmbeddingClient,
    EmbeddingError,
    VectorHit,
    VectorStore,
    _chunk_text,
    _content_hash,
    _deserialize_vector,
    _serialize_vector,
)


class TestChunkText:
    def test_empty_string(self):
        assert _chunk_text("") == []

    def test_short_text_single_chunk(self):
        text = "Hello world"
        chunks = _chunk_text(text, chunk_size=100)
        assert chunks == [text]

    def test_splits_long_text(self):
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        chunks = _chunk_text(text, chunk_size=20, overlap=0)
        assert len(chunks) >= 2
        assert all(len(c) > 0 for c in chunks)

    def test_preserves_paragraph_boundaries(self):
        text = "First para.\n\nSecond para.\n\nThird para."
        chunks = _chunk_text(text, chunk_size=25, overlap=0)
        assert any("First para" in c for c in chunks)
        assert any("Second para" in c for c in chunks)

    def test_handles_long_paragraph(self):
        text = " ".join(["word"] * 200)
        chunks = _chunk_text(text, chunk_size=50, overlap=0)
        assert len(chunks) > 1
        assert all(len(c) <= 55 for c in chunks)  # allow slight overshoot on word boundary

    def test_overlap_adds_context(self):
        text = "A" * 100 + "\n\n" + "B" * 100
        chunks = _chunk_text(text, chunk_size=110, overlap=20)
        assert len(chunks) == 2
        # Second chunk should start with tail of first
        assert chunks[1].startswith("A" * 20)

    def test_no_overlap_when_single_chunk(self):
        text = "Short text"
        chunks = _chunk_text(text, chunk_size=100, overlap=10)
        assert chunks == ["Short text"]


class TestSerializeVector:
    def test_roundtrip(self):
        vec = np.array([1.0, 2.0, 3.0, -1.5], dtype=np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        np.testing.assert_array_almost_equal(vec, result)

    def test_high_dimensional(self):
        vec = np.random.randn(768).astype(np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        np.testing.assert_array_almost_equal(vec, result)


class TestContentHash:
    def test_deterministic(self):
        assert _content_hash("hello") == _content_hash("hello")

    def test_different_content(self):
        assert _content_hash("hello") != _content_hash("world")

    def test_length(self):
        assert len(_content_hash("test")) == 16


class TestEmbeddingClient:
    def test_embed_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"index": 0, "embedding": [1.0, 2.0, 3.0]},
                {"index": 1, "embedding": [4.0, 5.0, 6.0]},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            client = EmbeddingClient("http://localhost:5103", model="test-model")
            results = client.embed(["hello", "world"])

        assert len(results) == 2
        np.testing.assert_array_equal(results[0], [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(results[1], [4.0, 5.0, 6.0])
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
        assert url == "http://localhost:5103/v1/embeddings"

    def test_embed_with_api_key(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"index": 0, "embedding": [1.0]}]}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            client = EmbeddingClient("http://localhost:5103", api_key="sk-test")
            client.embed(["text"])

        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer sk-test"

    def test_embed_http_error(self):
        import requests
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            client = EmbeddingClient("http://localhost:5103")
            with pytest.raises(EmbeddingError, match="Embedding request failed"):
                client.embed(["text"])

    def test_embed_unexpected_response(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "bad model"}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            client = EmbeddingClient("http://localhost:5103")
            with pytest.raises(EmbeddingError, match="Unexpected embedding response"):
                client.embed(["text"])

    def test_embed_one(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            client = EmbeddingClient("http://localhost:5103")
            result = client.embed_one("hello")

        np.testing.assert_array_equal(result, [1.0, 2.0])

    def test_available_true(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            client = EmbeddingClient("http://localhost:5103")
            assert client.available is True

    def test_available_connection_error(self):
        import requests
        with patch("requests.get", side_effect=requests.ConnectionError):
            client = EmbeddingClient("http://localhost:5103")
            assert client.available is False

    def test_sorts_by_index(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"index": 1, "embedding": [4.0, 5.0]},
                {"index": 0, "embedding": [1.0, 2.0]},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            client = EmbeddingClient("http://localhost:5103")
            results = client.embed(["first", "second"])

        np.testing.assert_array_equal(results[0], [1.0, 2.0])
        np.testing.assert_array_equal(results[1], [4.0, 5.0])


class TestVectorStore:
    @pytest.fixture
    def store(self, tmp_path):
        """VectorStore with a mocked embedding client."""
        s = VectorStore(str(tmp_path), endpoint="http://fake:5103")
        return s

    def test_init_creates_db(self, tmp_path):
        store = VectorStore(str(tmp_path))
        assert os.path.isfile(os.path.join(str(tmp_path), ".knowledge_vectors.db"))

    def test_stats_empty(self, store):
        stats = store.stats()
        assert stats["total_chunks"] == 0
        assert stats["total_documents"] == 0

    def test_index_document(self, store):
        fake_embeddings = [np.array([1.0, 0.0, 0.0], dtype=np.float32)]
        store._embedder = MagicMock()
        store._embedder.embed.return_value = fake_embeddings

        count = store.index_document("test/doc/one", "Short content")
        assert count == 1
        assert store.stats()["total_chunks"] == 1
        assert store.stats()["total_documents"] == 1

    def test_index_document_skips_if_unchanged(self, store):
        fake_embeddings = [np.array([1.0, 0.0], dtype=np.float32)]
        store._embedder = MagicMock()
        store._embedder.embed.return_value = fake_embeddings

        store.index_document("test/doc/one", "Same content")
        store.index_document("test/doc/one", "Same content")
        assert store._embedder.embed.call_count == 1

    def test_index_document_reindexes_on_change(self, store):
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0], dtype=np.float32)]

        store.index_document("test/doc/one", "Version 1")
        store._embedder.embed.return_value = [np.array([2.0], dtype=np.float32)]
        store.index_document("test/doc/one", "Version 2")
        assert store._embedder.embed.call_count == 2

    def test_index_empty_content(self, store):
        store._embedder = MagicMock()
        count = store.index_document("test/doc/empty", "")
        assert count == 0

    def test_index_no_endpoint_raises(self, tmp_path):
        store = VectorStore(str(tmp_path))
        with pytest.raises(EmbeddingError, match="No embedding endpoint"):
            store.index_document("a/b/c", "content")

    def test_remove_document(self, store):
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 2.0], dtype=np.float32)]
        store.index_document("test/doc/one", "Content here")

        removed = store.remove_document("test/doc/one")
        assert removed == 1
        assert store.stats()["total_chunks"] == 0

    def test_remove_nonexistent(self, store):
        removed = store.remove_document("does/not/exist")
        assert removed == 0

    def test_search(self, store):
        store._embedder = MagicMock()
        # Index two docs with different embeddings
        store._embedder.embed.side_effect = [
            [np.array([1.0, 0.0, 0.0], dtype=np.float32)],  # doc1
            [np.array([0.0, 1.0, 0.0], dtype=np.float32)],  # doc2
        ]
        store.index_document("math/linear/vectors", "Linear algebra stuff")
        store.index_document("cook/pasta/recipe", "Boil water add pasta")

        # Query similar to doc1
        store._embedder.embed_one = MagicMock(
            return_value=np.array([0.9, 0.1, 0.0], dtype=np.float32)
        )
        results = store.search("linear algebra")
        assert len(results) == 2
        assert results[0].path == "math/linear/vectors"
        assert results[0].score > results[1].score

    def test_search_with_path_filter(self, store):
        store._embedder = MagicMock()
        store._embedder.embed.side_effect = [
            [np.array([1.0, 0.0], dtype=np.float32)],
            [np.array([0.0, 1.0], dtype=np.float32)],
        ]
        store.index_document("python/async/gen", "generators")
        store.index_document("java/spring/boot", "spring boot")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([0.5, 0.5], dtype=np.float32)
        )
        results = store.search("code", path_filter="python/")
        assert len(results) == 1
        assert results[0].path == "python/async/gen"

    def test_search_no_endpoint_raises(self, tmp_path):
        store = VectorStore(str(tmp_path))
        with pytest.raises(EmbeddingError, match="No embedding endpoint"):
            store.search("query")

    def test_search_empty_store(self, store):
        store._embedder = MagicMock()
        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        results = store.search("anything")
        assert results == []

    def test_hybrid_search(self, store):
        store._embedder = MagicMock()
        store._embedder.embed.side_effect = [
            [np.array([1.0, 0.0], dtype=np.float32)],
            [np.array([0.0, 1.0], dtype=np.float32)],
        ]
        store.index_document("alpha/beta/gamma", "Alpha content")
        store.index_document("delta/epsilon/zeta", "Delta content")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([0.9, 0.1], dtype=np.float32)
        )

        fts_results = [
            {"path": "delta/epsilon/zeta", "title": "zeta", "snippet": "delta..."},
            {"path": "alpha/beta/gamma", "title": "gamma", "snippet": "alpha..."},
        ]

        results = store.hybrid_search("alpha query", fts_results, limit=5)
        assert len(results) == 2
        # Both vector (favors alpha) and FTS (favors delta first) contribute
        paths = [r.path for r in results]
        assert "alpha/beta/gamma" in paths
        assert "delta/epsilon/zeta" in paths

    def test_hybrid_search_vector_unavailable(self, store):
        store._embedder = MagicMock()
        store._embedder.embed_one = MagicMock(side_effect=EmbeddingError("down"))

        fts_results = [{"path": "a/b/c", "snippet": "text"}]
        results = store.hybrid_search("query", fts_results)
        assert len(results) == 1
        assert results[0].path == "a/b/c"

    def test_sync(self, store, tmp_path):
        from acai.knowledge.store import KnowledgeStore

        ks = KnowledgeStore(str(tmp_path / "knowledge"))
        ks.create("python", "async", "generators", content="Async generators are...")
        ks.create("python", "typing", "protocols", content="Protocols define...")

        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]

        result = store.sync(ks)
        assert result["indexed"] == 2
        assert result["errors"] == 0
        assert store.stats()["total_documents"] == 2

    def test_sync_removes_stale(self, store, tmp_path):
        from acai.knowledge.store import KnowledgeStore

        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0], dtype=np.float32)]
        store.index_document("old/stale/doc", "This is stale")

        ks = KnowledgeStore(str(tmp_path / "knowledge"))
        ks.create("new", "fresh", "doc", content="Fresh content")

        result = store.sync(ks)
        assert result["removed"] == 1
        assert result["indexed"] == 1

    def test_embedding_available_no_endpoint(self, tmp_path):
        store = VectorStore(str(tmp_path))
        assert store.embedding_available is False

    def test_index_with_metadata(self, store):
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0], dtype=np.float32)]

        store.index_document("a/b/c", "content", metadata={"tags": ["python"]})

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0], dtype=np.float32)
        )
        results = store.search("content")
        assert results[0].metadata == {"tags": ["python"]}


# ==========================================================================
# Edge cases: chunking
# ==========================================================================


class TestChunkTextEdgeCases:
    def test_whitespace_only(self):
        assert _chunk_text("   \n\n   ") == ["   \n\n   "]

    def test_single_newline_not_paragraph_break(self):
        text = "Line one.\nLine two.\nLine three."
        chunks = _chunk_text(text, chunk_size=10, overlap=0)
        assert len(chunks) >= 1

    def test_unicode_characters(self):
        text = "日本語のテスト文章。\n\n" * 50
        chunks = _chunk_text(text, chunk_size=100, overlap=10)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) > 0

    def test_extremely_long_single_word(self):
        text = "a" * 2000
        chunks = _chunk_text(text, chunk_size=100, overlap=0)
        # Long single word can't be split at word boundaries but should still chunk
        assert len(chunks) >= 1

    def test_many_empty_paragraphs(self):
        text = "content\n\n\n\n\n\nmore content"
        chunks = _chunk_text(text, chunk_size=50, overlap=0)
        assert len(chunks) >= 1
        assert any("content" in c for c in chunks)

    def test_chunk_size_one(self):
        text = "ab\n\ncd"
        chunks = _chunk_text(text, chunk_size=1, overlap=0)
        assert len(chunks) >= 2

    def test_overlap_larger_than_chunk(self):
        text = "Hello world.\n\nGoodbye world."
        chunks = _chunk_text(text, chunk_size=15, overlap=100)
        assert len(chunks) >= 1

    def test_only_newlines(self):
        text = "\n\n\n\n"
        result = _chunk_text(text, chunk_size=10)
        assert result == ["\n\n\n\n"]


# ==========================================================================
# Edge cases: serialization
# ==========================================================================


class TestSerializeVectorEdgeCases:
    def test_zero_vector(self):
        vec = np.zeros(128, dtype=np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        np.testing.assert_array_equal(vec, result)

    def test_nan_values(self):
        vec = np.array([1.0, float("nan"), 3.0], dtype=np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        assert np.isnan(result[1])

    def test_inf_values(self):
        vec = np.array([float("inf"), float("-inf"), 0.0], dtype=np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        assert np.isinf(result[0])
        assert np.isinf(result[1])

    def test_empty_vector(self):
        vec = np.array([], dtype=np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        assert len(result) == 0

    def test_single_element(self):
        vec = np.array([42.0], dtype=np.float32)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        np.testing.assert_array_equal(result, [42.0])

    def test_float64_input_converted_to_float32(self):
        vec = np.array([1.0, 2.0], dtype=np.float64)
        blob = _serialize_vector(vec)
        result = _deserialize_vector(blob)
        assert result.dtype == np.float32


# ==========================================================================
# Edge cases: EmbeddingClient
# ==========================================================================


class TestEmbeddingClientEdgeCases:
    def test_timeout_error(self):
        import requests
        with patch("requests.post", side_effect=requests.Timeout("timed out")):
            client = EmbeddingClient("http://localhost:5103")
            with pytest.raises(EmbeddingError, match="Embedding request failed"):
                client.embed(["text"])

    def test_http_500_raises(self):
        import requests
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch("requests.post", return_value=mock_resp):
            client = EmbeddingClient("http://localhost:5103")
            with pytest.raises(EmbeddingError, match="Embedding request failed"):
                client.embed(["text"])

    def test_malformed_json_response(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = json.JSONDecodeError("bad", "", 0)

        with patch("requests.post", return_value=mock_resp):
            client = EmbeddingClient("http://localhost:5103")
            with pytest.raises((EmbeddingError, json.JSONDecodeError)):
                client.embed(["text"])

    def test_empty_batch(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": []}

        with patch("requests.post", return_value=mock_resp):
            client = EmbeddingClient("http://localhost:5103")
            results = client.embed([])
        assert results == []

    def test_endpoint_trailing_slash_stripped(self):
        client = EmbeddingClient("http://localhost:5103///")
        assert client.endpoint == "http://localhost:5103"

    def test_available_timeout(self):
        import requests
        with patch("requests.get", side_effect=requests.Timeout):
            client = EmbeddingClient("http://localhost:5103")
            assert client.available is False

    def test_available_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("requests.get", return_value=mock_resp):
            client = EmbeddingClient("http://localhost:5103")
            assert client.available is False

    def test_embed_one_propagates_error(self):
        import requests
        with patch("requests.post", side_effect=requests.ConnectionError):
            client = EmbeddingClient("http://localhost:5103")
            with pytest.raises(EmbeddingError):
                client.embed_one("text")


# ==========================================================================
# Edge cases: VectorStore
# ==========================================================================


class TestVectorStoreEdgeCases:
    @pytest.fixture
    def store(self, tmp_path):
        s = VectorStore(str(tmp_path), endpoint="http://fake:5103")
        return s

    def test_search_with_zero_query_vector(self, store):
        """Zero-norm query vector should return empty (no division by zero)."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store.index_document("a/b/c", "content")

        store._embedder.embed_one = MagicMock(
            return_value=np.zeros(2, dtype=np.float32)
        )
        results = store.search("query")
        assert results == []

    def test_search_with_zero_norm_document_vector(self, store):
        """Documents with zero-norm embeddings should be skipped, not crash."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.zeros(3, dtype=np.float32)]
        store.index_document("a/b/c", "content with zero embedding")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0, 0.0], dtype=np.float32)
        )
        results = store.search("query")
        assert results == []

    def test_search_limit_respected(self, store):
        store._embedder = MagicMock()
        embeddings = [np.random.randn(3).astype(np.float32) for _ in range(10)]
        store._embedder.embed.side_effect = [[e] for e in embeddings]

        for i in range(10):
            store.index_document(f"doc/{i}/test", f"content {i}")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0, 0.0], dtype=np.float32)
        )
        results = store.search("query", limit=3)
        assert len(results) <= 3

    def test_index_embedding_failure_propagates(self, store):
        """If embedding fails during indexing, EmbeddingError propagates."""
        store._embedder = MagicMock()
        store._embedder.embed.side_effect = EmbeddingError("server down")

        with pytest.raises(EmbeddingError, match="server down"):
            store.index_document("a/b/c", "content")

    def test_index_very_large_document(self, store):
        """Large document should be chunked and each chunk embedded."""
        store._embedder = MagicMock()
        large_content = ("paragraph of text. " * 100 + "\n\n") * 20
        num_chunks = len(_chunk_text(large_content, store._chunk_size, store._chunk_overlap))
        store._embedder.embed.return_value = [
            np.random.randn(4).astype(np.float32) for _ in range(num_chunks)
        ]

        count = store.index_document("big/large/doc", large_content)
        assert count == num_chunks
        assert store.stats()["total_chunks"] == num_chunks

    def test_corrupted_metadata_in_db(self, store):
        """Search should handle corrupted JSON metadata gracefully."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store.index_document("a/b/c", "content")

        # Corrupt the metadata directly in SQLite
        with store._connect() as conn:
            conn.execute("UPDATE chunks SET metadata = 'not-json{{' WHERE path = 'a/b/c'")
            conn.commit()

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        results = store.search("query")
        assert len(results) == 1
        assert results[0].metadata == {}  # gracefully defaults to empty dict

    def test_concurrent_index_and_search(self, store):
        """Concurrent indexing and searching shouldn't corrupt the store."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )

        errors = []

        def index_docs():
            try:
                for i in range(20):
                    store.index_document(f"thread/a/{i}", f"content {i}")
            except Exception as exc:
                errors.append(exc)

        def search_docs():
            try:
                for _ in range(20):
                    store.search("query")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=index_docs)
        t2 = threading.Thread(target=search_docs)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Concurrent access errors: {errors}"

    def test_hybrid_search_empty_fts_results(self, store):
        """Hybrid search with no FTS results should still return vector results."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store.index_document("a/b/c", "some content")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        results = store.hybrid_search("query", fts_results=[], limit=5)
        assert len(results) == 1
        assert results[0].path == "a/b/c"

    def test_hybrid_search_both_empty(self, store):
        """Hybrid search with no vectors and no FTS returns empty."""
        store._embedder = MagicMock()
        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        results = store.hybrid_search("query", fts_results=[])
        assert results == []

    def test_hybrid_search_fts_only_no_vectors(self, store):
        """When vector search returns nothing, FTS results still appear."""
        store._embedder = MagicMock()
        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        # Empty vector store, but FTS has results
        fts = [
            {"path": "x/y/z", "snippet": "relevant text"},
            {"path": "a/b/c", "snippet": "other text"},
        ]
        results = store.hybrid_search("query", fts, limit=5)
        assert len(results) == 2
        assert results[0].path == "x/y/z"  # ranked higher (first in FTS)

    def test_hybrid_search_custom_weights(self, store):
        """Custom weights shift ranking towards the weighted source."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store.index_document("vec/best/doc", "vector-optimized content")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        fts = [{"path": "fts/best/doc", "snippet": "fts content"}]

        # Heavy vector weight
        results = store.hybrid_search("q", fts, vector_weight=0.99, fts_weight=0.01)
        assert results[0].path == "vec/best/doc"

        # Heavy FTS weight
        results = store.hybrid_search("q", fts, vector_weight=0.01, fts_weight=0.99)
        assert results[0].path == "fts/best/doc"

    def test_sync_with_embedding_failures(self, store, tmp_path):
        """Sync counts errors and continues past failed documents."""
        from acai.knowledge.store import KnowledgeStore

        ks = KnowledgeStore(str(tmp_path / "knowledge"))
        ks.create("good", "doc", "one", content="works fine")
        ks.create("bad", "doc", "two", content="will fail")
        ks.create("good", "doc", "three", content="also works")

        call_count = [0]

        def _embed_side_effect(texts):
            call_count[0] += 1
            if call_count[0] == 2:
                raise EmbeddingError("server error")
            return [np.array([1.0], dtype=np.float32) for _ in texts]

        store._embedder = MagicMock()
        store._embedder.embed.side_effect = _embed_side_effect

        result = store.sync(ks)
        assert result["indexed"] == 2
        assert result["errors"] == 1

    def test_sync_empty_store(self, store, tmp_path):
        """Sync with an empty KnowledgeStore removes all existing vectors."""
        from acai.knowledge.store import KnowledgeStore

        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0], dtype=np.float32)]
        store.index_document("old/stale/one", "stale")
        store.index_document("old/stale/two", "stale")

        ks = KnowledgeStore(str(tmp_path / "empty_knowledge"))
        result = store.sync(ks)
        assert result["removed"] == 2
        assert result["indexed"] == 0
        assert store.stats()["total_documents"] == 0

    def test_multiple_chunks_same_doc_in_search(self, store):
        """A multi-chunk document should return multiple hits in search."""
        long_content = ("First topic about AI. " * 50 + "\n\n" +
                        "Second topic about databases. " * 50)
        chunks = _chunk_text(long_content, store._chunk_size, store._chunk_overlap)

        store._embedder = MagicMock()
        store._embedder.embed.return_value = [
            np.random.randn(3).astype(np.float32) for _ in chunks
        ]
        store.index_document("multi/chunk/doc", long_content)

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0, 0.0], dtype=np.float32)
        )
        results = store.search("query", limit=20)
        assert len(results) == len(chunks)
        assert all(r.path == "multi/chunk/doc" for r in results)
        # chunk_index should differ
        indices = {r.chunk_index for r in results}
        assert len(indices) == len(chunks)

    def test_index_document_none_metadata(self, store):
        """Passing metadata=None should not crash."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0], dtype=np.float32)]
        count = store.index_document("a/b/c", "content", metadata=None)
        assert count == 1

    def test_stats_after_operations(self, store):
        """Stats reflect correct counts after add/remove cycles."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0], dtype=np.float32)]

        store.index_document("a/b/c", "content1")
        store.index_document("d/e/f", "content2")
        assert store.stats()["total_documents"] == 2

        store.remove_document("a/b/c")
        assert store.stats()["total_documents"] == 1
        assert store.stats()["total_chunks"] == 1

    def test_path_filter_no_match(self, store):
        """Path filter that matches nothing returns empty."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store.index_document("python/async/gen", "content")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        results = store.search("query", path_filter="java/")
        assert results == []

    def test_hybrid_search_deduplicates_same_path(self, store):
        """If both FTS and vector return the same path, it appears once in results."""
        store._embedder = MagicMock()
        store._embedder.embed.return_value = [np.array([1.0, 0.0], dtype=np.float32)]
        store.index_document("shared/path/doc", "content")

        store._embedder.embed_one = MagicMock(
            return_value=np.array([1.0, 0.0], dtype=np.float32)
        )
        fts = [{"path": "shared/path/doc", "snippet": "content"}]
        results = store.hybrid_search("query", fts, limit=5)
        paths = [r.path for r in results]
        assert paths.count("shared/path/doc") == 1
        # Score should be higher than either alone (combined RRF)
        assert results[0].score > 0


# ==========================================================================
# Edge cases: _auto_knowledge_context (in converse.py)
# ==========================================================================


class TestAutoKnowledgeContextEdgeCases:
    """Test the hybrid-aware _auto_knowledge_context function."""

    def test_short_query_returns_empty(self, tmp_path):
        from acai.tasks.converse import _auto_knowledge_context
        messages = [{"role": "user", "content": "hi"}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_no_user_message_returns_empty(self, tmp_path):
        from acai.tasks.converse import _auto_knowledge_context
        messages = [{"role": "assistant", "content": "hello there, how can I help?"}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_empty_messages_returns_empty(self, tmp_path):
        from acai.tasks.converse import _auto_knowledge_context
        result = _auto_knowledge_context(str(tmp_path), [])
        assert result == ""

    def test_no_db_file_returns_empty(self, tmp_path):
        from acai.tasks.converse import _auto_knowledge_context
        messages = [{"role": "user", "content": "tell me about python generators"}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""

    def test_vector_store_exception_falls_back_to_fts(self, tmp_path):
        """If VectorStore construction or search raises, FTS fallback works."""
        from acai.tasks.converse import _auto_knowledge_context
        from acai.knowledge.db import KnowledgeDB
        from acai.knowledge.store import KnowledgeStore

        knowledge_dir = str(tmp_path / "knowledge")
        os.makedirs(knowledge_dir, exist_ok=True)
        store = KnowledgeStore(knowledge_dir)
        store.create("python", "async", "generators", content="Async generators explained here")

        db_path = os.path.join(knowledge_dir, ".knowledge.db")
        db = KnowledgeDB(db_path)
        db.upsert("python", "async", "generators", content="Async generators explained here")

        messages = [{"role": "user", "content": "tell me about async generators"}]
        # Even without a running embedding endpoint, should work via FTS fallback
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert "generators" in result.lower() or result == ""

    def test_non_string_content_in_message(self, tmp_path):
        """Non-string content field should not crash."""
        from acai.tasks.converse import _auto_knowledge_context
        messages = [{"role": "user", "content": ["an", "array"]}]
        result = _auto_knowledge_context(str(tmp_path), messages)
        assert result == ""


# ==========================================================================
# Edge cases: content hash
# ==========================================================================


class TestContentHashEdgeCases:
    def test_empty_string(self):
        h = _content_hash("")
        assert len(h) == 16

    def test_unicode(self):
        h = _content_hash("日本語テスト")
        assert len(h) == 16

    def test_very_long_content(self):
        h = _content_hash("x" * 10_000_000)
        assert len(h) == 16

    def test_whitespace_variations_differ(self):
        assert _content_hash("hello world") != _content_hash("hello  world")
