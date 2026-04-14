Orchestrator
============

The orchestrator (``assai/core/server.py``) is the central coordinator
of the system.  It never calls the LLM itself — it manages the queue,
relays streams, and keeps the books.


Design philosophy
-----------------

1. **The queue is the integration bus.**  Everything — conversations,
   tool calls, thinking, multi-agent routing — goes through the same
   SQLite work queue.  Workers pop tasks; the orchestrator pushes them.

2. **Separation of inference.**  The orchestrator prepares and records.
   Workers generate tokens and execute tools.  No model weights are
   loaded in the orchestrator process.

3. **Schedulers own the task graph.**  Each task ``kind`` maps to a
   ``Scheduler`` subclass (an async generator) that yields ``WorkStep``
   objects and receives ``StepResult`` objects.  The scheduler decides
   what to hydrate, how to stream, when to follow up with tool calls,
   and how to compose multi-step workflows — the orchestrator just
   drives the generator loop.

4. **Streaming as a first-class relay.**  Token streams flow from
   workers through the orchestrator to the UI.  The scheduler driver
   bridges ``asyncio.Future`` objects with synchronous stream events
   so each ``yield`` in the scheduler blocks until the worker finishes
   that step.

5. **Files over blobs.**  Task specs and results are stored as JSON
   files on disk (``spec_path``, ``result_path``).  The queue holds
   pointers, not data.


What the orchestrator does
--------------------------

**Queue management**
    Owns the ``WorkQueue`` (SQLite).  Pushes tasks, pops and hydrates
    them for workers, tracks status transitions, handles retries, and
    reaps stuck tasks.

**Conversation store**
    Owns the ``ChatStore``.  Creates conversations, appends user and
    assistant messages, manages metadata (provider, agent, project).

**Scheduler driver**
    The ``drive_scheduler()`` async function is the core execution loop.
    For each incoming conversation (``/converse``, ``/think/converse``,
    ``/uber/converse``), the endpoint creates a root task, instantiates
    the appropriate ``Scheduler`` via the registry, and spawns the
    driver as an ``asyncio.Task``.

    The driver:

    1. Calls ``scheduler.run(task, conversation)`` to get the async
       generator.
    2. Receives each ``WorkStep`` yielded by the scheduler.
    3. Writes the pre-hydrated payload to disk and pushes a sub-task
       tagged with ``ext.scheduler_driven=True``.
    4. Registers an ``asyncio.Future`` keyed by sub-task ID.
    5. When stream events arrive at ``/stream/push``, the
       ``_resolve_step()`` function fills the future with a
       ``StepResult``.
    6. The driver ``asend()``s the result back to the generator.
    7. When the generator exhausts, the driver appends the final
       assistant message to chat and marks the root task completed.

**Task hydration**
    Scheduler-driven sub-tasks carry a pre-hydrated payload (written by
    the scheduler), so ``_do_pop()`` returns it directly.  Legacy
    ``llm_complete`` tasks (e.g. internal routing calls from
    ``UberScheduler``) still go through ``resolve_task`` →
    ``hydrate_task`` at pop time.  See ``docs/agent-resolution.md``.

**Stream relay**
    Receives NDJSON from workers at ``POST /stream/push``, fans out
    events to UI subscribers via ``StreamTracker`` → SSE.
    See ``docs/streaming.md``.

**Background chaining**
    The ``Orchestrator`` class runs a background thread that polls for
    completed tasks and chains tool-call pipelines when the streaming
    path didn't handle them (fallback for non-streaming or race
    conditions).

**Project and agent management**
    CRUD endpoints for projects, agents, providers, specs, worktrees.
    Agent definitions can be overridden per-workspace.

**Real-time UI updates**
    SocketIO broadcasts task lists, system status, and event history
    every ~2 seconds.  Rooms per conversation for targeted tool events.


Scheduler registry
------------------

``assai/scheduler/registry.py`` maps ``Task.kind`` to concrete
``Scheduler`` subclasses:

======================== =========================================
Kind                     Scheduler
======================== =========================================
``converse``             ``ConversationScheduler``
``llm_complete``         ``ConversationScheduler``
``think``                ``ThinkScheduler``
======================== =========================================

The ``ConversationScheduler`` handles hydration, tool-call follow-ups,
and streaming for standard conversations.  The ``ThinkScheduler``
composes two ``ConversationScheduler`` steps (think then reply) to
emulate reasoning for models that lack native thinking.

New task kinds can be added by calling ``registry.register(kind, cls)``.


What the orchestrator does NOT do
---------------------------------

- Call the LLM or load model weights.
- Execute tool calls against the filesystem or external services.
- Decide model parameters (temperature, top-p) — those live in agent
  definitions and provider configs.
- Own the frontend implementation — it only serves the API, SSE, and
  WebSocket endpoints.


Endpoint groups
---------------

================================= =========================================
Group                             Description
================================= =========================================
Conversations                     CRUD, context stats, inflight check.
Converse                          ``POST /converse`` — queue a conversation
                                  via ``ConversationScheduler``.
Uber routing                      ``POST /uber/converse`` — route to the
                                  best conversation, then converse via
                                  ``ConversationScheduler``.
Think-then-generate               ``POST /think/converse`` — two-step
                                  reasoning via ``ThinkScheduler``.
History                           Message history for a conversation.
Worker contract                   ``GET /work/pop``,
                                  ``POST /work/result/<id>``.
Streaming                         ``POST /stream/push`` (worker → orch),
                                  ``GET /stream/<conv>`` (orch → UI SSE).
Tasks                             CRUD, tree view.
Providers                         CRUD, activate.
Projects                          CRUD, git integration.
Agents                            CRUD, template editing, reset to builtin.
Tools                             List available tool namespaces.
Status & events                   System health, event bus.
================================= =========================================


SocketIO
--------

The orchestrator runs a SocketIO server for real-time updates:

``work_pop``
    Workers can pop tasks over WebSocket instead of HTTP polling.
    Same ``_do_pop()`` logic, lower latency.

``join_conversation`` / ``leave_conversation``
    Room-based subscription for tool start/end events.

Background emit loop
    Every ~2 seconds broadcasts the full task list, queue status
    counts, and recent events to all connected clients.
