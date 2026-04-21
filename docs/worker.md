Worker
======

The worker (``acai/worker/app.py`` + ``acai/worker/llm.py``) is the
execution engine.  It hosts the LLM, runs tools, and streams results
back to the orchestrator.


Design philosophy
-----------------

1. **The worker is dumb.**  It does not know about agent composition,
   thinking chains, multi-step workflows, or conversation state.  It
   receives a fully hydrated payload, executes it, and returns the
   result.

2. **One call at a time.**  The ``LoadBalancer`` ensures each worker
   handles one request at a time.  The orchestrator acquires a worker
   via ``lb.acquire()`` and releases it when the graph completes.

3. **GPU resource owner.**  The worker process owns the GPU.  When a
   tool needs the GPU (e.g. vision), the worker stops the LLM server
   to free VRAM, runs the tool, then restarts.

4. **Streaming relay.**  Token streams from the LLM are returned as
   SSE responses.  The orchestrator consumes them via
   ``AsyncSSEIterator``.


Process architecture
--------------------

The worker runs as a FastAPI app with these endpoints:

``POST /worker/llm/complete``
    Accepts a message list, optional tools, and provider config.
    Streams the LLM response as SSE events (``token``,
    ``reasoning``, ``tool_call_delta``, ``done``, ``error``).

``POST /worker/switch-model``
    Hot-swap the running model.

``GET /worker/status``
    Health check and model info.

``POST /tools/call``
    Execute a registered tool function.  Tool functions run in a
    thread pool (``asyncio.to_thread``) to avoid blocking the event
    loop.  A ``WorkerContext`` is set in the thread so tools can
    access the orchestrator client (for callbacks like ``ui.toast``).

``GET /worker/logs``
    Read latest vLLM server log.


Worker registration
-------------------

On startup the worker:

1. Registers with the orchestrator via
   ``POST /agent/workers/register``, providing its URL and
   capabilities.
2. Starts a ``HealthReporter`` that sends periodic heartbeats
   (telemetry: GPU utilization, VRAM, system load) via WebSocket
   or HTTP fallback.

The orchestrator's ``LoadBalancer`` tracks worker status (idle, busy,
offline) and provides ``lb.acquire()`` as an async context manager
for exclusive worker access.


LLM dispatch
------------

The orchestrator's ``TaskGraph.dispatch()`` sends a payload to the
worker's ``/llm/complete`` endpoint and consumes the SSE response::

    Orchestrator (TaskGraph)         Worker
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

The ``AsyncSSEIterator`` handles the HTTP connection and yields parsed
``ServerSentEvent`` objects.


Preparation
-----------

``TaskGraph.prepare()`` runs in the orchestrator before dispatch and
builds the LLM payload:

**Agent resolution**
    Loads the ``AgentDef``, renders the Jinja2 template with the
    conversation history and task metadata to produce the message list.
    See ``docs/agent-resolution.md``.

**Tool resolution**
    If the agent has tools configured, resolves tool schemas via the
    ``ToolRegistry`` and includes them in the payload.

**Provider override**
    If the conversation specifies a non-default provider, the provider
    config is included so the worker connects to the right LLM
    endpoint.


Tool dispatch
-------------

``TaskGraph.dispatch_tool()`` sends tool calls to the worker via
``POST /tools/call``.  The worker then:

1. If the tool requires the GPU and the LLM is running, stop the LLM
   server to free VRAM.
2. If sandboxing is configured and the tool's namespace is sandboxed
   (``code``, ``git``, ``shell``, ``filesystem``), proxy the call to a
   Podman container running ``acai mcp``.
3. Otherwise execute locally via ``registry.call(tool_name, args)``.

A ``WorkerContext`` is set in the tool's thread providing an
``OrchestratorClient`` so tools like ``ui.toast`` can call back to
the orchestrator.

Tools are discovered at startup from ``acai.tools`` and
``acai.plugins`` packages.  Each tool has a namespace, a qualified
name, and an OpenAI-compatible function schema.


What the worker does NOT do
---------------------------

- Decide what to run next.  No agent graphs, no composition, no
  thinking chains.
- Own conversation state.  It does not read or write the conversation
  store.
- Interpret tool-call deltas.  It forwards ``tool_call_delta`` events;
  the orchestrator's ``TaskGraph`` handles tool dispatch.


SocketIO
--------

The worker uses SocketIO in two directions:

**Server** (on the worker)
    Telemetry: the UI can request GPU/system metrics via
    ``request_telemetry``, and the worker responds with ``telemetry``
    events.

**Client** (worker → orchestrator)
    The worker sends heartbeats and telemetry over WebSocket for
    low-latency health monitoring.
