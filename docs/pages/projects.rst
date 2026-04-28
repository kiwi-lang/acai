Projects
========

.. rubric:: Route: ``/projects``

The projects list page displays all registered projects as cards.

Features
--------

* **Project cards** — each card shows the project language, source
  (cloned repo or new), path, and metadata.
* **New Project form** — toggled by a button; fields include language,
  creation mode (new vs. clone), template, repository URL, provider,
  and Python/venv settings for Python projects.
* **Navigation** — clicking a card navigates to ``/projects/:name``,
  which opens the :doc:`project_view`.

Source
------

``acai/ui/src/components/ProjectsPage.tsx``
