"""Unit tests for acai/ui/__init__.py."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestHasBuiltUi:
    def test_returns_true_when_index_exists(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")

        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import has_built_ui
            assert has_built_ui() is True

    def test_returns_false_when_dist_missing(self, tmp_path):
        dist = tmp_path / "dist"

        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import has_built_ui
            assert has_built_ui() is False

    def test_returns_false_when_index_missing(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()

        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import has_built_ui
            assert has_built_ui() is False


class TestMountUi:
    def test_no_op_when_no_built_ui(self, tmp_path):
        dist = tmp_path / "dist"

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import mount_ui
            mount_ui(app)

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 404

    def test_serves_index_at_root(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><body>App</body></html>")
        assets = dist / "assets"
        assets.mkdir()

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import mount_ui
            mount_ui(app)

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "App" in resp.text

    def test_spa_fallback_serves_index(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html><body>SPA</body></html>")
        assets = dist / "assets"
        assets.mkdir()

        import acai.ui
        from fastapi import Request
        from fastapi.responses import FileResponse, HTMLResponse

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            # Inject imports into module globals so FastAPI can resolve annotations
            acai.ui.Request = Request
            acai.ui.FileResponse = FileResponse
            acai.ui.HTMLResponse = HTMLResponse
            try:
                from acai.ui import mount_ui
                mount_ui(app)
            finally:
                del acai.ui.Request
                del acai.ui.FileResponse
                del acai.ui.HTMLResponse

        client = TestClient(app)
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "SPA" in resp.text

    def test_serves_static_file_in_dist(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
        assets = dist / "assets"
        assets.mkdir()

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import mount_ui
            mount_ui(app)

        client = TestClient(app)
        resp = client.get("/favicon.ico")
        assert resp.status_code == 200

    def test_serves_actual_file_over_spa_fallback(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>index</html>")
        (dist / "robots.txt").write_text("User-agent: *")
        assets = dist / "assets"
        assets.mkdir()

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import mount_ui
            mount_ui(app)

        client = TestClient(app)
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert "User-agent" in resp.text

    def test_no_assets_dir_still_works(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>no-assets</html>")

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import mount_ui
            mount_ui(app)

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "no-assets" in resp.text

    def test_path_traversal_blocked(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>safe</html>")
        assets = dist / "assets"
        assets.mkdir()

        secret = tmp_path / "secret.txt"
        secret.write_text("TOP SECRET")

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            from acai.ui import mount_ui
            mount_ui(app)

        client = TestClient(app)
        resp = client.get("/../secret.txt")
        assert "TOP SECRET" not in resp.text

    def test_spa_fallback_serves_existing_subdir_file(self, tmp_path):
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html>index</html>")
        assets = dist / "assets"
        assets.mkdir()
        subdir = dist / "data"
        subdir.mkdir()
        (subdir / "info.json").write_text('{"ok": true}')

        import acai.ui
        from fastapi import Request
        from fastapi.responses import FileResponse, HTMLResponse

        app = FastAPI()
        with patch("acai.ui._DIST_DIR", dist):
            acai.ui.Request = Request
            acai.ui.FileResponse = FileResponse
            acai.ui.HTMLResponse = HTMLResponse
            try:
                from acai.ui import mount_ui
                mount_ui(app)
            finally:
                del acai.ui.Request
                del acai.ui.FileResponse
                del acai.ui.HTMLResponse

            client = TestClient(app)
            resp = client.get("/data/info.json")
            assert resp.status_code == 200
            assert '{"ok": true}' in resp.text
