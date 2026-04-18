"""Agent definitions, persistence, and Jinja2 task hydration.

Built-in agents ship as files under ``assai/agents/<name>/``.
User customisations live in ``workspace/agents/<name>/``.

Each agent directory contains:
- ``definition.json`` -- serialised :class:`AgentDef`
- ``system.j2``       -- Jinja2 template that produces the LLM context

The workspace layer *shadows* the built-in layer: saving or editing a
built-in agent writes a copy to ``workspace/agents/`` (copy-on-write).
Deleting the workspace copy reveals the built-in again.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2

log = logging.getLogger(__name__)

_PACKAGE_AGENTS_DIR = str(Path(__file__).resolve().parent.parent / "agents")


# ------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------

@dataclass
class AgentDef:
    id: str = ""
    name: str = ""
    description: str = ""
    role: str = "worker"
    avatar: str = ""
    provider: str = "auto"
    output_format: str = "messages"
    model_overrides: dict = field(default_factory=dict)
    system_template: str = "system.j2"
    context_sources: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    tool_permissions: list[str] = field(default_factory=lambda: ["read"])
    uses_sandbox: bool = False
    max_iterations: int = 20
    approval_required: bool = False
    compressor: str = "compressor"
    created_at: str = ""
    tags: list[str] = field(default_factory=list)
    builtin: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        # Migrate legacy per-agent sandbox dicts to boolean flag
        if isinstance(self.uses_sandbox, dict):
            self.uses_sandbox = self.uses_sandbox.get("type", "none") != "none"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["builtin"] = self.builtin
        return d

    @classmethod
    def from_dict(cls, d: dict) -> AgentDef:
        d = dict(d)
        d.pop("builtin", None)
        # Migrate legacy "sandbox" dict to "uses_sandbox" bool
        if "sandbox" in d and "uses_sandbox" not in d:
            sb = d.pop("sandbox")
            if isinstance(sb, dict):
                d["uses_sandbox"] = sb.get("type", "none") != "none"
            elif isinstance(sb, bool):
                d["uses_sandbox"] = sb
        return cls(**d)


# ------------------------------------------------------------------
# AgentStore — layered (builtin + workspace) with copy-on-write
# ------------------------------------------------------------------

class AgentStore:
    """CRUD for agent definitions with a two-layer layout.

    *Builtin* agents ship inside the ``assai`` package and are
    **read-only**.  *Workspace* agents live in the user's workspace and
    can be freely created, edited, and deleted.

    When a builtin agent is modified, the updated copy is written to
    the workspace layer, shadowing the builtin.  Deleting a workspace
    override reveals the original builtin again.

    Layout::

        assai/agents/          (builtin — read-only)
        └── coder/
            ├── definition.json
            └── system.j2

        workspace/agents/      (workspace — writable)
        └── my-agent/
            ├── definition.json
            └── system.j2
    """

    def __init__(
        self,
        workspace_dir: str,
        builtin_dir: str = _PACKAGE_AGENTS_DIR,
    ):
        self.workspace_dir = workspace_dir
        self.builtin_dir = builtin_dir
        os.makedirs(self.workspace_dir, exist_ok=True)

    # -- path helpers ------------------------------------------------

    def _ws_dir(self, name: str) -> str:
        return os.path.join(self.workspace_dir, name)

    def _bi_dir(self, name: str) -> str:
        return os.path.join(self.builtin_dir, name)

    @staticmethod
    def _def_path(directory: str) -> str:
        return os.path.join(directory, "definition.json")

    @staticmethod
    def _tpl_path(directory: str) -> str:
        return os.path.join(directory, "system.j2")

    def _is_builtin(self, name: str) -> bool:
        return os.path.isfile(self._def_path(self._bi_dir(name)))

    def _has_workspace_override(self, name: str) -> bool:
        return os.path.isfile(self._def_path(self._ws_dir(name)))

    # -- reading (workspace shadows builtin) -------------------------

    def _load_from(self, directory: str, *, builtin: bool) -> AgentDef | None:
        path = self._def_path(directory)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            agent = AgentDef.from_dict(json.load(f))
        agent.builtin = builtin
        return agent

    def get(self, name: str) -> AgentDef | None:
        """Return an agent, preferring the workspace copy over builtin."""
        ws = self._load_from(self._ws_dir(name), builtin=False)
        if ws is not None:
            return ws
        return self._load_from(self._bi_dir(name), builtin=True)

    def list(self) -> list[AgentDef]:
        """Merge builtin and workspace agents. Workspace wins on conflict."""
        agents: dict[str, AgentDef] = {}

        for root, builtin_flag in [
            (self.builtin_dir, True),
            (self.workspace_dir, False),
        ]:
            if not os.path.isdir(root):
                continue
            for entry in sorted(os.listdir(root)):
                defn = os.path.join(root, entry, "definition.json")
                if os.path.isfile(defn):
                    with open(defn) as f:
                        agent = AgentDef.from_dict(json.load(f))
                    agent.builtin = builtin_flag
                    agents[agent.name] = agent

        return list(agents.values())

    # -- writing (always to workspace) --------------------------------

    def save(self, agent: AgentDef) -> None:
        """Save (or overwrite) an agent in the workspace layer."""
        d = self._ws_dir(agent.name)
        os.makedirs(d, exist_ok=True)
        data = agent.to_dict()
        data.pop("builtin", None)
        with open(self._def_path(d), "w") as f:
            json.dump(data, f, indent=2)

    def delete(self, name: str) -> bool:
        """Delete the workspace override. Returns False if nothing to delete."""
        import shutil
        d = self._ws_dir(name)
        if os.path.isdir(d):
            shutil.rmtree(d)
            return True
        return False

    def scaffold(self, agent: AgentDef) -> None:
        """Save definition and write the default template if none exists."""
        self.save(agent)
        tpl = self._tpl_path(self._ws_dir(agent.name))
        if not os.path.isfile(tpl):
            builtin_tpl = self._tpl_path(self._bi_dir("default"))
            fallback = ""
            if os.path.isfile(builtin_tpl):
                with open(builtin_tpl) as f:
                    fallback = f.read()
            self.save_template(agent.name, fallback)

    # -- Template I/O (workspace shadows builtin) --------------------

    def read_template(self, name: str) -> str:
        ws_path = self._tpl_path(self._ws_dir(name))
        if os.path.isfile(ws_path):
            with open(ws_path) as f:
                return f.read()
        bi_path = self._tpl_path(self._bi_dir(name))
        if os.path.isfile(bi_path):
            with open(bi_path) as f:
                return f.read()
        bi_default = self._tpl_path(self._bi_dir("default"))
        if os.path.isfile(bi_default):
            with open(bi_default) as f:
                return f.read()
        return ""

    def save_template(self, name: str, content: str) -> None:
        """Save a template to the workspace layer."""
        d = self._ws_dir(name)
        os.makedirs(d, exist_ok=True)
        with open(self._tpl_path(d), "w") as f:
            f.write(content)

    def template_path(self, name: str) -> str:
        """Return the effective template path (workspace first, then builtin)."""
        ws = self._tpl_path(self._ws_dir(name))
        if os.path.isfile(ws):
            return ws
        bi = self._tpl_path(self._bi_dir(name))
        if os.path.isfile(bi):
            return bi
        return ws

    _STANDARD_VARS = frozenset({
        "agent", "task", "messages", "project", "spec",
        "tools_description", "datetime",
        "range", "true", "false", "none", "loop",
    })

    def template_inputs(self, name: str) -> list[str]:
        """Return custom Jinja2 variable names used by the agent's template.

        Standard context variables (``agent``, ``task``, ``messages``, etc.)
        are excluded — only user-defined variables that need explicit values
        are returned, sorted alphabetically.
        """
        from jinja2 import Environment, meta as jinja_meta

        src = self.read_template(name)
        if not src:
            return []
        try:
            env = Environment()
            ast = env.parse(src)
            all_vars = jinja_meta.find_undeclared_variables(ast)
        except Exception:
            return []
        return sorted(all_vars - self._STANDARD_VARS)


# ------------------------------------------------------------------
# Context compression
# ------------------------------------------------------------------

def compress_messages(
    messages: list[dict],
    context_window: int,
    llm_client,
    *,
    threshold: float = 0.75,
    keep_recent: int = 6,
    model: str | None = None,
) -> list[dict]:
    """Compress a conversation if it exceeds *threshold* of *context_window*.

    Keeps the system message (index 0) and the last *keep_recent* messages
    intact.  Everything in between is summarized by the compressor LLM
    into a single ``{"role": "system", "content": "..."}`` message.

    Returns the (possibly shortened) messages list.
    """
    def _content_len(m: dict) -> int:
        c = m.get("content")
        if isinstance(c, str):
            return len(c)
        if isinstance(c, list):
            return sum(len(p.get("text", "")) for p in c if isinstance(p, dict))
        return 0

    total_chars = sum(_content_len(m) for m in messages if isinstance(m, dict))
    estimated_tokens = total_chars // 4
    limit = int(context_window * threshold)

    if estimated_tokens <= limit or len(messages) <= keep_recent + 2:
        return messages

    log.info(
        "Context compression triggered: ~%d tokens vs %d limit (%d messages)",
        estimated_tokens, limit, len(messages),
    )

    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    start = 1 if system_msg else 0
    split = max(start, len(messages) - keep_recent)
    old_messages = messages[start:split]
    recent_messages = messages[split:]

    if not old_messages:
        return messages

    compressor_tpl_path = os.path.join(_PACKAGE_AGENTS_DIR, "compressor", "system.j2")
    try:
        with open(compressor_tpl_path) as f:
            compressor_tpl_src = f.read()
    except OSError:
        log.warning("Compressor template not found at %s", compressor_tpl_path)
        return messages

    env = jinja2.Environment(
        undefined=jinja2.Undefined,
        keep_trailing_newline=True,
    )
    tpl = env.from_string(compressor_tpl_src)
    prompt = tpl.render(messages=old_messages).strip()

    try:
        resp = llm_client.chat.completions.create(
            model=model or "default",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=min(2048, context_window // 4),
        )
        summary = resp.choices[0].message.content.strip()
    except Exception:
        log.exception("Compression LLM call failed, keeping original messages")
        return messages

    summary_msg = {
        "role": "system",
        "content": f"[Conversation summary — earlier messages compressed]\n\n{summary}",
    }

    compressed: list[dict] = []
    if system_msg:
        compressed.append(system_msg)
    compressed.append(summary_msg)
    compressed.extend(recent_messages)

    log.info(
        "Compressed %d messages -> %d (%d old -> 1 summary + %d recent)",
        len(messages), len(compressed), len(old_messages), len(recent_messages),
    )
    return compressed


# ------------------------------------------------------------------
# Task resolution
# ------------------------------------------------------------------

def resolve_task(task, config: Any, chat: Any, projects: Any) -> dict:
    """Build a resolved dict with file contents loaded from task paths.

    The returned dict is passed to the Jinja2 template as ``task``.
    """
    resolved: dict[str, Any] = {
        "id": task.id,
        "kind": task.kind,
        "title": task.title,
        "description": task.description or "",
        "priority": task.priority,
        "project": task.project or "",
        "agent": task.agent or "",
        "gpu": task.gpu,
        "parent_task": task.parent_task or "",
        "root_task": task.root_task or "",
        "worktree": getattr(task, "worktree", "") or "",
    }

    resolved["spec"] = task.spec or ""
    resolved["spec_path"] = task.spec_path or ""
    resolved["spec_content"] = resolved["spec"]
    if not resolved["spec"] and task.spec_path and os.path.isfile(task.spec_path):
        try:
            with open(task.spec_path) as f:
                resolved["spec_content"] = f.read()
        except OSError:
            pass

    resolved["messages"] = []
    resolved["conversation"] = ""
    if task.spec_path and task.spec_path.endswith("conversation.json"):
        conv_dir = os.path.dirname(task.spec_path)
        resolved["conversation"] = os.path.basename(conv_dir)
        try:
            all_messages = json.loads(resolved["spec_content"])
        except (json.JSONDecodeError, TypeError):
            all_messages = []
        _DISPLAY_ROLES = {"tool_call", "tool_result"}
        resolved["messages"] = [
            m for m in all_messages
            if m.get("role") not in _DISPLAY_ROLES
        ]

    resolved["project_obj"] = None
    if resolved["project"] and projects is not None:
        resolved["project_obj"] = projects.get(resolved["project"])

    resolved["project_spec"] = ""
    if hasattr(config, "scribe"):
        spec_file = os.path.join(config.scribe.specs_dir, "spec.md")
        if os.path.isfile(spec_file):
            try:
                with open(spec_file) as f:
                    resolved["project_spec"] = f.read()
            except OSError:
                pass

    return resolved


# ------------------------------------------------------------------
# Jinja2 hydration
# ------------------------------------------------------------------

def hydrate_task(
    agent: AgentDef,
    store: AgentStore,
    resolved: dict,
    *,
    tools_description: str = "",
    extra_context: dict | None = None,
) -> list[dict]:
    """Render the agent's template with the resolved task.

    Returns a messages list ready for the LLM.

    If ``agent.output_format`` is ``"messages"`` the template must
    produce a JSON array.  If ``"text"`` it produces a system-prompt
    string that is automatically wrapped into a messages list.

    ``extra_context``, when provided, is passed as additional top-level
    variables to the Jinja2 template render call.
    """
    template_src = store.read_template(agent.name)

    env = jinja2.Environment(
        undefined=jinja2.Undefined,
        keep_trailing_newline=True,
    )
    tpl = env.from_string(template_src)

    ctx: dict = {
        "agent": agent,
        "task": resolved,
        "messages": resolved.get("messages", []),
        "project": resolved.get("project_obj"),
        "spec": resolved.get("project_spec", ""),
        "tools_description": tools_description,
        "datetime": datetime.now(timezone.utc).isoformat(),
    }
    if extra_context:
        ctx.update(extra_context)

    rendered = tpl.render(**ctx).strip()

    if agent.output_format == "text":
        return [{"role": "system", "content": rendered}] + resolved.get("messages", [])

    try:
        return json.loads(rendered)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("Agent %s template did not produce valid JSON, "
                     "falling back to text wrapping: %s", agent.name, exc)
        return [{"role": "system", "content": rendered}] + resolved.get("messages", [])
