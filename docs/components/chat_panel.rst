ChatPanel
=========

The main chat component used across multiple pages (Conversations,
Project View, Uber Chat, Workflow Editor).

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``conversationId``
     - ``string``
     - ID of the conversation to display.
   * - ``onConversationCreated``
     - ``function``
     - Callback fired when a new conversation is created.
   * - ``project``
     - ``string``
     - Scope the chat to a project.
   * - ``refinerAgent``
     - ``string``
     - Override the default agent for this chat.
   * - ``compact``
     - ``boolean``
     - Reduce padding for embedded use.
   * - ``initialProvider``
     - ``string``
     - Pre-select an LLM provider.
   * - ``initialAgent``
     - ``string``
     - Pre-select an agent.
   * - ``mode``
     - ``ChatMode``
     - ``'converse'`` (default) or ``'uber'``.
   * - ``onRoute``
     - ``function``
     - Callback for uber-mode route events.
   * - ``statusBar``
     - ``ReactNode``
     - Custom element rendered in the status bar area.
   * - ``disabled``
     - ``boolean``
     - Disable input.
   * - ``onResponseComplete``
     - ``function``
     - Callback fired when the LLM finishes responding.
   * - ``placeholder``
     - ``string``
     - Input placeholder text.
   * - ``initialThinking``
     - ``boolean``
     - Enable thinking mode on mount.
   * - ``initialThinkingMode``
     - ``string``
     - Initial thinking mode (``native`` or ``emulated``).
   * - ``autoSendMessage``
     - ``string``
     - Automatically send this message on mount.
   * - ``initialGraph``
     - ``string``
     - Pre-select a workflow graph.
   * - ``ephemeral``
     - ``boolean``
     - Do not persist the conversation.
   * - ``taskId``
     - ``string``
     - Scope to a specific task.

Features
--------

* Streams LLM tokens via SSE (``converse``, ``uberConverse``, or
  ``thinkConverse``).
* Displays a **ContextRing** — a circular SVG indicator showing token
  usage vs. context window.
* Collapsible **Reasoning** blocks rendered as Markdown.
* Supports thinking mode toggling at the conversation level.

Source
------

``acai/ui/src/components/ChatPanel.tsx``
