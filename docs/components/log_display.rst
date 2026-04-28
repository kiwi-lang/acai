LogDisplay
==========

A collapsible real-time log panel that subscribes to WebSocket
stdout/stderr events.

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``isVisible``
     - ``boolean``
     - Whether the panel is expanded.
   * - ``onToggle``
     - ``function``
     - Toggle visibility callback.
   * - ``clearOnNewRequest``
     - ``boolean``
     - Auto-clear logs when a new request starts.

Features
--------

* Accumulates log entries with timestamps, capped at 500 lines.
* Differentiates stderr (red) from stdout styling.
* Auto-scrolls to the latest entry.
* ``clearLogs`` helper for parent components.

Source
------

``acai/ui/src/components/LogDisplay.tsx``
