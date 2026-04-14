Worker
======

The worker (``assai/core/worker.py`` + ``assai/core/llm.py``) is the
execution engine.  It pops tasks, calls the LLM, runs tools, and
pushes results back.  It is intentionally simple.


Design philosophy
-----------------

1. **The worker is dumb.**  It does not know about agent composition,
   thinking chains, multi-step workflows, or conversation state.  It
   receives a fully hydrated payload, executes it, and returns the
   result.

2. **One task, one call.**  Each popped task results in exactly one LLM
   call or one tool execution.  Chaining multiple calls is the
   orchestrator's job.

3. **Stateless pump.**  The poller loop is: pop → prepare → dispatch →
   push result.  No task history, no conversation memory, no
   inter-task state.

4. **GPU resource owner.**  The worker process owns the GPU.  When a
   tool needs the GPU (e.g. vision), the worker stops the LLM server
   to free VRAM, runs the tool, then restarts.

5. **Streaming relay.**  Token streams from the LLM are relayed as
   NDJSON to the orchestrator in real time.  The worker does not
   interpret stream events — it just forwards them.


Process architecture
--------------------

The worker runs as a single OS process with two concurrent roles:

**Flask app** (main thread)
    Serves HTTP endpoints:

    ``POST /worker/llm/complete``
        Accepts a message list, optional tools, and provider config.
        Streams the LLM response as SSE events (``token``,
        ``reasoning``, ``tool_call_delta``, ``done``, ``error``).

    ``POST /worker/switch-model``
        Hot-swap the running model.

    ``GET /worker/status``
        Health check and model info.

    ``POST /tools/call``
        Execute a registered tool function.

**WorkerPoller** (background thread)
    Polls the orchestrator for work and drives execution:

    1. ``_poll_once()`` — pop a task via SocketIO RPC (preferred) or
       HTTP ``GET /work/pop`` (fallback).
    2. Route by ``kind``:

       - ``llm_complete`` → ``_prepare_llm_work`` → ``_dispatch_llm``
       - ``tool_call`` → ``_dispatch_tool``

    3. ``_push_result()`` — POST result to
       ``/work/result/<task_id>``.


LLM dispatch
------------

``_dispatch_llm`` is the core streaming relay::

    Worker Poller                    Worker Flask App
         │                                │
         │  POST /worker/llm/complete     │
         │  {messages, tools, provider}   │
         │ ────────────────────────────>  │
         │                                │
         │  SSE: event: token             │
         │       data: {"token": "..."}   │
         │ <────────────────────────────  │
         │                                │
         │  ... more events ...           │
         │                                │
         │  SSE: event: done              │
         │ <────────────────────────────  │

Each SSE event is repackaged as an NDJSON line and yielded into a
chunked ``POST /stream/push`` to the orchestrator.  The accumulated
text (and optional reasoning) is returned as the task result.

The worker calls its own HTTP endpoint (localhost) rather than the LLM
library directly.  This keeps the LLM abstraction
(``OpenAICompatibleLLM``) behind a clean HTTP boundary and allows the
same endpoint to serve direct requests.


Preparation
-----------

``_prepare_llm_work`` runs before dispatch and may mutate the work
dict:

**Compressor**
    If the agent specifies a ``compressor`` and the message list
    exceeds a token threshold, older messages are summarized by a
    compressor LLM call and replaced with a condensed system message.
    This keeps context within the model's window.

**Worktree setup**
    For coding agents with a project path, the worker may create or
    reuse a git worktree and append a working-directory system message
    so file-system tools operate in the right location.


Tool dispatch
-------------

``_dispatch_tool`` handles ``tool_call`` tasks:

1. If the tool requires the GPU and the LLM is running, stop the LLM
   server to free VRAM.
2. If sandboxing is configured and the tool's namespace is sandboxed
   (``code``, ``git``, ``shell``, ``filesystem``), proxy the call to a
   Podman container running ``assai mcp``.
3. Otherwise execute locally via ``registry.call(tool_name, args)``.

Tools are discovered at startup from ``assai.tools`` and
``assai.plugins`` packages.  Each tool has a namespace, a qualified
name, and an OpenAI-compatible function schema.


What the worker does NOT do
---------------------------

- Decide what to run next.  No agent graphs, no composition, no
  thinking chains.
- Own conversation state.  It passes the ``conversation`` ID through
  to the result payload but never reads or writes the conversation
  store.
- Interpret tool-call deltas.  It forwards ``tool_call_delta`` events
  to the orchestrator, which handles tool dispatch.
- Manage the task queue.  It only pops and pushes results.


SocketIO
--------

The worker uses SocketIO in two directions:

**Server** (on the worker)
    Telemetry: the UI can request GPU/system metrics via
    ``request_telemetry``, and the worker responds with ``telemetry``
    events.

**Client** (poller → orchestrator)
    Faster work distribution: the poller connects to the orchestrator's
    SocketIO and calls ``work_pop`` as an RPC.  Falls back to HTTP
    ``GET /work/pop`` if the WebSocket connection fails.
