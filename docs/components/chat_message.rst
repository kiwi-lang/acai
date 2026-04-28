ChatMessage
===========

Renders a single message in a conversation.

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``message``
     - ``Message``
     - The message object to render.
   * - ``onRetry``
     - ``function``
     - Retry callback for failed messages.

Features
--------

* Two-column layout: avatar on the left, content on the right.
* **U** avatar for user messages, **AI** avatar for assistant messages.
* Role-coloured labels and differentiated background styling.
* Renders body content as Markdown, with support for media, mesh
  viewer, and log attachments.
* Error/retry UI driven by ``retryPrompt``.
* Auto-scrolls attached log sections into view.

Source
------

``acai/ui/src/components/ChatMessage.tsx``
