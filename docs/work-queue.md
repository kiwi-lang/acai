Work Queue
==========

The work queue (``assai/queue/work.py``) is a SQLite-backed task table.
It is primarily used for standalone tasks (created via ``POST /tasks``
or internal routing calls) and provides task lineage tracking for the
UI tree view.

**Note:** Conversation endpoints (``/converse``, ``/think/converse``,
``/uber/converse``) no longer route through the work queue.  They
use ``TaskGraph`` subclasses that dispatch directly to workers via
``LoadBalancer``.  See ``docs/orchestrator.md``.


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
    The task is being executed.  ``started_at`` is recorded.

``COMPLETED``
    Set by ``/work/result/<task_id>`` on success.

``FAILED``
    Set when retries are exhausted or a stuck-task timeout is reached.

``"chained"``
    Not a formal ``TaskStatus`` constant — used as a string status.
    Indicates the task has been superseded by a follow-up.

``CURATING`` / ``REVIEW``
    Defined in ``TaskStatus`` but not actively used.  Reserved for
    future workflows.


Task fields
-----------

Identity
^^^^^^^^

``id`` — ``String``, primary key
    Auto-generated 12-character hex string.

``kind`` — ``String``, default ``"llm_complete"``
    Determines how the task is processed.

    ============== =============================================
    Value          Meaning
    ============== =============================================
    ``llm_complete`` LLM inference.
    ``tool_call``    Tool execution.
    ``task``         Generic task (used by ``POST /tasks`` API).
    ============== =============================================

``title`` — ``String``, required
    Human-readable label.

``description`` — ``Text``, default ``""``
    Optional longer description.


Scheduling
^^^^^^^^^^

``status`` — ``String``, default ``PENDING``
    Current lifecycle state.

``priority`` — ``Integer``, default ``0``
    Higher values are popped first.

``depends_on`` — ``String``, default ``""``
    Comma-separated list of task IDs that must be ``COMPLETED`` before
    this task is eligible for popping.

``gpu`` — ``Integer``, default ``0``
    GPU requirement hint.


Specification
^^^^^^^^^^^^^

``spec`` — ``Text``, default ``""``
    Inline task specification.

``spec_path`` — ``String``, default ``""``
    Path to a JSON file containing the task specification.


Results
^^^^^^^

``result_path`` — ``String``, default ``""``
    Path to the result JSON file.

``error_log`` — ``Text``, default ``""``
    Error message from the last failed attempt.

``retries`` — ``Integer``, default ``0``
    Number of retry attempts so far.

``max_retries`` — ``Integer``, default ``3``
    Maximum number of retries before permanent failure.


Lineage
^^^^^^^

``parent_task`` — ``String``, nullable
    The task that spawned this one.

``root_task`` — ``String``, nullable
    The original ancestor task of the entire chain.


Context
^^^^^^^

``project`` — ``String``, default ``""``
    Project name.

``agent`` — ``String``, default ``""``
    Agent name for this task.

``conversation`` — ``String``, default ``""``
    Conversation ID this task belongs to.

``enable_thinking`` — ``Boolean``, nullable
    When ``True``, the LLM activates native reasoning.

``ext`` — ``JSON``, nullable
    Flexible metadata column.


Timestamps
^^^^^^^^^^

``created_at`` — ``DateTime``
    Automatically set on insert.

``updated_at`` — ``DateTime``
    Automatically updated on any change.

``started_at`` — ``DateTime``, nullable
    Set when status transitions to ``IN_PROGRESS``.


WorkQueue methods
-----------------

``push(title, kind, spec_path, project, agent, ...)``
    Insert a new task row.

``pop(status=READY) -> Task | None``
    Find the highest-priority task with the given status whose
    dependencies are all resolved.

``update(task_id, **fields)``
    Update any subset of task fields.

``get(task_id) -> Task | None``
    Fetch a task by ID.

``resolve_root(parent_id) -> str``
    Walk up the ``parent_task`` chain to find the root task ID.

``_deps_resolved(task) -> bool``
    Check whether all task IDs in ``depends_on`` are ``COMPLETED``.


Migration
---------

``_migrate()`` adds columns that may be missing in older databases.
This runs automatically on ``WorkQueue.__init__``.


Files-not-blobs pattern
-----------------------

Tasks store their data as files on disk, not in database columns:

- **Specs** are JSON files pointed to by ``spec_path``.
- **Results** are JSON files written to
  ``<workspace>/tasks/<task_id>/result.json``.

This keeps the SQLite database small and makes task artifacts
inspectable and git-trackable.
