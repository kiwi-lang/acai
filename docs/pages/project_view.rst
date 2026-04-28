Project View
============

.. rubric:: Route: ``/projects/:name``

A detailed view for a single project combining metadata management, a
Kanban task board, and a project-scoped chat.

Sections
--------

**Project metadata**

An edit modal exposes fields such as language, template, path, repository,
virtual-env settings, and the *refiner agent* used for the project chat.

**Kanban board**

Tasks are organised into status columns (e.g. backlog, in-progress, done).
Users can create tasks, drag them between columns, and open a detail
panel for each task.  Live updates arrive over Socket.IO.

**Project chat**

A :doc:`../components/chat_panel` instance scoped to the project, with the
refiner agent pre-selected.  Task context can be injected into the chat
automatically.

Source
------

``acai/ui/src/components/ProjectView.tsx``
