"""Persistent conversation storage.

Layout::

    workspace/conversations/<conv_id>/conversation.json

Each conversation file is an array of ``{role, content}`` message
dicts (matching the OpenAI format).

A ``metadata.json`` next to each ``conversation.json`` stores the
conversation title, creation time, and optional project link.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ConversationMeta:
    __slots__ = ("id", "title", "project", "created_at")

    def __init__(self, id: str, title: str = "", project: str = "",
                 created_at: float | None = None):
        self.id = id
        self.title = title or id
        self.project = project
        self.created_at = created_at or time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "project": self.project,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConversationMeta:
        return cls(
            id=d["id"],
            title=d.get("title", d["id"]),
            project=d.get("project", ""),
            created_at=d.get("created_at"),
        )


class ChatStore:
    """Thread-safe CRUD for conversations stored on disk."""

    def __init__(self, workspace: str):
        self.base = os.path.join(workspace, "conversations")
        os.makedirs(self.base, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _dir(self, conv_id: str) -> str:
        return os.path.join(self.base, conv_id)

    def _msg_path(self, conv_id: str) -> str:
        return os.path.join(self._dir(conv_id), "conversation.json")

    def _meta_path(self, conv_id: str) -> str:
        return os.path.join(self._dir(conv_id), "metadata.json")

    # ------------------------------------------------------------------
    # Conversation lifecycle
    # ------------------------------------------------------------------

    def create(self, title: str = "", project: str = "") -> ConversationMeta:
        conv_id = _new_id()
        meta = ConversationMeta(id=conv_id, title=title, project=project)
        d = self._dir(conv_id)
        with self._lock:
            os.makedirs(d, exist_ok=True)
            self._write_json(self._meta_path(conv_id), meta.to_dict())
            self._write_json(self._msg_path(conv_id), [])
        return meta

    def list(self) -> list[dict]:
        """Return all conversation metadata, newest first."""
        results = []
        if not os.path.isdir(self.base):
            return results
        for name in os.listdir(self.base):
            mp = self._meta_path(name)
            if os.path.isfile(mp):
                try:
                    with open(mp, encoding="utf-8") as f:
                        results.append(json.load(f))
                except (json.JSONDecodeError, OSError):
                    results.append({"id": name, "title": name, "project": "", "created_at": 0})
        results.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        return results

    def get_meta(self, conv_id: str) -> dict | None:
        mp = self._meta_path(conv_id)
        if not os.path.isfile(mp):
            return None
        try:
            with open(mp, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def update_meta(self, conv_id: str, **fields) -> dict | None:
        meta = self.get_meta(conv_id)
        if meta is None:
            return None
        meta.update(fields)
        with self._lock:
            self._write_json(self._meta_path(conv_id), meta)
        return meta

    def delete(self, conv_id: str) -> bool:
        d = self._dir(conv_id)
        if not os.path.isdir(d):
            return False
        import shutil
        with self._lock:
            shutil.rmtree(d, ignore_errors=True)
        return True

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def read(self, conv_id: str) -> list[dict]:
        path = self._msg_path(conv_id)
        if not os.path.isfile(path):
            return []
        with self._lock:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, ValueError, OSError):
                return []
        return data if isinstance(data, list) else []

    def append(self, conv_id: str, message: dict) -> None:
        path = self._msg_path(conv_id)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            messages = self._read_json_list(path)
            messages.append(message)
            self._write_json(path, messages)

        if message.get("role") == "user" and len(messages) == 1:
            self._auto_title(conv_id, message.get("content", ""))

    def write(self, conv_id: str, messages: list[dict]) -> None:
        """Overwrite the entire message history."""
        path = self._msg_path(conv_id)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._write_json(path, messages)

    def clear(self, conv_id: str) -> None:
        path = self._msg_path(conv_id)
        with self._lock:
            if os.path.isfile(path):
                os.remove(path)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read_json_list(self, path: str) -> list:
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _write_json(self, path: str, data) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _auto_title(self, conv_id: str, first_message: str) -> None:
        """Set the conversation title from the first user message."""
        title = first_message.strip().split("\n")[0][:80]
        if not title:
            return
        self.update_meta(conv_id, title=title)
