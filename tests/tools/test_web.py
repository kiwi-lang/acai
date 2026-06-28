"""Unit tests for acai/tools/web.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import requests

from acai.tools.web import _StripHTML, fetch_url, search_web


class TestStripHTML:
    def test_plain_text(self):
        p = _StripHTML()
        p.feed("Hello world")
        assert p.text() == "Hello world"

    def test_strips_tags(self):
        p = _StripHTML()
        p.feed("<p>Hello <b>world</b></p>")
        assert p.text() == "Hello world"

    def test_strips_script(self):
        p = _StripHTML()
        p.feed("<div>Before </div><script>alert('x')</script><div> After</div>")
        assert p.text() == "Before After"

    def test_strips_style(self):
        p = _StripHTML()
        p.feed("<style>body{color:red}</style><p>Visible</p>")
        assert p.text() == "Visible"

    def test_strips_noscript(self):
        p = _StripHTML()
        p.feed("<noscript>Enable JS</noscript><p>Content</p>")
        assert p.text() == "Content"

    def test_nested_skip_tags(self):
        p = _StripHTML()
        p.feed("<script><script>inner</script></script><p>OK</p>")
        assert p.text() == "OK"

    def test_collapses_whitespace(self):
        p = _StripHTML()
        p.feed("<p>  lots   of   spaces  </p>")
        assert p.text() == "lots of spaces"

    def test_empty_input(self):
        p = _StripHTML()
        p.feed("")
        assert p.text() == ""

    def test_only_script(self):
        p = _StripHTML()
        p.feed("<script>var x = 1;</script>")
        assert p.text() == ""


class TestFetchUrl:
    @patch("acai.tools.web.http.get")
    def test_html_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.text = "<html><body><p>Hello</p><script>x</script></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://example.com"))
        assert result["url"] == "https://example.com"
        assert result["status"] == 200
        assert result["content_type"] == "text/html; charset=utf-8"
        assert result["text"] == "Hello"

    @patch("acai.tools.web.http.get")
    def test_plain_text_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "Plain text content"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://example.com/file.txt"))
        assert result["text"] == "Plain text content"

    @patch("acai.tools.web.http.get")
    def test_json_response_not_stripped(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = '{"key": "value"}'
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://api.example.com/data"))
        assert result["text"] == '{"key": "value"}'

    @patch("acai.tools.web.http.get")
    def test_truncation(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "x" * 200
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://example.com", max_chars=50))
        assert result["text"] == "x" * 50 + "\n... (truncated)"

    @patch("acai.tools.web.http.get")
    def test_no_truncation_at_boundary(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "x" * 50
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://example.com", max_chars=50))
        assert result["text"] == "x" * 50
        assert "(truncated)" not in result["text"]

    @patch("acai.tools.web.http.get")
    def test_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("404 Not Found")
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://example.com/missing"))
        assert "error" in result
        assert "404" in result["error"]
        assert result["url"] == "https://example.com/missing"

    @patch("acai.tools.web.http.get")
    def test_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("DNS failure")

        result = json.loads(fetch_url("https://nonexistent.invalid"))
        assert "error" in result
        assert result["url"] == "https://nonexistent.invalid"

    @patch("acai.tools.web.http.get")
    def test_timeout_error(self, mock_get):
        mock_get.side_effect = requests.Timeout("Request timed out")

        result = json.loads(fetch_url("https://slow.example.com"))
        assert "error" in result
        assert result["url"] == "https://slow.example.com"

    @patch("acai.tools.web.http.get")
    def test_request_headers(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "ok"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        fetch_url("https://example.com")
        mock_get.assert_called_once_with(
            "https://example.com",
            timeout=30,
            headers={"User-Agent": "acai-tools/1.0"},
        )

    @patch("acai.tools.web.http.get")
    def test_missing_content_type_header(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = "<p>Some text</p>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = json.loads(fetch_url("https://example.com"))
        # No content-type means no HTML detection; body returned as-is
        assert result["text"] == "<p>Some text</p>"
        assert result["content_type"] == ""


class TestSearchWeb:
    @patch("acai.tools.web.http.get")
    def test_basic_search(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "Python is a language.",
            "AbstractURL": "https://python.org",
            "RelatedTopics": [
                {"Text": "Python tutorial"},
                {"Text": "Python docs"},
            ],
        }
        mock_get.return_value = mock_resp

        result = json.loads(search_web("python"))
        assert result["query"] == "python"
        assert result["abstract"] == "Python is a language."
        assert result["abstract_url"] == "https://python.org"
        assert result["related"] == ["Python tutorial", "Python docs"]

    @patch("acai.tools.web.http.get")
    def test_empty_abstract(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "",
            "AbstractURL": "",
            "RelatedTopics": [],
        }
        mock_get.return_value = mock_resp

        result = json.loads(search_web("obscure query"))
        assert result["abstract"] == ""
        assert result["abstract_url"] == ""
        assert result["related"] == []

    @patch("acai.tools.web.http.get")
    def test_missing_abstract_key(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"RelatedTopics": []}
        mock_get.return_value = mock_resp

        result = json.loads(search_web("query"))
        assert result["abstract"] == ""
        assert result["abstract_url"] == ""

    @patch("acai.tools.web.http.get")
    def test_nested_topics(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "",
            "AbstractURL": "",
            "RelatedTopics": [
                {
                    "Topics": [
                        {"Text": "Subtopic 1"},
                        {"Text": "Subtopic 2"},
                    ]
                },
            ],
        }
        mock_get.return_value = mock_resp

        result = json.loads(search_web("nested"))
        assert "Subtopic 1" in result["related"]
        assert "Subtopic 2" in result["related"]

    @patch("acai.tools.web.http.get")
    def test_max_related_limit(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        topics = [{"Text": f"Topic {i}"} for i in range(20)]
        mock_resp.json.return_value = {
            "Abstract": "",
            "AbstractURL": "",
            "RelatedTopics": topics,
        }
        mock_get.return_value = mock_resp

        result = json.loads(search_web("many topics", max_related=3))
        assert len(result["related"]) == 3

    @patch("acai.tools.web.http.get")
    def test_mixed_topic_types(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "Summary",
            "AbstractURL": "https://example.com",
            "RelatedTopics": [
                {"Text": "Direct topic"},
                {"Topics": [{"Text": "Nested topic"}]},
                {"Text": "Another direct"},
            ],
        }
        mock_get.return_value = mock_resp

        result = json.loads(search_web("mixed", max_related=8))
        assert "Direct topic" in result["related"]
        assert "Nested topic" in result["related"]
        assert "Another direct" in result["related"]

    @patch("acai.tools.web.http.get")
    def test_topic_without_text_skipped(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "",
            "AbstractURL": "",
            "RelatedTopics": [
                {"FirstURL": "https://example.com"},
                {"Text": "Valid topic"},
            ],
        }
        mock_get.return_value = mock_resp

        result = json.loads(search_web("skip invalid"))
        assert result["related"] == ["Valid topic"]

    @patch("acai.tools.web.http.get")
    def test_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("Network unreachable")

        result = json.loads(search_web("fail query"))
        assert "error" in result
        assert result["query"] == "fail query"

    @patch("acai.tools.web.http.get")
    def test_timeout_error(self, mock_get):
        mock_get.side_effect = requests.Timeout("Timed out")

        result = json.loads(search_web("slow query"))
        assert "error" in result
        assert result["query"] == "slow query"

    @patch("acai.tools.web.http.get")
    def test_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("503 Service Unavailable")
        mock_get.return_value = mock_resp

        result = json.loads(search_web("error query"))
        assert "error" in result
        assert "503" in result["error"]
        assert result["query"] == "error query"

    @patch("acai.tools.web.http.get")
    def test_query_url_encoding(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "",
            "AbstractURL": "",
            "RelatedTopics": [],
        }
        mock_get.return_value = mock_resp

        search_web("hello world & more")
        called_url = mock_get.call_args[0][0]
        assert "hello+world+%26+more" in called_url

    @patch("acai.tools.web.http.get")
    def test_request_headers_and_timeout(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "Abstract": "",
            "AbstractURL": "",
            "RelatedTopics": [],
        }
        mock_get.return_value = mock_resp

        search_web("test")
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 20
        assert kwargs["headers"] == {"User-Agent": "acai-tools/1.0"}
