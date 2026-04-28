Models
======

An admin-style page component for inspecting and managing loaded LLM
models.

Props
-----

This component takes **no props**.  It loads data from
``acaiAPI.getLoadedModels()``.

Features
--------

* Displays GPU and PyTorch memory usage summaries.
* Per-model usage statistics.
* **Unload** button to remove a model from memory
  (``removeLoadedModel``).
* Success and error toast notifications (auto-clear after ~3 seconds).
* ``formatMemory`` and ``formatTime`` helpers for human-readable display.

Source
------

``acai/ui/src/components/Models.tsx``
