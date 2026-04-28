Conversations
=============

.. rubric:: Routes: ``/conversations``, ``/conversations/:convId``

The main chat interface of Açaí.  A two-panel layout with a conversation
list on the left and the active chat on the right.

Layout
------

**Left panel — Conversation Sidebar**

* Scrollable list of all conversations.
* Each entry shows title, description preview, and up to three tags.
* Click to select; the URL updates to ``/conversations/<convId>``.
* Edit button opens a modal to rename, re-describe, re-tag, or delete
  a conversation.
* A *New Chat* action at the top creates a fresh conversation.

**Right panel — Chat Panel**

* Full chat view powered by the :doc:`../components/chat_panel` component.
* Supports ``converse`` mode (standard agent chat with tool follow-up).
* Receives optional navigation state from the :doc:`home` page:
  ``pendingMessage``, ``initialProvider``, ``initialAgent``, and
  ``initialThinking``.

Key behaviours
--------------

* Selecting a conversation loads its history from the backend.
* Sending a message triggers an SSE stream; tokens appear in real time.
* The conversation list refreshes when new messages arrive.

Source
------

``acai/ui/src/components/ConversationsPage.tsx``
