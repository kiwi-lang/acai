Streaming Architecture
======================

This document describes how LLM output reaches the frontend in real time,
and how multi-step flows (tool calls, emulated thinking) compose through
a single stream.


Overview
--------

::

  ┌──────────┐   SSE    ┌─────────────┐  NDJSON   ┌──────────┐  SSE   ┌──────────┐
  │  LLM     │ -------> │   Worker    │ --------> │  Server  │ -----> │ Frontend │
  │ (vLLM)   │          │  (poller)   │           │  (orch)  │        │  (React) │
  └──────────┘          └─────────────┘           └──────────┘        └──────────┘
       /llm/complete          /stream/push            /stream/<conv>
       (SSE response)         (chunked POST)          (SSE response)


Components
----------

**Worker** (``assai/core/worker.py``)
    Pops a task from the orchestrator, POSTs it to the local LLM endpoint
    (``/llm/complete``), and consumes the SSE response line-by-line.  Each
    event is re-packaged as an NDJSON line and relayed to the orchestrator
    via a single chunked ``POST /stream/push``.  The worker is stateless
    and knows nothing about thinking, agents, or composition.

**StreamTracker** (``assai/core/stream.py``)
    A thread-safe pub-sub hub living in the orchestrator process.  Each
    conversation has a list of subscriber ``Queue`` objects.
    ``push(conversation, event)`` fans out to all subscribers.
    ``subscribe(conversation)`` returns a new queue.

**SSE endpoint** (``GET /stream/<conv_id>`` in ``assai/core/server.py``)
    The frontend opens an ``EventSource`` to this URL.  The endpoint
    subscribes to the ``StreamTracker``, enters a blocking loop on the
    queue, and yields each event as an SSE frame.  The loop breaks on
    ``done`` or ``error``, then unsubscribes.

**Scheduler driver** (``drive_scheduler`` in ``assai/core/server.py``)
    Bridges async scheduler generators with the synchronous stream
    push endpoint.  Each ``WorkStep`` yielded by a scheduler creates
    a sub-task and an ``asyncio.Future``.  When stream events arrive
    at ``/stream/push``, the ``_resolve_step()`` function accumulates
    text, reasoning, and tool-call deltas, forwards events to the
    ``StreamTracker`` according to the step's ``stream_mode``, and
    resolves the future on ``done`` or ``error``.


Event types
-----------

============== ====================================================
Event          Description
============== ====================================================
``token``      A chunk of assistant text.
``reasoning``  A chunk of reasoning/thinking text.
``tool_call_delta``  Incremental tool-call arguments (streaming).
``tool_start`` A tool execution has begun.
``tool_end``   A tool execution has finished.
``done``       The LLM call completed.  Closes the SSE connection.
``error``      An error occurred.  Closes the SSE connection.
============== ====================================================


Normal (single-call) flow
-------------------------

::

  Frontend                 Server                    Worker           LLM
     │                       │                         │               │
     │  POST /converse       │                         │               │
     │ ───────────────────>  │  create root task       │               │
     │                       │  (IN_PROGRESS)          │               │
     │                       │  drive_scheduler()      │               │
     │                       │  ↳ scheduler.run()      │               │
     │                       │  ↳ yield WorkStep       │               │
     │                       │  ↳ push sub-task (READY)│               │
     │                       │                         │               │
     │  EventSource          │                         │               │
     │  GET /stream/<conv>   │                         │               │
     │ ───────────────────>  │  tracker.subscribe()    │               │
     │                       │                         │               │
     │                       │  GET /work/pop          │               │
     │                       │ <─────────────────────  │               │
     │                       │  {pre-hydrated payload} │               │
     │                       │ ─────────────────────>  │               │
     │                       │                         │               │
     │                       │                         │  POST         │
     │                       │                         │  /llm/complete│
     │                       │                         │ ────────────> │
     │                       │                         │               │
     │                       │                         │  SSE: token   │
     │                       │  NDJSON: token          │ <──────────── │
     │  SSE: token           │ <─────────────────────  │               │
     │ <───────────────────  │  _resolve_step →        │               │
     │                       │  tracker.push("token")  │               │
     │                       │                         │               │
     │                       │                         │  SSE: done    │
     │                       │  NDJSON: done           │ <──────────── │
     │                       │ <─────────────────────  │               │
     │                       │  _resolve_step →        │               │
     │                       │  future.set_result()    │               │
     │                       │  driver.asend(result)   │               │
     │                       │  ↳ generator exhausts   │               │
     │  SSE: done            │  chat.append(assistant) │               │
     │ <───────────────────  │  tracker.push("done")   │               │
     │                       │                         │               │
     │  close EventSource    │  unsubscribe            │               │


Emulated thinking flow
----------------------

The **ThinkScheduler** (``assai/scheduler/thinking.py``) chains two
LLM calls through the scheduler driver.  The worker remains completely
unaware — it just executes one task at a time.

::

  Frontend                 Server / Driver            Worker
     │                       │                          │
     │  POST /think/converse │                          │
     │ ───────────────────>  │                          │
     │                       │  create root task        │
     │                       │  (IN_PROGRESS)           │
     │                       │  drive_scheduler()       │
     │                       │  ↳ ThinkScheduler.run()  │
     │                       │                          │
     │  EventSource          │                          │
     │  GET /stream/<conv>   │                          │
     │ ───────────────────>  │  tracker.subscribe()     │
     │                       │                          │
     │                       │       ┌─────────────────────────────┐
     │                       │       │  Step 1: Think              │
     │                       │       └─────────────────────────────┘
     │                       │  yield WorkStep(         │
     │                       │    stream_mode="reasoning")
     │                       │  push thinker sub-task   │
     │                       │                          │
     │                       │  Worker pops sub-task    │
     │                       │                    <──────│
     │                       │                          │
     │                       │  NDJSON: token           │
     │  SSE: reasoning       │ <────────────────────────│
     │ <───────────────────  │  _resolve_step:          │
     │                       │    stream_mode=reasoning  │
     │                       │    → push as "reasoning"  │
     │                       │                          │
     │                       │  NDJSON: done            │
     │                       │ <────────────────────────│
     │                       │  future.set_result()     │
     │                       │  driver.asend(result)    │
     │                       │                          │
     │                       │       ┌─────────────────────────────┐
     │                       │       │  Step 2: Reply              │
     │                       │       └─────────────────────────────┘
     │                       │  yield WorkStep(         │
     │                       │    stream_mode="token")   │
     │                       │  push main sub-task      │
     │                       │                          │
     │                       │  Worker pops sub-task    │
     │                       │                    <──────│
     │                       │                          │
     │                       │  NDJSON: token           │
     │  SSE: token           │ <────────────────────────│
     │ <───────────────────  │  _resolve_step:          │
     │                       │    stream_mode=token      │
     │                       │    → push as "token"      │
     │                       │                          │
     │                       │  NDJSON: done            │
     │                       │ <────────────────────────│
     │                       │  future.set_result()     │
     │                       │  driver.asend(result)    │
     │                       │  ↳ generator exhausts    │
     │  SSE: done            │  chat.append(assistant)  │
     │ <───────────────────  │  tracker.push("done")    │
     │                       │                          │
     │  close EventSource    │  unsubscribe             │

Key points:

1. The **worker is unchanged** — it pops a task, calls the LLM, streams
   the result.  It does this twice (once for the thinker, once for the
   main agent) without knowing they are related.

2. The **ThinkScheduler** generator owns the composition:

   - First ``yield`` sends the thinker payload with
     ``stream_mode="reasoning"`` — the driver remaps ``token`` events
     to ``reasoning`` events for the frontend.
   - The driver ``asend()``s back a ``StepResult`` containing the
     accumulated thinker text.
   - Second ``yield`` sends the main-agent payload (with reasoning
     injected) and ``stream_mode="token"`` — tokens stream normally.
   - Tool-call follow-ups are handled in a loop, same as
     ``ConversationScheduler``.

3. The **scheduler driver** manages the ``asyncio.Future`` bridge:

   - ``_push_step()`` writes the pre-hydrated payload to disk and
     pushes a sub-task tagged ``ext.scheduler_driven=True``.
   - ``_await_step()`` creates a future keyed by sub-task ID.
   - ``_resolve_step()`` is called for every stream event; it fills
     the future when ``done`` arrives.
   - The SSE connection is per-conversation, not per-task, so events
     from both steps flow through the same ``EventSource``.

4. ``_do_pop()`` returns scheduler-driven sub-tasks' pre-hydrated
   payloads directly — no re-hydration needed.


Native thinking
---------------

When the model supports reasoning natively (e.g. via ``<think>`` tags),
no scheduler is involved.  The frontend calls ``POST /converse`` with
``enable_thinking: true``.  The LLM produces interleaved ``reasoning``
and ``token`` events which the worker relays unchanged.  The stream
handler forwards them as-is.


Tool-call flow
--------------

Tool calls are handled by the scheduler generator, not by the stream
handler.  When the ``StepResult`` returned to the generator contains
``tool_calls``, the scheduler:

1. Yields a ``WorkStep(kind="tool_call", stream_mode="tool")`` for
   each call.
2. Pushes ``tool_start`` / ``tool_end`` events via the ``StreamTracker``.
3. Appends ``tool_call`` and ``tool_result`` messages to chat history.
4. Builds follow-up messages and yields a new LLM ``WorkStep`` with
   the tool results included.
5. Repeats until no more tool calls are returned.

A legacy fallback in ``_handle_stream_event`` still handles tool-call
dispatch for non-scheduler-driven tasks (e.g. internal LLM calls from
``UberScheduler``'s routing step).
