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
            resolved["messages"] = json.loads(resolved["spec_content"])
        except (json.JSONDecodeError, TypeError):
            resolved["messages"] = []

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
