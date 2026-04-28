Home
====

.. rubric:: Route: ``/``

The landing page presents a clean "What can I help you with?" prompt with
the Açaí logo.

Features
--------

* **Message input** — a textarea where Enter sends and Shift+Enter inserts
  a newline.
* **Agent selector** — pick the agent that will handle the first message.
* **Provider selector** — choose the LLM provider/model.
* **Thinking mode** — toggle between *off*, *native*, and *emulated*
  reasoning.  The selection is persisted in ``localStorage``.
* **Automatic routing** — when a message is sent, it goes through
  ``uberConverse`` (SSE).  On receiving a ``route`` event the UI either
  navigates directly to the chosen conversation or shows a countdown
  letting the user accept or override.

Interaction flow
----------------

1. User types a message and presses Enter.
2. The ``uberConverse`` API streams back routing metadata.
3. On a ``route`` event the browser navigates to
   ``/conversations/<convId>`` with the pending message, provider, agent,
   and thinking mode passed as navigation state.
4. The conversation page picks up and continues the exchange.

Source
------

``acai/ui/src/components/Home.tsx``
