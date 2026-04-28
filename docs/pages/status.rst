Status
======

.. rubric:: Route: ``/status``

System health dashboard and LLM provider management.

Orchestrator status
-------------------

* Real-time status fetched over WebSocket (falls back to REST polling).
* Event log showing recent system events with timestamps.

LLM providers
-------------

A CRUD interface for managing LLM provider configurations:

* **Add / Edit** — backend type, model name, endpoint URL, API key,
  roles (e.g. ``chat``, ``completion``), and other provider-specific
  settings.
* **Activate / Deactivate** — toggle a provider on or off.
* **Delete** — remove a provider configuration.

Source
------

``acai/ui/src/components/StatusPage.tsx``
