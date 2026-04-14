Work Queue
==========

The work queue (``assai/queue/work.py``) is the central coordination
mechanism.  It is a SQLite-backed task table that all components —
orchestrator, schedulers, and workers — interact with through the
``WorkQueue`` class.


Task status lifecycle
---------------------

::

    PENDING ──> READY ──> IN_PROGRESS ──> COMPLETED
                  │              │
                  │              ├──> FAILED
                  │              │
                  │              └──> "chained" (superseded by follow-up)
                  │
                  └──> (retry: back to READY)


``PENDING``
    Default status on insert.  The task exists but is not yet
    schedulable.  Most code paths immediately follow ``push()`` with
    ``update(status=READY)``.

``READY``
    Eligible to be popped by a worker.  ``queue.pop(status=READY)``
    returns the highest-priority task whose dependencies are all
    ``COMPLETED``.

``IN_PROGRESS``
    Set by ``_do_pop()`` immediately after ``pop()``.  The task is
    being executed by a worker.  ``started_at`` is recorded.

``COMPLETED``
    Set by ``/work/result/<task_id>`` on success (unless already
    ``"chained"``).

``FAILED``
    Set when retries are exhausted or a stuck-task timeout is reached.

``"chained"``
    Not a formal ``TaskStatus`` constant — used as a string status.
    Indicates the task has been superseded by a follow-up (e.g. a
    tool-call pipeline or a post-thinking main task).  The result
    handler skips appending an assistant message for chained tasks.

``CURATING`` / ``REVIEW``
    Defined in ``TaskStatus`` but not actively used in the current
    codebase.  Reserved for future workflows.


Task fields
-----------

Identity
^^^^^^^^

``id`` — ``String``, primary key
    Auto-generated 12-character hex string.  Stable across the entire
    system — used in URLs, file paths, parent/root links, and stream
    events.

    **Set:** automatically at ``push()`` time.

``kind`` — ``String``, default ``"llm_complete"``
    Determines how the worker processes the task.

    ============== =============================================
    Value          Meaning
    ============== =============================================
    ``llm_complete`` LLM inference.  Worker calls ``/llm/complete``.
    ``tool_call``    Tool execution.  Worker calls the tool registry.
    ``task``         Generic task (used by ``POST /tasks`` API).
    ============== =============================================

    **Set at push:** by the caller (``/converse``, schedulers, stream
    handler, ``POST /tasks``).

``title`` — ``String``, required
    Human-readable label (e.g. ``"converse: how do I..."``,
    ``"tool: filesystem.read_file"``).  Also used to infer the tool
    name in result handling.

    **Set at push.**

``description`` — ``Text``, default ``""``
    Optional longer description.  Not heavily used in the current
    codebase.

    **Set at push.**


Scheduling
^^^^^^^^^^

``status`` — ``String``, default ``PENDING``
    Current lifecycle state.  See lifecycle section above.

    **Set:** at ``push()`` (default ``PENDING``), then transitioned by
    ``queue.update()`` throughout the task's life.

``priority`` — ``Integer``, default ``0``
    Higher values are popped first.  ``pop()`` orders by
    ``priority DESC, created_at ASC``.  Follow-up and tool tasks
    inherit the parent's priority.

    **Set at push.**

``depends_on`` — ``String``, default ``""``
    Comma-separated list of task IDs that must be ``COMPLETED`` before
    this task is eligible for popping.  Used for follow-up LLM tasks
    that wait for tool results.

    **Set at push:** ``push(depends_on=["id1", "id2"])`` joins them.
    **Checked at pop:** ``_deps_resolved()`` verifies each dependency
    exists and is ``COMPLETED``.

``gpu`` — ``Integer``, default ``0``
    GPU requirement hint.  Currently always ``0`` in active code paths.

    **Set at push.**


Specification
^^^^^^^^^^^^^

``spec`` — ``Text``, default ``""``
    Inline task specification.  If non-empty, ``resolve_task()``
    prefers this over loading from ``spec_path``.

    **Set at push.**

``spec_path`` — ``String``, default ``""``
    Path to a JSON file containing the task specification.  This is
    the primary mechanism — tasks point to files rather than storing
    data inline.

    Typical values:

    - ``<workspace>/conversations/<conv_id>/conversation.json`` — for
      conversation-based LLM tasks.  ``resolve_task()`` parses this as
      a message array.
    - ``<workspace>/tasks/<parent_id>/payload_<tool>.json`` — for tool
      call payloads written by the stream handler.
    - ``<workspace>/tasks/<task_id>/conversation.json`` — for
      scheduler-created specs.

    **Set at push** or by ``update(spec_path=...)`` shortly after.

``context_path`` — ``String``, default ``""``
    Legacy field for curator-attached context files.  Not used in
    active code paths.


Results
^^^^^^^

``result_path`` — ``String``, default ``""``
    Path to the result JSON file, written by the orchestrator when
    ``/work/result/<task_id>`` is called.  Always under
    ``<workspace>/tasks/<task_id>/result.json``.

    **Set:** by the result handler on success or failure.
    **Read:** by the ``Orchestrator`` background chainer and
    ``AsyncTask.result()`` in schedulers.

``error_log`` — ``Text``, default ``""``
    Error message from the last failed attempt.

    **Set:** by the result handler on failure, or by the stuck-task
    reaper.

``retries`` — ``Integer``, default ``0``
    Number of retry attempts so far.  Incremented on each failure.
    When ``retries >= max_retries``, the task transitions to
    ``FAILED``.

    **Set:** by the result handler or stuck-task reaper.

``max_retries`` — ``Integer``, default ``3``
    Maximum number of retries before permanent failure.

    **Set at push.**


Lineage
^^^^^^^

``parent_task`` — ``String``, nullable
    The task that spawned this one.  Tool-call tasks point to their
    parent LLM task.  Follow-up LLM tasks point to the previous LLM
    task in the chain.

    **Set at push.**

``root_task`` — ``String``, nullable
    The original ancestor task of the entire chain.  Computed via
    ``queue.resolve_root(parent_id)`` which walks up to find the
    root.  Used for grouping related tasks in the UI tree view.

    **Set at push.**


Context
^^^^^^^

``project`` — ``String``, default ``""``
    Project name.  Used for routing, worktree setup, and agent
    resolution.

    **Set at push.**

``agent`` — ``String``, default ``""``
    Agent name to use for this task (e.g. ``"default"``,
    ``"thinker"``, ``"coder"``).  Resolved by ``_do_pop()`` into an
    ``AgentDef`` for template rendering.

    **Set at push.**

``conversation`` — ``String``, default ``""``
    Conversation ID this task belongs to.  Used for:

    - Appending assistant messages to the conversation on completion.
    - Registering with ``StreamTracker`` for SSE routing.
    - Resolving provider from conversation metadata.

    **Set at push.**

``enable_thinking`` — ``Boolean``, nullable
    When ``True``, the worker passes ``enable_thinking`` to the LLM
    endpoint to activate native reasoning (e.g. ``<think>`` tags).
    When ``None``, the LLM uses its default behavior.

    **Set at push** (from ``/converse``).  Propagated to follow-up
    tasks.

``ext`` — ``JSON``, nullable
    Flexible metadata column for scheduler-specific data.  Not
    included in the task list API response.

    Current uses:

    ``scheduler_driven``
        Set to ``True`` by the scheduler driver when pushing sub-tasks.
        Tells ``_do_pop()`` to return the pre-hydrated payload directly
        instead of re-hydrating.

    **Set:** by ``queue.update(task_id, ext={...})`` after ``push()``.


Timestamps
^^^^^^^^^^

``created_at`` — ``DateTime``
    Automatically set on insert.

``updated_at`` — ``DateTime``
    Automatically updated on any change.

``started_at`` — ``DateTime``, nullable
    Set when status transitions to ``IN_PROGRESS`` (if not already
    set).  Cleared on retry.  Used by the stuck-task reaper to detect
    tasks that have been running too long.


Other
^^^^^

``assigned_to`` — ``String``, default ``""``
    Worker assignment.  Settable via ``PATCH /tasks/<id>`` but not
    actively used by the popping logic.

``worktree`` — ``String``, default ``""``
    Git worktree path.  Exposed in ``resolve_task()`` but not written
    by the orchestrator in current code paths.  The worker manages
    worktrees in memory during execution.


WorkQueue methods
-----------------

``push(title, kind, spec_path, project, agent, ...)``
    Insert a new task row.  Does **not** set ``status`` — the caller
    must follow up with ``update(id, status=READY)`` when the task
    should become schedulable.

``pop(status=READY) -> Task | None``
    Find the highest-priority task with the given status whose
    dependencies are all resolved.  Returns ``None`` if the queue is
    empty.  Does **not** change the task's status — ``_do_pop()``
    handles the ``IN_PROGRESS`` transition.

``update(task_id, **fields)``
    Update any subset of task fields.  Automatically sets
    ``started_at`` when status becomes ``IN_PROGRESS``.

``get(task_id) -> Task | None``
    Fetch a task by ID.

``resolve_root(parent_id) -> str``
    Walk up the ``parent_task`` chain to find the root task ID.
    Returns the parent's ``root_task`` if set, otherwise the parent's
    own ID.

``_deps_resolved(task) -> bool``
    Check whether all task IDs in ``depends_on`` exist and have
    ``status == COMPLETED``.


Migration
---------

``_migrate()`` adds columns that may be missing in older databases:
``agent``, ``started_at``, ``enable_thinking``, ``conversation``,
``ext``.  This runs automatically on ``WorkQueue.__init__``.


Files-not-blobs pattern
-----------------------

Tasks store their data as files on disk, not in database columns:

- **Specs** are JSON files pointed to by ``spec_path``.  For
  conversations, this is the ``conversation.json`` file in the chat
  store.  For tool calls, it's a payload file under the tasks
  directory.

- **Results** are JSON files written to
  ``<workspace>/tasks/<task_id>/result.json`` by the orchestrator's
  result handler.

This keeps the SQLite database small and makes task artifacts
inspectable and git-trackable.
