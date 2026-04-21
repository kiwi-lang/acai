"""Serve the built React UI from the installed package."""

from __future__ import annotations

import os
from pathlib import Path

_UI_DIR = Path(__file__).parent
_DIST_DIR = _UI_DIR / "dist"


def has_built_ui() -> bool:
    """Return True if the pre-built UI bundle exists."""
    return (_DIST_DIR / "index.html").is_file()


def mount_ui(app):
    """Mount the React SPA on a FastAPI ``app``.

    * ``/assets/*`` — hashed JS/CSS/media served by Starlette StaticFiles.
    * Every other path that doesn't match an API route falls through to
      ``index.html`` so client-side routing works.
    """
    if not has_built_ui():
        return

    from fastapi import Request
    from fastapi.responses import FileResponse, HTMLResponse
    from starlette.staticfiles import StaticFiles

    assets_dir = _DIST_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui-assets")

    for static_file in _DIST_DIR.iterdir():
        if static_file.is_file() and static_file.name != "index.html":
            name = static_file.name

            def _make_handler(fpath):
                async def _handler():
                    return FileResponse(fpath)
                return _handler

            app.get(f"/{name}", include_in_schema=False)(_make_handler(static_file))

    index_html = _DIST_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    async def _serve_index():
        return FileResponse(index_html)

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa_fallback(request: Request, path: str):
        """Serve index.html for any route not handled by the API."""
        file_path = _DIST_DIR / path
        if file_path.is_file() and ".." not in path:
            return FileResponse(file_path)
        return FileResponse(index_html)
