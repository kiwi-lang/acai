"""Agent definitions, persistence, and Jinja2 task hydration.

Agents live in ``workspace/agents/<name>/`` with:
- ``definition.json`` -- serialised :class:`AgentDef`
- ``system.j2``       -- Jinja2 template that produces the LLM context

The template receives a *resolved task* dict (all file contents loaded)
and renders either a JSON messages array (``output_format="messages"``)
or plain text (``output_format="text"``).
"""

from __future__ import annotations

import json
import logging
import os
import textwrap
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import jinja2

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------

@dataclass
class SandboxConfig:
    type: str = "none"
    network: bool = True
    writable_paths: list[str] = field(default_factory=list)
    readonly_paths: list[str] = field(default_factory=list)
    gpu: bool = False
    timeout: int = 120
    memory_limit: str = "4G"


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
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    max_iterations: int = 20
    approval_required: bool = False
    compressor: str = "compressor"
    created_at: str = ""
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if isinstance(self.sandbox, dict):
            self.sandbox = SandboxConfig(**self.sandbox)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> AgentDef:
        d = dict(d)
        if "sandbox" in d and isinstance(d["sandbox"], dict):
            d["sandbox"] = SandboxConfig(**d["sandbox"])
        return cls(**d)


# ------------------------------------------------------------------
# Default template  (output_format="messages")
# ------------------------------------------------------------------

DEFAULT_SYSTEM_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are {{ agent.name }}{% if agent.description %}, {{ agent.description }}{% endif %}.
    {% if task.project_obj %}

    You are working on project **{{ task.project_obj.name }}** ({{ task.project_obj.language }}).
    {% if task.project_spec %}

    ## Project Specification
    {{ task.project_spec }}
    {% endif %}
    {% endif %}
    {% if tools_description %}

    ## Available Tools
    {{ tools_description }}
    {% endif %}

    Answer questions, suggest plans, and create tasks when asked.
    {%- endset -%}
    [
      {"role": "system", "content": {{ system_prompt | tojson }}}
    {% for msg in messages %},
      {"role": {{ msg.role | tojson }}, "content": {{ msg.content | tojson }}}
    {% endfor %}
    ]
""")


# ------------------------------------------------------------------
# AgentStore
# ------------------------------------------------------------------

class AgentStore:
    """CRUD for agent definitions stored on disk.

    Layout::

        agents/
        └── code-reviewer/
            ├── definition.json
            └── system.j2
    """

    def __init__(self, agents_dir: str):
        self.root = agents_dir
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, name: str) -> str:
        return os.path.join(self.root, name)

    def _def_path(self, name: str) -> str:
        return os.path.join(self._dir(name), "definition.json")

    def template_path(self, name: str) -> str:
        return os.path.join(self._dir(name), "system.j2")

    # -- CRUD -------------------------------------------------------

    def save(self, agent: AgentDef) -> None:
        d = self._dir(agent.name)
        os.makedirs(d, exist_ok=True)
        with open(self._def_path(agent.name), "w") as f:
            json.dump(agent.to_dict(), f, indent=2)

    def get(self, name: str) -> AgentDef | None:
        path = self._def_path(name)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return AgentDef.from_dict(json.load(f))

    def list(self) -> list[AgentDef]:
        agents: list[AgentDef] = []
        if not os.path.isdir(self.root):
            return agents
        for entry in sorted(os.listdir(self.root)):
            defn = os.path.join(self.root, entry, "definition.json")
            if os.path.isfile(defn):
                with open(defn) as f:
                    agents.append(AgentDef.from_dict(json.load(f)))
        return agents

    def delete(self, name: str) -> None:
        import shutil
        d = self._dir(name)
        if os.path.isdir(d):
            shutil.rmtree(d)

    # -- Template I/O -----------------------------------------------

    def read_template(self, name: str) -> str:
        path = self.template_path(name)
        if os.path.isfile(path):
            with open(path) as f:
                return f.read()
        return DEFAULT_SYSTEM_TEMPLATE

    def save_template(self, name: str, content: str) -> None:
        path = self.template_path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    # -- Scaffold ---------------------------------------------------

    def scaffold(self, agent: AgentDef) -> None:
        """Save definition and write a default template if none exists."""
        self.save(agent)
        tpl = self.template_path(agent.name)
        if not os.path.isfile(tpl):
            self.save_template(agent.name, DEFAULT_SYSTEM_TEMPLATE)

    def ensure_default(self) -> AgentDef:
        """Create the built-in ``default`` agent if it doesn't exist yet."""
        existing = self.get("default")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="default",
            description="a helpful AI assistant that plans and builds software projects",
            role="worker",
            provider="auto",
            output_format="messages",
            tools=[],
        )
        self.scaffold(agent)
        return agent

    def ensure_builtin_agents(self) -> None:
        """Create the built-in refiner, coder, and compressor agents if they don't exist."""
        self._ensure_refiner()
        self._ensure_coder()
        self._ensure_compressor()

    def _ensure_refiner(self) -> AgentDef:
        existing = self.get("refiner")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="refiner",
            description="a task refinement specialist that helps users define clear, actionable work items",
            role="planner",
            provider="auto",
            output_format="messages",
            tools=["tasks", "ui"],
        )
        self.save(agent)
        self.save_template(agent.name, _REFINER_TEMPLATE)
        return agent

    def _ensure_coder(self) -> AgentDef:
        existing = self.get("coder")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="coder",
            description="a software engineer that implements features using test-driven development",
            role="worker",
            provider="auto",
            output_format="messages",
            tools=["filesystem", "code", "git"],
            max_iterations=40,
        )
        self.save(agent)
        self.save_template(agent.name, _CODER_TEMPLATE)
        return agent

    def _ensure_compressor(self) -> AgentDef:
        existing = self.get("compressor")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="compressor",
            description="a context compressor that distills conversations into concise summaries",
            role="system",
            provider="auto",
            output_format="text",
            compressor="",
        )
        self.save(agent)
        self.save_template(agent.name, _COMPRESSOR_TEMPLATE)
        return agent


# ------------------------------------------------------------------
# Refiner agent template
# ------------------------------------------------------------------

_REFINER_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are the **Refiner** — a task refinement specialist.

    Your job is to help the user turn vague ideas into well-defined, actionable tasks.
    Through conversation you should:

    1. **Clarify the goal** — ask questions until the objective is unambiguous.
    2. **Break it down** — split large requests into small, independently testable tasks.
    3. **Define acceptance criteria** — each task description should state what "done" looks like.
    4. **Create the tasks** — use ``tasks.create`` to add them to the queue.
    5. **Mark ready** — when a task is fully specified, use ``tasks.mark_ready`` so a worker can pick it up.

    Guidelines:
    - Keep task titles short (< 80 chars) and imperative ("Add user login endpoint").
    - Put acceptance criteria and context in the description field.
    - Set ``kind="work"`` for implementation tasks, ``kind="task"`` for research / planning.
    - Assign ``agent="coder"`` for implementation tasks.
    - Use ``ui.toast`` to notify the user of important status changes.
    {% if task.project_obj %}

    ## Project Context
    Project: **{{ task.project_obj.name }}** ({{ task.project_obj.language }})
    {% if task.project_obj.path %}Path: ``{{ task.project_obj.path }}``{% endif %}
    {% endif %}
    {% if task.project_spec %}

    ## Project Specification
    {{ task.project_spec }}
    {% endif %}
    {% if tools_description %}

    ## Available Tools
    {{ tools_description }}
    {% endif %}
    {%- endset -%}
    [
      {"role": "system", "content": {{ system_prompt | tojson }}}
    {% for msg in messages %},
      {"role": {{ msg.role | tojson }}, "content": {{ msg.content | tojson }}}
    {% endfor %}
    ]
""")


# ------------------------------------------------------------------
# Coder agent template
# ------------------------------------------------------------------

_CODER_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are the **Coder** — a software engineer that implements features using test-driven development.

    ## Workflow

    1. **Understand the task** — read the description and acceptance criteria carefully.
    2. **Explore the codebase** — use ``code.list_files``, ``code.read_file``, and ``code.search`` to understand the existing code structure.
    3. **Write tests first** — create failing tests that capture the acceptance criteria.
    4. **Run tests** — use ``code.run_tests`` to confirm the tests fail as expected.
    5. **Implement** — write the minimum code to make the tests pass.
    6. **Run tests again** — confirm all tests pass (including pre-existing ones).
    7. **Iterate** — if tests fail, read the output, fix the code, and re-run.
    8. **Commit** — once all tests pass, use ``git.commit`` with a clear message.
    9. **Push** — use ``git.push`` to push the branch.

    ## Rules

    - Work in small increments — commit frequently.
    - Never skip the test-first step unless the task is purely cosmetic.
    - If a test already covers the requested behavior, skip to implementation.
    - Read error output carefully before making changes.
    - Prefer editing existing files over creating new ones.
    - Follow the project's existing code style and conventions.
    {% if task.project_obj %}

    ## Project Context
    Project: **{{ task.project_obj.name }}** ({{ task.project_obj.language }})
    {% if task.project_obj.path %}Working directory: ``{{ task.project_obj.path }}``{% endif %}
    {% endif %}
    {% if task.worktree %}

    ## Working Directory
    Your worktree is at: ``{{ task.worktree }}``
    Use this as ``cwd`` for all code and git tool calls.
    {% endif %}

    ## Current Task
    **{{ task.title }}**
    {% if task.description %}
    {{ task.description }}
    {% endif %}
    {% if task.project_spec %}

    ## Project Specification
    {{ task.project_spec }}
    {% endif %}
    {% if tools_description %}

    ## Available Tools
    {{ tools_description }}
    {% endif %}
    {%- endset -%}
    [
      {"role": "system", "content": {{ system_prompt | tojson }}}
    {% for msg in messages %},
      {"role": {{ msg.role | tojson }}, "content": {{ msg.content | tojson }}}
    {% endfor %}
    ]
""")


# ------------------------------------------------------------------
# Compressor agent template
# ------------------------------------------------------------------

_COMPRESSOR_TEMPLATE = textwrap.dedent("""\
    You are a **context compressor**. Your job is to read a conversation
    between a user and an AI assistant and produce a concise summary that
    preserves all information the assistant would need to continue the
    conversation without loss of quality.

    Rules:
    - Keep all **decisions made**, **facts established**, and **open questions**.
    - Keep all **code snippets**, **file paths**, **variable names**, and **error messages** verbatim.
    - Keep the **latest user request** in full — never summarize the last user message.
    - Drop pleasantries, repeated greetings, and purely stylistic exchanges.
    - Drop tool call / tool result messages that are no longer relevant (old diagnostics, superseded searches).
    - Use compact prose; bullet lists are encouraged.
    - Output ONLY the summary — no preamble, no explanation.

    ## Conversation to compress

    {% for msg in messages %}
    **{{ msg.role }}**: {{ msg.content }}

    {% endfor %}
""")


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
    total_chars = sum(len(m.get("content", "")) for m in messages)
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

    env = jinja2.Environment(
        undefined=jinja2.Undefined,
        keep_trailing_newline=True,
    )
    tpl = env.from_string(_COMPRESSOR_TEMPLATE)
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
) -> list[dict]:
    """Render the agent's template with the resolved task.

    Returns a messages list ready for the LLM.

    If ``agent.output_format`` is ``"messages"`` the template must
    produce a JSON array.  If ``"text"`` it produces a system-prompt
    string that is automatically wrapped into a messages list.
    """
    template_src = store.read_template(agent.name)

    env = jinja2.Environment(
        undefined=jinja2.Undefined,
        keep_trailing_newline=True,
    )
    tpl = env.from_string(template_src)

    rendered = tpl.render(
        agent=agent,
        task=resolved,
        messages=resolved.get("messages", []),
        project=resolved.get("project_obj"),
        spec=resolved.get("project_spec", ""),
        tools_description=tools_description,
        datetime=datetime.now(timezone.utc).isoformat(),
    ).strip()

    if agent.output_format == "text":
        return [{"role": "system", "content": rendered}] + resolved.get("messages", [])

    try:
        return json.loads(rendered)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("Agent %s template did not produce valid JSON, "
                     "falling back to text wrapping: %s", agent.name, exc)
        return [{"role": "system", "content": rendered}] + resolved.get("messages", [])
