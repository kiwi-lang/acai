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

Dataclass defined in ``assai/orchestrator/agent_store.py``.  Key fields:

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

``AgentStore`` (``assai/orchestrator/agent_store.py``) loads agent definitions:

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


Resolution in TaskGraph
-----------------------

When a conversation endpoint receives a request, the ``TaskGraph``
handles agent resolution through its ``prepare()`` method.

::

    ┌──────────────┐     ┌──────────────┐     ┌───────────────┐
    │ TaskGraph     │ --> │  prepare()   │ --> │ Dispatch-ready │
    │ (agent name) │     │              │     │   payload      │
    └──────────────┘     └──────────────┘     └───────────────┘


prepare()
^^^^^^^^^

``TaskGraph.prepare(agent_name, work, reasoning=None) -> dict``

Builds a dispatch-ready payload in a single step:

1. ``agent_store.get(agent_name)`` — load the ``AgentDef``.  Falls
   back to ``"default"`` if not found.
2. Resolve tools via ``tool_registry.mcp_definitions(namespaces=...)``.
3. Build a human-readable ``tools_description`` string.
4. Render the Jinja2 template with these variables:

   =================== =====================================================
   Variable            Value
   =================== =====================================================
   ``agent``           The ``AgentDef`` instance.
   ``task``            The ``work`` dict (contains messages, project, etc.).
   ``messages``        Conversation history from ``work["messages"]``.
   ``project``         Project config object (or ``None``).
   ``spec``            Project spec string.
   ``tools_description`` Human-readable tool listing.
   ``datetime``        Current UTC timestamp.
   =================== =====================================================

5. If ``reasoning`` is provided (from a prior think phase), inject a
   system message with the reasoning after the agent's system prompt.
6. Resolve provider: check conversation metadata for a ``provider``
   field.  If non-default, include the provider config in the payload.
7. Return the final payload dict::

       {
           "messages": [...],             # hydrated messages
           "conversation": "...",
           "agent": "default",
           "compressor": "compressor",
           "tools": [...],                # OpenAI tool definitions (if any)
           "provider": {...},             # provider override (if non-default)
           "enable_thinking": true,       # (if set on the work)
           "project_path": "...",         # (if project has a path)
       }


Output formats
--------------

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


Tool resolution
---------------

If ``agent_def.tools`` is non-empty:

1. ``tool_registry.mcp_definitions(namespaces=agent_def.tools)``
   filters registered ``ToolDef`` objects by namespace and returns
   OpenAI-style function definitions.

2. A human-readable ``tools_description`` string is built from the
   definitions (name, description, parameters) and passed into the
   Jinja2 template so agents can reference available tools in their
   system prompt.

3. The raw ``tool_defs`` list is included in the payload so the worker
   can pass them to the LLM's tool-calling API.


Provider resolution
-------------------

``TaskGraph.prepare()`` determines the LLM provider:

1. Check the conversation metadata for a ``provider`` field.
2. If ``"auto"`` or missing: use ``ProviderScheduler.select("worker")``
   to pick the best provider for the worker role.
3. If a named provider: look it up via ``config.get_provider(name)``.
4. If the resolved provider matches ``config.active_provider()`` (the
   worker's default), return ``None`` — no override needed.
5. Otherwise return the full provider config as a dict so the worker
   can connect to the right LLM endpoint.
