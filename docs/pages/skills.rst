Skills
======

.. rubric:: Route: ``/skills``

Management interface for user-created skills — ad-hoc tools that agents
can call at runtime.

Layout
------

**Sidebar**

* Lists skills grouped by namespace.
* Search field to filter by name.
* *Create Skill* form: namespace, name, and description.

**Detail pane (tabs)**

* **Code** — edit ``run.py``, the Python script executed when the skill
  is invoked.
* **Definition** — edit ``tool.json``, the MCP tool schema (description,
  parameters, required fields).
* **README** — edit the human-readable documentation.

Each tab has its own *Save* action.  A *Delete* button removes the skill
entirely.

Skill lifecycle
---------------

1. Create a skill via the UI or the ``create_skill`` tool.
2. On the next server restart (or live via the API) the ``SkillStore``
   discovers the skill and registers it in the ``ToolRegistry``.
3. Agents with the matching ``skills.<namespace>`` namespace can call
   the skill during conversations.

Source
------

``acai/ui/src/components/SkillsPage.tsx``
