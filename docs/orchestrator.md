Orchestrator
============

The orchestrator (``assai/orchestrator/server.py``) is the central coordinator
of the system.  It acquires workers, dispatches LLM calls via
``TaskGraph`` subclasses, streams results to the frontend, and keeps
the books.


Design philosophy
-----------------

1. **TaskGraph owns the agent flow.**  Each conversation endpoint
   instantiates a ``TaskGraph`` subclass (``ConverseGraph``,
   ``ThinkGraph``) that orchestrates the full agent pipeline:
   prepare the payload, dispatch to a worker, handle tool follow-ups,
   and persist the result.  The orchestrator just acquires a worker
   and iterates the graph.

2. **Separation of inference.**  The orchestrator prepares and records.
   Workers generate tokens and execute tools.  No model weights are
   loaded in the orchestrator process.

3. **SSE streaming end-to-end.**  Conversation endpoints return an SSE
   ``StreamingResponse`` directly.  Each ``TaskGraph.run()`` is an async
   generator that yields event dicts; the endpoint formats them as SSE
   frames.  No intermediate queues or futures.

4. **Load-balanced worker acquisition.**  The ``LoadBalancer`` manages
   registered workers.  ``lb.acquire()`` is an async context manager
   that waits for a free worker and auto-releases it when the graph
   completes.

5. **Files over blobs.**  Task specs and results are stored as JSON
   files on disk (``spec_path``, ``result_path``).  The queue holds
   pointers, not data.


What the orchestrator does
--------------------------

**Agent execution**
    For each incoming conversation (``/converse``, ``/think/converse``,
    ``/uber/converse``), the endpoint acquires a worker from the
    ``LoadBalancer`` and runs the appropriate ``TaskGraph``:

    ``POST /converse`` → ``ConverseGraph``
        Prepares the agent payload, dispatches to the worker, handles
        tool-call follow-ups, persists the assistant message.  Returns
        an SSE stream directly.

    ``POST /think/converse`` → ``ThinkGraph``
        Two-phase flow: dispatches the *thinker* agent (tokens streamed
        as ``reasoning`` events), then dispatches the main agent with
        reasoning injected (tokens streamed normally).  Tool follow-ups
        are handled in the reply phase.  Returns an SSE stream.

    ``POST /uber/converse`` → ``UberRouter`` + ``ConverseGraph``
        Routes the message to the best conversation via a lightweight
        LLM call (``UberRouter``), then launches a ``ConverseGraph``
        as a background ``asyncio.Task``.  Returns a JSON response
        with the conversation ID and stream ID (202).

**Conversation store**
    Owns the ``ChatStore``.  Creates conversations, appends user and
    assistant messages, manages metadata (provider, agent, project).

**Stream relay**
    ``TaskGraph`` pushes events to the ``StreamTracker`` during
    execution.  UI clients can subscribe via ``GET /stream/<stream_id>``
    for real-time updates or reconnection.

**Background task reaping**
    The ``Orchestrator`` class runs a background thread that detects
    tasks stuck in ``IN_PROGRESS`` beyond a timeout and retries or
    fails them.  Conversation and think tasks (which use ``TaskGraph``
    and don't go through the queue) are skipped.

**Project and agent management**
    CRUD endpoints for projects, agents, providers, specs, worktrees.
    Agent definitions can be overridden per-workspace.

**Real-time UI updates**
    SocketIO broadcasts task lists, system status, and event history
    every ~2 seconds.  Rooms per conversation for targeted tool events.


TaskGraph subclasses
--------------------

``assai/tasks/`` contains the concrete graph implementations:

======================== =========================================
Class                    Description
======================== =========================================
``ConverseGraph``        Single agent + tool-call follow-up loop.
``ThinkGraph``           Thinker → reply + tool loop.
``UberRouter``           Routes messages to conversations via a
                         direct LLM call (not a TaskGraph subclass).
======================== =========================================

The base ``TaskGraph`` (``assai/tasks/graph.py``) provides the building
blocks: ``prepare()``, ``dispatch()``, ``_run_with_tools()``, and
shared helpers for error events, done events, and chat persistence.

New graph types can be added by subclassing ``TaskGraph`` and
registering in ``assai/tasks/registry.py``.


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
Converse                          ``POST /converse`` — SSE stream via
                                  ``ConverseGraph``.
Uber routing                      ``POST /uber/converse`` — route + converse
                                  via ``UberRouter`` + ``ConverseGraph``.
Think-then-generate               ``POST /think/converse`` — SSE stream via
                                  ``ThinkGraph``.
History                           Message history for a conversation.
Work results                      ``POST /work/result/<id>`` — legacy task
                                  result handler for standalone tasks.
Streaming                         ``GET /stream/<stream_id>`` (orch → UI
                                  SSE).
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

``join_conversation`` / ``leave_conversation``
    Room-based subscription for tool start/end events.

``worker_heartbeat``
    Workers send periodic telemetry over WebSocket.

Background emit loop
    Every ~2 seconds broadcasts the full task list, queue status
    counts, and recent events to all connected clients.
