Agent Resolution
================

This document describes how an agent name string becomes a fully hydrated
list of messages ready for the LLM.


Agent definitions on disk
-------------------------

Each agent lives in its own directory under ``assai/agents/``::

    assai/agents/
    ├── default/
    │   ├── definition.json
    │   └── system.j2
    ├── thinker/
    │   ├── definition.json
    │   └── system.j2
    ├── coder/
    │   ├── definition.json
    │   └── system.j2
    └── ...

``definition.json``
    JSON object matching ``AgentDef`` fields.  Minimal example::

        {
            "name": "default",
            "description": "general-purpose assistant",
            "role": "system",
            "provider": "auto",
            "output_format": "messages",
            "tools": ["ui"]
        }

``system.j2``
    Jinja2 template that produces either a JSON message array (when
    ``output_format == "messages"``) or a plain system-prompt string
    (when ``output_format == "text"``).  Available template variables
    are listed in the `Hydration`_ section below.

Workspace agents in ``<workspace>/agents/`` shadow builtins — if both
define the same agent name, the workspace version wins.


AgentDef
--------

Dataclass defined in ``assai/core/agent_store.py``.  Key fields:

================= ====================================================
Field             Description
================= ====================================================
``name``          Unique identifier (matches directory name).
``description``   Human-readable summary.
``role``          Default message role (usually ``"system"``).
``provider``      Provider name or ``"auto"``.
``output_format`` ``"messages"`` (template emits JSON array) or
                  ``"text"`` (template emits a system-prompt string).
``tools``         List of tool namespace strings (e.g. ``["filesystem",
                  "ui"]``).  Empty means no tools.
``compressor``    Name of the compressor agent for context trimming.
                  Empty string disables compression.
``max_iterations`` Max tool-call round-trips.
``model_overrides`` Dict of LLM parameter overrides.
``system_template`` Template filename (default ``system.j2``).
================= ====================================================


AgentStore
----------

``AgentStore`` (``assai/core/agent_store.py``) loads agent definitions:

``get(name) -> AgentDef | None``
    Tries ``<workspace>/agents/<name>/definition.json`` first, then
    falls back to the builtin ``assai/agents/<name>/definition.json``.

``read_template(name) -> str``
    Same resolution order for ``system.j2``.  If neither workspace nor
    builtin has a template for the given agent, falls back to the
    **default** agent's template.

``list() -> list[AgentDef]``
    Merges builtins and workspace agents; workspace wins on name
    collision.


Resolution pipeline
-------------------

When the orchestrator pops a task from the queue, it goes through four
stages to build the work dict that the worker receives.

::

    ┌──────────┐     ┌──────────────┐     ┌──────────────┐     ┌─────────┐
    │  Task    │ --> │ resolve_task │ --> │ hydrate_task │ --> │ _do_pop │
    │  (DB)    │     │              │     │              │     │  (work) │
    └──────────┘     └──────────────┘     └──────────────┘     └─────────┘


Stage 1: resolve_task
^^^^^^^^^^^^^^^^^^^^^

``resolve_task(task, config, chat, projects) -> dict``

Builds a ``resolved`` dict from the task's DB fields and file contents:

=================== =====================================================
Field               Source
=================== =====================================================
``id``              Task primary key.
``kind``            ``"llm_complete"`` or ``"tool_call"``.
``title``           Task title.
``agent``           Agent name string from the task.
``project``         Project name.
``spec_content``    Contents of ``task.spec_path`` (loaded from disk).
``messages``        If ``spec_path`` ends with ``conversation.json``,
                    parsed as a JSON message array.  Messages with role
                    ``tool_call`` or ``tool_result`` are filtered out.
``conversation``    Conversation ID (dirname of ``conversation.json``).
``project_obj``     Project config object.
``project_spec``    Contents of the project's ``spec.md`` if it exists.
=================== =====================================================


Stage 2: hydrate_task
^^^^^^^^^^^^^^^^^^^^^

``hydrate_task(agent, store, resolved, tools_description="") -> list[dict]``

Renders the agent's Jinja2 template with these variables:

=================== =====================================================
Variable            Value
=================== =====================================================
``agent``           The ``AgentDef`` instance.
``task``            The ``resolved`` dict from stage 1.
``messages``        ``resolved["messages"]`` (conversation history).
``project``         Project config object (or ``None``).
``spec``            Project spec string.
``tools_description`` Human-readable tool listing.
``datetime``        Current UTC timestamp.
=================== =====================================================

The ``output_format`` field controls how the rendered template is
interpreted:

``"messages"``
    The template must produce a valid JSON array of message objects
    (``[{"role": "system", "content": "..."}, ...]``).  This gives
    templates full control over message structure.

``"text"``
    The rendered string is wrapped as a single system message and the
    conversation history is appended::

        [{"role": "system", "content": rendered}] + messages


Stage 3: tool resolution
^^^^^^^^^^^^^^^^^^^^^^^^^

If ``agent_def.tools`` is non-empty, the orchestrator resolves tools:

1. ``tool_registry.mcp_definitions(namespaces=agent_def.tools)``
   filters registered ``ToolDef`` objects by namespace and returns
   OpenAI-style function definitions.

2. A human-readable ``tools_description`` string is built from the
   definitions (name, description, parameters) and passed into the
   Jinja2 template so agents can reference available tools in their
   system prompt.

3. The raw ``tool_defs`` list is included in the work dict so the
   worker can pass them to the LLM's tool-calling API.


Stage 4: _do_pop
^^^^^^^^^^^^^^^^^

``_do_pop()`` in ``assai/core/server.py`` ties everything together:

1. ``queue.pop(status=READY)`` — grab the next task.
2. Set status to ``IN_PROGRESS``.
3. If ``kind == "tool_call"``: load spec JSON and return early (no agent
   hydration needed).
4. ``resolve_task(task, ...)`` — build the resolved dict.
5. ``agent_store.get(agent_name)`` — load the ``AgentDef``.
6. Resolve tools via ``tool_registry.mcp_definitions()``.
7. ``hydrate_task(agent_def, ...)`` — render the template into messages.
8. Build the work dict::

       {
           "task_id": "...",
           "kind": "llm_complete",
           "messages": [...],         # hydrated messages
           "conversation": "...",
           "agent": "default",
           "compressor": "compressor",
           "tools": [...],            # OpenAI tool definitions (if any)
           "provider": {...},         # provider override (if non-default)
           "enable_thinking": true,   # (if set on the task)
           "project_path": "...",     # (if project has a path)
       }

9. If ``ext.injected_reasoning`` is present (emulated thinking flow),
   insert a system message with the prior reasoning right after the
   agent's system prompt.


Provider resolution
-------------------

``_resolve_provider_for_task(task, conv_id)`` determines which LLM
provider the worker should use:

1. Check the conversation metadata for a ``provider`` field.
2. If ``"auto"`` or missing: use ``ProviderScheduler.select("worker")``
   to pick the best provider for the worker role.
3. If a named provider: look it up via ``config.get_provider(name)``.
4. If the resolved provider matches ``config.active_provider()`` (the
   worker's default), return ``None`` — no override needed.
5. Otherwise return the full provider config as a dict so the worker
   can connect to the right LLM endpoint.
