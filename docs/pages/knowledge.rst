Knowledge
=========

.. rubric:: Route: ``/knowledge``

A tree-based knowledge base browser and editor.

Features
--------

* **Document tree** — hierarchical navigation by
  *subject → sub-subject → title*.
* **Search** — filter documents by keyword.
* **Markdown viewer** — selecting a document renders its content as
  Markdown.
* **Edit mode** — inline editor to modify document content and save.
* **Delete** — remove a document with a confirmation prompt.

The knowledge store is backed by the orchestrator's ``KnowledgeStore``
and exposed through REST endpoints (``/knowledge/...``).

Source
------

``acai/ui/src/components/KnowledgePage.tsx``
