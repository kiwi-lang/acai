"""Persistent conversation storage in ``workspace/projects/<name>/chat.json``.

Each project keeps a single chat file that is an array of
``{role, content}`` message dicts (matching the OpenAI format).
"""

from __future__ import annotations

import json
import os
import threading


class ChatStore:
    """Thread-safe read/append for project chat files."""

    def __init__(self, projects_dir: str):
        self.projects_dir = projects_dir
        self._lock = threading.Lock()

    def _path(self, project: str) -> str:
        return os.path.join(self.projects_dir, project, "chat.json")

    def read(self, project: str) -> list[dict]:
        path = self._path(project)
        if not os.path.isfile(path):
            return []
        with self._lock:
            with open(path, encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    return []
        if not isinstance(data, list):
            return []
        return data

    def append(self, project: str, message: dict) -> None:
        path = self._path(project)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            messages = []
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    try:
                        messages = json.load(f)
                    except (json.JSONDecodeError, ValueError):
                        messages = []
            if not isinstance(messages, list):
                messages = []
            messages.append(message)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def write(self, project: str, messages: list[dict]) -> None:
        """Overwrite the entire chat history for a project."""
        path = self._path(project)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)

    def clear(self, project: str) -> None:
        path = self._path(project)
        with self._lock:
            if os.path.isfile(path):
                os.remove(path)
