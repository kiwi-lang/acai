"""Jupyter notebook tools — read and edit ``.ipynb`` cells."""

from __future__ import annotations

import json
from typing import Optional

from acai.orchestrator.tools import tool


@tool(permissions=("read",), resources=("notebooks:read",))
def read_notebook(path: str) -> str:
    """Load a notebook and return cell indices, types, and source previews.

    Args:
        path: Path to the ``.ipynb`` file.
    """
    try:
        with open(path, encoding="utf-8") as f:
            nb = json.load(f)
    except OSError as exc:
        return json.dumps({"error": str(exc)})

    cells = nb.get("cells", [])
    summary = []
    for i, cell in enumerate(cells):
        cid = cell.get("id") or ""
        ctype = cell.get("cell_type", "")
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        preview = src.strip().replace("\n", " ")[:120]
        summary.append({
            "index": i,
            "id": cid,
            "cell_type": ctype,
            "source_preview": preview,
        })
    return json.dumps({"path": path, "nbformat": nb.get("nbformat"), "cells": summary})


@tool(permissions=("write",), resources=("notebooks:write",))
def edit_notebook_cell(
    notebook_path: str,
    new_source: str,
    cell_index: int = -1,
    cell_id: str = "",
    cell_type: str = "",
) -> str:
    """Replace a notebook cell's source (and optionally its type).

    Args:
        notebook_path: Path to the ``.ipynb`` file.
        new_source: New cell source text.
        cell_index: Zero-based cell index (use -1 if resolving by ``cell_id``).
        cell_id: If set, find the cell with this ``id`` in metadata.
        cell_type: If set, change cell type (``code`` or ``markdown``, etc.).
    """
    try:
        with open(notebook_path, encoding="utf-8") as f:
            nb = json.load(f)
    except OSError as exc:
        return json.dumps({"error": str(exc)})

    cells = nb.get("cells", [])
    idx: Optional[int] = None
    if cell_id:
        for i, c in enumerate(cells):
            if c.get("id") == cell_id:
                idx = i
                break
        if idx is None:
            return json.dumps({"error": f"cell id not found: {cell_id}"})
    else:
        if cell_index < 0 or cell_index >= len(cells):
            return json.dumps({"error": f"cell_index out of range: {cell_index}"})
        idx = cell_index

    cell = cells[idx]
    cell["source"] = new_source
    if cell_type:
        cell["cell_type"] = cell_type

    try:
        with open(notebook_path, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
        return json.dumps({"ok": True, "path": notebook_path, "cell_index": idx})
    except OSError as exc:
        return json.dumps({"error": str(exc)})
