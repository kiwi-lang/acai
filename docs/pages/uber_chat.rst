Uber Chat
=========

.. rubric:: Route: ``/uber``

An experimental auto-routing chat mode.  The interface looks like the
standard conversations page but operates in ``uber`` mode where the
system automatically selects (or creates) the most appropriate
conversation for each message.

Features
--------

* **Automatic conversation routing** — instead of manually picking a
  conversation, the ``uberConverse`` API analyses the message and routes
  it to the best-fit conversation.
* **Sidebar** — lists conversations with edit/delete via ``EditModal``.
* **Chat panel** — same :doc:`../components/chat_panel` component in
  ``mode="uber"``.
* **Placeholder** — "Ask anything — conversations are picked
  automatically…"

This page lives under the *Dev* section of the sidebar navigation.

Source
------

``acai/ui/src/components/UberChat.tsx``
