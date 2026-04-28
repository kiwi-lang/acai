Tasks
=====

.. rubric:: Route: ``/tasks``

A work-queue browser showing all dispatched tasks and their child trees.

Features
--------

* **Task list** — all queued, running, and completed tasks with status
  badges and kind labels.
* **Project filter** — optionally scope the list to a single project.
* **Task tree** — expand a task to see its child tasks in a hierarchical
  view.
* **Detail panel** — select a node to inspect full database fields
  (status, result, timestamps, etc.).
* **Live updates** — new tasks and status changes arrive in real time
  via ``useAgentSocket``.

Source
------

``acai/ui/src/components/TasksPage.tsx``
