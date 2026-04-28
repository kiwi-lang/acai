ChatInput
=========

A textarea input with send button and optional image/audio attachment
support.

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``onSendMessage``
     - ``function``
     - ``(message, imageFile?, audioFile?) => void``
   * - ``disabled``
     - ``boolean``
     - Disable the input.
   * - ``placeholder``
     - ``string``
     - Placeholder text (default: ``"Send a message..."``).

Features
--------

* **Enter** sends the message; **Shift+Enter** inserts a newline.
* Image and audio attachments via :doc:`file_upload` buttons.
* Inline image preview before sending.
* Focus is restored to the textarea after each send.

Source
------

``acai/ui/src/components/ChatInput.tsx``
