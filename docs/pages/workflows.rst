Workflow Editor
===============

.. rubric:: Routes: ``/workflows``, ``/workflows/:workflowIdParam``

A visual node-graph editor built on `React Flow <https://reactflow.dev/>`_
for designing and running multi-step agent workflows.

Editor features
---------------

* **Node palette** — lists all registered node types fetched from the
  server (``getNodeTypes``), grouped by category (Flow, Agent, Data,
  Debug).
* **Drag-and-drop** — add nodes to the canvas by dragging from the
  palette.
* **Typed pins** — nodes expose execution pins (white, control flow) and
  data pins (coloured, typed values).  Invalid connections are rejected
  based on pin-type compatibility.
* **Save / Load** — workflows are persisted on the server and can be
  loaded by ID from the URL.
* **Validate** — checks for type errors and unconnected required inputs.
* **Run** — executes the workflow with a test conversation; events stream
  back through SSE and appear in an embedded chat panel.
* **Built-in save** — save a workflow as a built-in that ships with the
  package.

Node types
----------

The available node types are defined in ``acai/tasks/nodes.py``.
Key built-in nodes include:

====================  =========  ================================================
Node                  Category   Purpose
====================  =========  ================================================
Start                 Flow       Entry point; provides conversation and message
Condition             Flow       Branch on a Python expression
Output                Flow       Terminal node; streams final result to the user
Agent Call            Agent      Prepare and dispatch an LLM call
Accumulate            Agent      Consume a token stream and collect the result
Stream Transform      Agent      Relabel stream event modes
Tool Follow-Up        Agent      Execute tool calls and re-call the LLM in a loop
Background Agent      Agent      All-in-one silent agent with tool loop
Append                Data       Append an item to a message list
Extend                Data       Merge two message lists
Reasoning Message     Data       Wrap reasoning text into a system message
Content               Data       Extract content string from a message
Role                  Data       Extract role string from a message
Set Variable          Data       Store a named variable for later retrieval
Get Variable          Data       Read a previously stored variable
Parse Knowledge       Data       Convert curator JSON output to knowledge context
Fetch Conversation    Data       Load conversation history by ID
Print                 Debug      JSON-dump a value to the user
====================  =========  ================================================

Source
------

``acai/ui/src/components/WorkflowEditor.tsx`` (~1700 lines)
