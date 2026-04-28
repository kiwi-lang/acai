ChatComponent
=============

A config-driven, full-height chat shell.  Unlike :doc:`chat_panel` (which
is tailored to agent conversations), ``ChatComponent`` is a generic
message-list + input container that can be wired to any backend action.

Props
-----

A single ``config: ChatComponentConfig`` prop drives the entire component:

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Config field
     - Type
     - Description
   * - ``title``
     - ``string``
     - Header text.
   * - ``description``
     - ``string``
     - Sub-header text.
   * - ``allowedInputTypes``
     - ``ChatInputType[]``
     - Input types accepted (text, image, audio).
   * - ``expectedOutputType``
     - ``ChatOutputType``
     - Expected response type.
   * - ``placeholder``
     - ``string``
     - Input placeholder.
   * - ``onSendMessage``
     - ``function``
     - ``(message, actionId) => void``
   * - ``onRetry``
     - ``function``
     - Retry handler for failed messages.
   * - ``settingsPanel``
     - ``ReactNode``
     - Optional settings side-panel.
   * - ``customInput``
     - ``ReactNode``
     - Replace the default ``ChatInput``.
   * - ``modelSelector``
     - ``ReactNode``
     - Model picker element.

Features
--------

* **Empty state** when no messages are present.
* Maps stdout/stderr WebSocket events to in-flight message updates.
* Integrates :doc:`log_display` for real-time logs.

Source
------

``acai/ui/src/components/ChatComponent.tsx``
