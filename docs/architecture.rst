Architecture Overview
=====================

Açaí is split into a **Python backend** (FastAPI + worker processes) and a
**React frontend** (Vite + Chakra UI v3).  They communicate over REST, SSE
(server-sent events for streaming LLM output), and Socket.IO (real-time
status, telemetry, and log updates).

High-level diagram
------------------

::

   ┌─────────────────────────────────────────────────────────┐
   │                     React UI (Vite)                     │
   │  HashRouter · Chakra UI v3 · Socket.IO client           │
   └──────────┬──────────────────────────────┬───────────────┘
              │  REST / SSE                  │  Socket.IO
              ▼                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │               Orchestrator  (FastAPI)                   │
   │  Routes · Agent store · Chat store · Knowledge store    │
   │  Project store · Skill store · Tool registry            │
   │  Queue integration · Git sync · Updater                 │
   └──────┬──────────┬──────────────┬────────────────────────┘
          │          │              │
          ▼          ▼              ▼
   ┌──────────┐ ┌──────────┐ ┌───────────────┐
   │  Worker   │ │  Queue   │ │  LLM Provider │
   │ (sandbox) │ │ (SQLite) │ │  (vLLM, API)  │
   └──────────┘ └──────────┘ └───────────────┘

Backend packages
----------------

``acai/orchestrator/``
   FastAPI application, HTTP routes, stores (chat, agents, projects,
   knowledge, queue integration), tool registry, skill store, git sync,
   and the updater.

``acai/worker/``
   Worker HTTP application that streams LLM completions and runs sandboxed
   tool execution (Docker, Podman, Bubblewrap, or Nsjail).

``acai/tasks/``
   Task-graph implementations: ``DynamicGraph``, converse, think, uber,
   scribe, and the full node type registry (``nodes.py``).

``acai/tools/``
   Tool implementations exposed to agents — file I/O, shell, web search,
   code execution, skills management, and more.

``acai/queue/``
   SQLAlchemy-backed work queue for asynchronous task dispatch.

``acai/agents/``
   Built-in agent definitions (``definition.json``) and Jinja2 system
   prompt templates (``system.j2``).

``acai/models/``
   Pluggable model stacks — vLLM, HuggingFace pipelines, text-to-image,
   text-to-speech, depth estimation, and others.

``acai/scheduler/``
   Provider selection and load balancing across LLM backends.

``acai/cli/``
   The ``acai`` command-line interface: ``orchestrator``, ``worker``,
   ``uber``, ``serve``, ``mcp``, ``knowledge`` sub-commands, and more.

``acai/plugins/``
   Optional plugin loading mechanism.

Frontend stack
--------------

============  =====================================
Technology    Purpose
============  =====================================
React 18+     Component framework
Vite          Dev server and production bundler
Chakra UI v3  Component library and theming
React Router  Hash-based client-side routing
Socket.IO     Real-time events and telemetry
React Flow    Visual workflow editor (node graph)
============  =====================================

The UI dev server (port **8081**) proxies ``/api/agent`` and ``/socket.io``
to the orchestrator (port **5050**).  In production the UI is built into
``acai/ui/dist/`` and served by the orchestrator directly.

API layer
---------

All frontend API calls go through ``acai/ui/src/services/api.ts``, which
provides:

* A generic ``request<T>()`` JSON helper with error handling.
* An ``SSEStream`` class that parses POST response bodies as server-sent
  events (the native ``EventSource`` API only supports GET).
* Typed functions grouped by domain: conversations, tasks, projects,
  providers, agents, workflows, skills, knowledge, config, and system
  status.

Data flow
---------

1. The user interacts with the React UI.
2. REST calls reach the **Orchestrator**, which resolves the target agent,
   prepares the LLM payload, and dispatches it to a **Worker** (or an
   external LLM provider).
3. The Worker streams tokens back over SSE.  Tool calls are executed
   in-process or inside a **sandbox** container, and results are appended
   to the conversation before re-calling the LLM.
4. Final responses are persisted in the **Chat store** and pushed to the
   frontend over Socket.IO for live updates.

Skill system
------------

Users (or agents) can create ad-hoc tools called *skills*.  A skill lives
under ``workspace/skills/<namespace>/<skill_name>/`` and contains:

* ``tool.json`` — MCP tool definition (description, parameters).
* ``run.py`` — Python script executed in a subprocess.
* ``README.md`` — Human-readable documentation.

Skills are discovered at startup by the ``SkillStore``, registered into the
``ToolRegistry``, and can be selectively exposed to individual agents via
the ``skills.<namespace>`` namespace prefix.

Workflow engine
---------------

The visual workflow editor builds a directed graph of typed nodes
(Start, Agent Call, Accumulate, Condition, Tool Follow-Up, Set Variable,
Get Variable, etc.).  At runtime the ``DynamicGraph`` executor walks the
graph following execution pins and resolving data pins, streaming events
back to the UI in real time.
