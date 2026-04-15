Streaming Architecture
======================

This document describes how LLM output reaches the frontend in real time,
and how multi-step flows (tool calls, emulated thinking) compose through
a single stream.


Overview
--------

::

  ┌──────────┐   SSE    ┌──────────┐  SSE/aiohttp   ┌──────────┐  SSE   ┌──────────┐
  │  LLM     │ -------> │  Worker  │ -------------> │  Server  │ -----> │ Frontend │
  │ (vLLM)   │          │          │                │  (orch)  │        │  (React) │
  └──────────┘          └──────────┘                └──────────┘        └──────────┘
       /llm/complete       TaskGraph.dispatch()        /converse (SSE)
       (SSE response)      (AsyncSSEIterator)          or /stream/<id>


Components
----------

**Worker** (``assai/core/worker.py``)
    Hosts the ``POST /worker/llm/complete`` endpoint that calls the
    local LLM and streams SSE events.  Also hosts ``POST /tools/call``
    for tool execution.  Workers register with the ``LoadBalancer`` and
    are acquired by the orchestrator on demand.

**TaskGraph** (``assai/core/graph.py``)
    The orchestrator-side execution engine.  ``dispatch()`` opens an
    ``AsyncSSEIterator`` to the worker's ``/llm/complete`` endpoint,
    consumes SSE events, pushes them to the ``StreamTracker``, and
    yields them as dicts.  ``_run_with_tools()`` wraps dispatch with
    a tool-call follow-up loop.

**StreamTracker** (``assai/core/stream.py``)
    A thread-safe pub-sub hub living in the orchestrator process.  Each
    stream (keyed by conversation ID) has a list of subscriber
    ``Queue`` objects.  ``push(stream_id, event)`` fans out to all
    subscribers.  ``subscribe(stream_id)`` returns a new queue.

**SSE endpoint** (``GET /stream/<stream_id>`` in ``assai/core/server.py``)
    UI clients can subscribe to ongoing streams via this endpoint for
    reconnection or background-task monitoring.  The endpoint subscribes
    to the ``StreamTracker``, enters a blocking loop on the queue, and
    yields each event as an SSE frame.

**Direct SSE endpoints** (``POST /converse``, ``POST /think/converse``)
    These return an SSE ``StreamingResponse`` directly.  The response
    iterates ``TaskGraph.run()`` and formats each event as an SSE frame.
    The frontend consumes the stream from the POST response body using
    a custom ``SSEStream`` class (since ``EventSource`` only supports
    GET).


Event types
-----------

============== ====================================================
Event          Description
============== ====================================================
``meta``       First event in a direct SSE stream.  Contains the
               conversation ID.
``token``      A chunk of assistant text.
``reasoning``  A chunk of reasoning/thinking text.
``tool_call_delta``  Incremental tool-call arguments (streaming).
``tool_start`` A tool execution has begun.
``tool_end``   A tool execution has finished.
``done``       The graph completed.  Closes the SSE connection.
``error``      An error occurred.  Closes the SSE connection.
============== ====================================================


Normal (single-call) flow
-------------------------

::

  Frontend                 Server                    Worker           LLM
     │                       │                         │               │
     │  POST /converse       │                         │               │
     │ ───────────────────>  │                         │               │
     │                       │  lb.acquire() worker    │               │
     │                       │  ConverseGraph.run()    │               │
     │                       │                         │               │
     │  SSE: meta            │                         │               │
     │ <───────────────────  │  {conversation: id}     │               │
     │                       │                         │               │
     │                       │  graph.prepare()        │               │
     │                       │  graph.dispatch()       │               │
     │                       │  ↳ POST /llm/complete   │               │
     │                       │ ─────────────────────>  │               │
     │                       │                         │  call LLM     │
     │                       │                         │ ────────────> │
     │                       │                         │               │
     │                       │  AsyncSSEIterator       │  SSE: token   │
     │  SSE: token           │ <─────────────────────  │ <──────────── │
     │ <───────────────────  │  tracker.push("token")  │               │
     │                       │                         │               │
     │                       │                         │  SSE: done    │
     │                       │ <─────────────────────  │ <──────────── │
     │                       │  chat.append(assistant) │               │
     │  SSE: done            │  tracker.push("done")   │               │
     │ <───────────────────  │                         │               │
     │                       │  lb.release() worker    │               │
     │  close connection     │                         │               │


Emulated thinking flow
----------------------

The ``ThinkGraph`` (``assai/tasks/think.py``) chains two LLM calls
through the same worker.  The worker remains completely unaware — it
just executes one call at a time.

::

  Frontend                 Server / ThinkGraph        Worker
     │                       │                          │
     │  POST /think/converse │                          │
     │ ───────────────────>  │                          │
     │                       │  lb.acquire() worker     │
     │                       │  ThinkGraph.run()        │
     │                       │                          │
     │  SSE: meta            │                          │
     │ <───────────────────  │                          │
     │                       │                          │
     │                       │       ┌─────────────────────────────┐
     │                       │       │  Phase 1: Think             │
     │                       │       └─────────────────────────────┘
     │                       │  prepare("thinker", work)│
     │                       │  dispatch(payload,       │
     │                       │    stream_mode="reasoning")
     │                       │  ↳ POST /llm/complete    │
     │                       │ ─────────────────────>   │
     │                       │                          │
     │                       │  SSE: token from worker  │
     │  SSE: reasoning       │ <────────────────────────│
     │ <───────────────────  │  (remapped to reasoning) │
     │                       │                          │
     │                       │  SSE: done               │
     │                       │ <────────────────────────│
     │                       │  Acc captures text       │
     │                       │                          │
     │                       │       ┌─────────────────────────────┐
     │                       │       │  Phase 2: Reply             │
     │                       │       └─────────────────────────────┘
     │                       │  prepare(agent, work,    │
     │                       │    reasoning=acc.text)    │
     │                       │  _run_with_tools(payload) │
     │                       │  ↳ POST /llm/complete    │
     │                       │ ─────────────────────>   │
     │                       │                          │
     │  SSE: token           │ <────────────────────────│
     │ <───────────────────  │                          │
     │                       │                          │
     │                       │  SSE: done               │
     │                       │ <────────────────────────│
     │                       │  chat.append(assistant)  │
     │  SSE: done            │  tracker.push("done")    │
     │ <───────────────────  │                          │
     │                       │  lb.release() worker     │
     │  close connection     │                          │

Key points:

1. The **worker is unchanged** — it receives a payload, calls the LLM,
   streams the result.  It does this twice (once for the thinker, once
   for the main agent) without knowing they are related.

2. The **ThinkGraph** owns the composition:

   - Phase 1 dispatches with ``stream_mode="reasoning"`` — the graph
     remaps ``token`` events to ``reasoning`` events for the frontend.
   - ``Acc`` accumulates the thinker's text.
   - Phase 2 prepares the main agent with reasoning injected, then
     enters the standard tool-call follow-up loop via
     ``_run_with_tools()``.

3. Both phases run on the **same worker** within a single
   ``lb.acquire()`` context — no worker release between phases.

4. The SSE connection is **per-request**, not per-task.  Events from
   both phases flow through the same HTTP response.


Native thinking
---------------

When the model supports reasoning natively (e.g. via ``<think>`` tags),
no two-phase graph is needed.  The frontend calls ``POST /converse``
with ``enable_thinking: true``.  The LLM produces interleaved
``reasoning`` and ``token`` events which the worker relays unchanged.
The ``ConverseGraph`` forwards them as-is.


Tool-call flow
--------------

Tool calls are handled by ``TaskGraph._run_with_tools()``.  When the
worker returns ``tool_call_delta`` events, ``Acc`` accumulates them.
After the stream completes, if tool calls are present:

1. For each tool call, push a ``tool_start`` event and dispatch the
   tool via ``POST /tools/call`` to the worker.
2. Push ``tool_end`` events and append ``tool_call`` / ``tool_result``
   messages to chat history.
3. Build follow-up messages (original + assistant tool calls + tool
   results) and dispatch a new LLM call.
4. Repeat until no more tool calls are returned.

The worker's ``/tools/call`` endpoint runs tool functions in a thread
pool (``asyncio.to_thread``) so blocking tools (like ``ui.toast``
which calls back to the orchestrator) don't deadlock the event loop.
