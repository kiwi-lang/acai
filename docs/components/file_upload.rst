FileUpload
==========

Hidden file-input controls with icon-button triggers for image and audio
uploads.

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``onImageUpload``
     - ``function``
     - Callback receiving the selected image ``File``.
   * - ``onAudioUpload``
     - ``function``
     - Callback receiving the selected audio ``File``.
   * - ``disabled``
     - ``boolean``
     - Disable the upload buttons.

Features
--------

* Image button only renders when ``onImageUpload`` is provided; same for
  audio.
* Accepts ``image/*`` and ``audio/*`` respectively.
* Resets the file input value after each selection so the same file can
  be re-selected.

Source
------

``acai/ui/src/components/FileUpload.tsx``
