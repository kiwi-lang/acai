Agents
======

.. rubric:: Routes: ``/agents``, ``/agents/:agentName``

CRUD interface for agent definitions.  The URL is shareable — navigating
to ``/agents/coder`` opens the edit modal for the *coder* agent directly.

Agent list
----------

A scrollable list of all agents.  Each entry shows the agent name and a
short description.  Clicking an agent (or navigating to its URL) opens the
edit modal.

Edit modal
----------

A fixed-size modal with multiple sections:

**Configuration**

* Name, description, provider binding.
* ``uses_sandbox`` toggle (defaults to *true* for new agents).

**System template**

* A Jinja2 template editor with syntax highlighting (``django`` language
  mode via ``react-syntax-highlighter``).
* The template defines the system prompt injected into every LLM call
  for this agent.

**Permissions**

* Toggle buttons for each permission category (``read``, ``write``,
  ``execute``, ``admin``).  Displayed *above* the tool namespace section.

**Tool namespaces**

* **Tools** — toggle badges for each built-in tool namespace.
* **Skills** — toggle badges for each ``skills.<namespace>`` prefix.
  Individual skill namespaces can be exposed to an agent without granting
  access to the general skill management tools.

**Actions**

* Save, delete, or reset to the built-in definition.

Source
------

``acai/ui/src/components/AgentsPage.tsx``
