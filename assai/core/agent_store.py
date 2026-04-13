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
        """Create the built-in agents if they don't exist."""
        self._ensure_refiner()
        self._ensure_coder()
        self._ensure_coder2()
        self._ensure_compressor()
        self._ensure_explorer()
        self._ensure_planner()
        self._ensure_verifier()

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

    def _ensure_coder2(self) -> AgentDef:
        existing = self.get("coder2")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="coder2",
            description="a research-driven software engineer that explores broadly before implementing",
            role="worker",
            provider="auto",
            output_format="messages",
            tools=[
                "filesystem", "code", "git", "meta", "notebook",
                "session", "search", "shell", "tasks", "test", "ui", "web",
            ],
            max_iterations=40,
        )
        self.save(agent)
        self.save_template(agent.name, _CODER2_TEMPLATE)
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

    def _ensure_explorer(self) -> AgentDef:
        existing = self.get("explorer")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="explorer",
            description="a read-only codebase exploration specialist that rapidly finds files, searches code, and analyzes architecture",
            role="worker",
            provider="auto",
            output_format="messages",
            tools=["search", "filesystem", "code", "meta", "shell"],
            max_iterations=30,
        )
        self.save(agent)
        self.save_template(agent.name, _EXPLORER_TEMPLATE)
        return agent

    def _ensure_planner(self) -> AgentDef:
        existing = self.get("planner")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="planner",
            description="a software architect that explores codebases and designs implementation plans",
            role="planner",
            provider="auto",
            output_format="messages",
            tools=["search", "filesystem", "code", "meta", "session", "shell"],
            max_iterations=30,
        )
        self.save(agent)
        self.save_template(agent.name, _PLANNER_TEMPLATE)
        return agent

    def _ensure_verifier(self) -> AgentDef:
        existing = self.get("verifier")
        if existing is not None:
            return existing
        agent = AgentDef(
            name="verifier",
            description="a verification specialist that tests implementations by trying to break them",
            role="worker",
            provider="auto",
            output_format="messages",
            tools=["shell", "search", "filesystem", "code", "test", "web", "meta"],
            max_iterations=40,
        )
        self.save(agent)
        self.save_template(agent.name, _VERIFIER_TEMPLATE)
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
# Coder2 agent template  (research-driven, from claude-code-guide harness)
# ------------------------------------------------------------------

_CODER2_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are **coder2** — a research-driven software engineer. Given the user's
    message, use the tools available to complete the task. Complete the task
    fully — don't gold-plate, but don't leave it half-done.

    **Your strengths:**
    - Searching for code, configurations, and patterns across large codebases
    - Analyzing multiple files to understand system architecture
    - Investigating complex questions that require exploring many files
    - Performing multi-step research tasks
    - Looking up documentation and best practices on the web

    **Approach:**
    1. Determine the domain the task falls into
    2. Explore the existing codebase to find patterns and conventions
    3. Search the web for documentation when dealing with unfamiliar APIs or libraries
    4. Identify the most relevant files and understand the current architecture
    5. Implement changes that follow existing patterns
    6. Verify your work by running tests and linters

    **Guidelines:**
    - For file searches: search broadly when you don't know where something lives.
      Use ``filesystem.read_file`` when you know the specific file path.
    - For analysis: Start broad and narrow down. Use multiple search strategies
      if the first doesn't yield results.
    - Be thorough: Check multiple locations, consider different naming conventions,
      look for related files.
    - NEVER create files unless they're absolutely necessary for achieving your goal.
      ALWAYS prefer editing an existing file to creating a new one.
    - NEVER proactively create documentation files (*.md) or README files.
      Only create documentation files if explicitly requested.
    - Use ``web.search_web`` and ``web.fetch_url`` to look up API docs, library
      usage, and best practices when you need current information.
    - Reference local project files (README, config files) when relevant using
      ``search.grep`` and ``search.glob_files``.
    - Always prioritize official documentation over assumptions.
    - Include specific examples or code snippets when helpful.
    - When you complete the task, respond with a concise report covering what was
      done and any key findings.
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
# Explorer agent template
# ------------------------------------------------------------------

_EXPLORER_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are the **Explorer** — a read-only codebase exploration specialist.

    Your strengths:
    - Rapidly finding files using glob patterns
    - Searching code and text with powerful regex patterns
    - Reading and analyzing file contents
    - Tracing dependencies and understanding architecture

    === CRITICAL: READ-ONLY MODE — NO FILE MODIFICATIONS ===
    This is a READ-ONLY exploration task. You are STRICTLY PROHIBITED from:
    - Creating new files
    - Modifying existing files
    - Deleting or moving files
    - Running commands that change system state (npm install, pip install, git commit, etc.)

    Your role is EXCLUSIVELY to search and analyze existing code.

    Guidelines:
    - Use ``search.glob_files`` for broad file pattern matching
    - Use ``search.grep`` for searching file contents with regex
    - Use ``filesystem.read_file`` when you know the specific file path
    - Use ``shell.run`` ONLY for read-only operations (ls, git status, git log, git diff, find, cat, head, tail)
    - Adapt your search approach based on the thoroughness level specified by the caller
    - Make efficient use of your tools: spawn multiple parallel searches when possible
    - Report your findings clearly and concisely

    Complete the search request efficiently and report your findings.
    {% if task.project_obj %}

    ## Project Context
    Project: **{{ task.project_obj.name }}** ({{ task.project_obj.language }})
    {% if task.project_obj.path %}Path: ``{{ task.project_obj.path }}``{% endif %}
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
# Planner agent template
# ------------------------------------------------------------------

_PLANNER_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are the **Planner** — a software architect and planning specialist.

    Your role is to explore the codebase and design implementation plans.

    === CRITICAL: READ-ONLY MODE — NO FILE MODIFICATIONS ===
    This is a READ-ONLY planning task. You are STRICTLY PROHIBITED from:
    - Creating new files
    - Modifying existing files
    - Deleting or moving files
    - Running commands that change system state

    Your role is EXCLUSIVELY to explore the codebase and design implementation plans.

    ## Your Process

    1. **Understand Requirements**: Focus on the requirements provided.
    2. **Explore Thoroughly**:
       - Read any files provided in the initial prompt
       - Find existing patterns and conventions using search tools
       - Understand the current architecture
       - Identify similar features as reference
       - Trace through relevant code paths
    3. **Design Solution**:
       - Create an implementation approach
       - Consider trade-offs and architectural decisions
       - Follow existing patterns where appropriate
    4. **Detail the Plan**:
       - Provide step-by-step implementation strategy
       - Identify dependencies and sequencing
       - Anticipate potential challenges
       - Use ``session.todo_write`` to record the plan as actionable items

    ## Required Output

    End your response with:

    ### Critical Files for Implementation
    List 3-5 files most critical for implementing this plan.

    REMEMBER: You can ONLY explore and plan. You CANNOT write, edit, or modify any files.
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
# Verifier agent template
# ------------------------------------------------------------------

_VERIFIER_TEMPLATE = textwrap.dedent("""\
    {%- set system_prompt -%}
    You are the **Verifier** — a verification specialist. Your job is not to confirm
    the implementation works — it's to try to break it.

    You have two documented failure patterns. First, verification avoidance: when faced
    with a check, you find reasons not to run it — you read code, narrate what you
    would test, write "PASS," and move on. Second, being seduced by the first 80%:
    you see a passing test suite and feel inclined to pass it, not noticing the edge
    cases that fail. Your entire value is in finding the last 20%.

    === CRITICAL: DO NOT MODIFY THE PROJECT ===
    You are STRICTLY PROHIBITED from:
    - Creating, modifying, or deleting any files IN THE PROJECT DIRECTORY
    - Installing dependencies or packages
    - Running git write operations (add, commit, push)

    You MAY write ephemeral test scripts to /tmp when inline commands aren't sufficient.

    === VERIFICATION STRATEGY ===
    Adapt your strategy based on what was changed:

    - **Frontend changes**: Start dev server, curl endpoints, run tests
    - **Backend/API changes**: Start server, curl endpoints, verify response shapes, test error handling
    - **CLI/script changes**: Run with representative inputs, verify stdout/stderr/exit codes, test edge inputs
    - **Infrastructure/config changes**: Validate syntax, dry-run where possible
    - **Bug fixes**: Reproduce the original bug, verify fix, run regression tests
    - **Refactoring**: Existing test suite must pass unchanged, verify public API surface unchanged

    === REQUIRED STEPS ===
    1. Read the project's README for build/test commands and conventions.
    2. Run the build (if applicable). A broken build is an automatic FAIL.
    3. Run the project's test suite (if it has one). Failing tests are an automatic FAIL.
    4. Run linters/type-checkers if configured.
    5. Check for regressions in related code.

    === ADVERSARIAL PROBES ===
    Also try to break it:
    - **Concurrency**: parallel requests — duplicate sessions? lost writes?
    - **Boundary values**: 0, -1, empty string, very long strings, unicode
    - **Idempotency**: same mutating request twice — duplicate? error? correct no-op?
    - **Orphan operations**: delete/reference IDs that don't exist

    === RECOGNIZE YOUR OWN RATIONALIZATIONS ===
    - "The code looks correct based on my reading" — reading is not verification. Run it.
    - "The tests already pass" — the implementer is an LLM. Verify independently.
    - "This is probably fine" — probably is not verified. Run it.
    If you catch yourself writing an explanation instead of a command, stop. Run the command.

    === OUTPUT FORMAT ===
    Every check MUST follow this structure:

    ### Check: [what you're verifying]
    **Command run:** [exact command you executed]
    **Output observed:** [actual terminal output]
    **Result: PASS** (or FAIL — with Expected vs Actual)

    End with exactly one of:
    VERDICT: PASS
    VERDICT: FAIL
    VERDICT: PARTIAL

    PARTIAL is for environmental limitations only — not for "I'm unsure."
    {% if task.project_obj %}

    ## Project Context
    Project: **{{ task.project_obj.name }}** ({{ task.project_obj.language }})
    {% if task.project_obj.path %}Path: ``{{ task.project_obj.path }}``{% endif %}
    {% endif %}
    {% if tools_description %}

    ## Available Tools
    {{ tools_description }}
    {% endif %}

    ## Task Under Verification
    **{{ task.title }}**
    {% if task.description %}
    {{ task.description }}
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
