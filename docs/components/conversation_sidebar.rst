ConversationSidebar
===================

A scrollable sidebar listing conversations with selection and editing
capabilities.

Exports
-------

This file exports two components:

**ConversationSidebar**

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``conversations``
     - ``Conversation[]``
     - Array of conversations to display.
   * - ``activeId``
     - ``string``
     - Currently selected conversation ID.
   * - ``onSelect``
     - ``function``
     - Called when a conversation is clicked.
   * - ``onEdit``
     - ``function``
     - Called after saving or deleting a conversation.
   * - ``header``
     - ``ReactNode``
     - Optional element rendered above the list.

**EditModal**

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``conv``
     - ``Conversation``
     - The conversation being edited.
   * - ``onSave``
     - ``function``
     - Save callback.
   * - ``onDelete``
     - ``function``
     - Delete callback.
   * - ``onClose``
     - ``function``
     - Close callback.

Features
--------

* Title, description, and up to three tag badges per conversation.
* "No conversations yet" empty state.
* Fixed overlay modal for editing metadata and deleting.

Source
------

``acai/ui/src/components/ConversationSidebar.tsx``
