"""Persistent conversation storage.

Layout::

    # Conversations without a project (legacy / global)
    workspace/conversations/<conv_id>/conversation.json

    # Project conversations
    workspace/projects/<project>/conversations/<conv_id>/conversation.json

    # Task-scoped conversations within a project
    workspace/projects/<project>/<task_id>/<conv_id>/conversation.json

Each conversation file is an array of ``{role, content}`` message
dicts (matching the OpenAI format).

A ``metadata.json`` next to each ``conversation.json`` stores the
conversation title, creation time, and optional project / task link.
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
    __slots__ = ("id", "title", "description", "project", "task_id",
                 "provider", "agent", "tags", "created_at")

    def __init__(self, id: str, title: str = "", description: str = "",
                 project: str = "", task_id: str = "",
                 provider: str = "auto", agent: str = "",
                 tags: list[str] | None = None,
                 created_at: float | None = None):
        self.id = id
        self.title = title or id
        self.description = description or ""
        self.project = project
        self.task_id = task_id or ""
        self.provider = provider or "auto"
        self.agent = agent or ""
        self.tags = tags or []
        self.created_at = created_at or time.time()

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "project": self.project,
            "provider": self.provider,
            "agent": self.agent,
            "tags": self.tags,
            "created_at": self.created_at,
        }
        if self.task_id:
            d["task_id"] = self.task_id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ConversationMeta:
        return cls(
            id=d["id"],
            title=d.get("title", d["id"]),
            description=d.get("description", ""),
            project=d.get("project", ""),
            task_id=d.get("task_id", ""),
            provider=d.get("provider", "auto"),
            agent=d.get("agent", ""),
            tags=d.get("tags", []),
            created_at=d.get("created_at"),
        )


class ChatStore:
    """Thread-safe CRUD for conversations stored on disk.

    Path resolution
    ---------------
    * No project  → ``workspace/conversations/<conv_id>/``
    * Project     → ``workspace/projects/<project>/conversations/<conv_id>/``
    * Project+task→ ``workspace/projects/<project>/<task_id>/<conv_id>/``

    The ``conv_id`` alone is still the primary key.  A lightweight
    index (``_index``) maps each known ``conv_id`` to its directory
    on disk so that ``get`` / ``read`` / ``append`` work without
    the caller having to remember the project or task.
    """

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.base = os.path.join(workspace, "conversations")
        self._projects_root = os.path.join(workspace, "projects")
        self._tmp = os.path.join(workspace, "tmp")
        os.makedirs(self.base, exist_ok=True)
        self._lock = threading.Lock()
        self._index: dict[str, str] = {}
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Index — maps conv_id → absolute directory path
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """Walk all known conversation locations and populate the index."""
        idx: dict[str, str] = {}
        self._collect_convs(self.base, idx)
        if os.path.isdir(self._projects_root):
            for proj in os.listdir(self._projects_root):
                proj_dir = os.path.join(self._projects_root, proj)
                if not os.path.isdir(proj_dir):
                    continue
                proj_convs = os.path.join(proj_dir, "conversations")
                self._collect_convs(proj_convs, idx)
                _SKIP = {"conversations", "definition.json", ".git", ".worktrees"}
                for entry in os.listdir(proj_dir):
                    if entry in _SKIP or entry.startswith("."):
                        continue
                    task_dir = os.path.join(proj_dir, entry)
                    if not os.path.isdir(task_dir):
                        continue
                    self._collect_convs(task_dir, idx)
        self._index = idx

    @staticmethod
    def _collect_convs(parent: str, idx: dict[str, str]) -> None:
        if not os.path.isdir(parent):
            return
        for name in os.listdir(parent):
            d = os.path.join(parent, name)
            if os.path.isfile(os.path.join(d, "metadata.json")):
                idx[name] = d

    def _register(self, conv_id: str, directory: str) -> None:
        self._index[conv_id] = directory

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _conv_root(self, project: str = "", task_id: str = "") -> str:
        """Return the parent directory where a new conversation dir is created."""
        if project:
            proj_dir = os.path.join(self._projects_root, project)
            if task_id:
                return os.path.join(proj_dir, task_id)
            return os.path.join(proj_dir, "conversations")
        return self.base

    def _dir(self, conv_id: str) -> str:
        if conv_id in self._index:
            return self._index[conv_id]
        self._rebuild_index()
        if conv_id in self._index:
            return self._index[conv_id]
        if conv_id.startswith("ephemeral-"):
            return os.path.join(self._tmp, conv_id)
        return os.path.join(self.base, conv_id)

    def _msg_path(self, conv_id: str) -> str:
        return os.path.join(self._dir(conv_id), "conversation.json")

    def _meta_path(self, conv_id: str) -> str:
        return os.path.join(self._dir(conv_id), "metadata.json")

    # ------------------------------------------------------------------
    # Conversation lifecycle
    # ------------------------------------------------------------------

    def create(self, title: str = "", project: str = "",
               task_id: str = "",
               provider: str = "auto", agent: str = "") -> ConversationMeta:
        conv_id = _new_id()
        meta = ConversationMeta(id=conv_id, title=title, project=project,
                                task_id=task_id,
                                provider=provider, agent=agent)
        root = self._conv_root(project, task_id)
        d = os.path.join(root, conv_id)
        with self._lock:
            os.makedirs(d, exist_ok=True)
            self._write_json(os.path.join(d, "metadata.json"), meta.to_dict())
            self._write_json(os.path.join(d, "conversation.json"), [])
            self._register(conv_id, d)
        return meta

    def list(self, project: str = "", task_id: str = "") -> list[dict]:
        """Return conversation metadata, newest first.

        When *project* is given, only conversations under that project
        are returned.  When *task_id* is also given, further narrowed
        to that task.  With neither, all conversations across all
        locations are returned.
        """
        results: list[dict] = []

        if project and task_id:
            search_roots = [os.path.join(self._projects_root, project, task_id)]
        elif project:
            search_roots = [
                os.path.join(self._projects_root, project, "conversations"),
            ]
            proj_dir = os.path.join(self._projects_root, project)
            if os.path.isdir(proj_dir):
                for entry in os.listdir(proj_dir):
                    if entry in ("conversations", "definition.json") or entry.startswith("."):
                        continue
                    td = os.path.join(proj_dir, entry)
                    if os.path.isdir(td):
                        search_roots.append(td)
        else:
            self._rebuild_index()
            for conv_id, d in self._index.items():
                mp = os.path.join(d, "metadata.json")
                self._try_load_meta(mp, conv_id, results)
            results.sort(key=lambda d: d.get("created_at", 0), reverse=True)
            return results

        for root in search_roots:
            if not os.path.isdir(root):
                continue
            for name in os.listdir(root):
                mp = os.path.join(root, name, "metadata.json")
                self._try_load_meta(mp, name, results)
        results.sort(key=lambda d: d.get("created_at", 0), reverse=True)
        return results

    # ------------------------------------------------------------------
    # Task conversations — stored as conv_1.json, conv_2.json, …
    # in workspace/projects/<project>/<task_id>/ (never in the UI index)
    # ------------------------------------------------------------------

    def _task_dir(self, project: str, task_id: str) -> str:
        return os.path.join(self._projects_root, project, task_id)

    def task_history(self, project: str, task_id: str) -> list[str]:
        """Return paths to task conversation files, oldest first.

        Files are named ``conv_1.json``, ``conv_2.json``, … and sorted
        by their numeric suffix.
        """
        td = self._task_dir(project, task_id)
        if not os.path.isdir(td):
            return []
        import re
        conv_re = re.compile(r"^conv_(\d+)\.json$")
        hits: list[tuple[int, str]] = []
        for name in os.listdir(td):
            m = conv_re.match(name)
            if m:
                hits.append((int(m.group(1)), os.path.join(td, name)))
        hits.sort()
        return [path for _, path in hits]

    def read_task_conversation(self, path: str) -> list[dict]:
        """Read a task conversation file (a JSON message array)."""
        if not os.path.isfile(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def save_task_conversation(
        self, project: str, task_id: str, messages: list[dict],
    ) -> str:
        """Append a new conversation file and return its path.

        Determines the next ``conv_N.json`` number automatically.
        """
        td = self._task_dir(project, task_id)
        os.makedirs(td, exist_ok=True)
        existing = self.task_history(project, task_id)
        n = len(existing) + 1
        path = os.path.join(td, f"conv_{n}.json")
        with self._lock:
            self._write_json(path, messages)
        return path

    @staticmethod
    def _try_load_meta(mp: str, conv_id: str, out: list[dict]) -> None:
        if not os.path.isfile(mp):
            return
        try:
            with open(mp, encoding="utf-8") as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            out.append({"id": conv_id, "title": conv_id, "project": "", "created_at": 0})

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
            self._index.pop(conv_id, None)
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
