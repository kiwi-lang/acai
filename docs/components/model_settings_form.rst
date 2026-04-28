ModelSettingsForm
=================

A spec-driven form that renders numeric input fields for model
hyperparameters (temperature, top-p, max tokens, etc.).

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``spec``
     - ``SettingInputFieldSpec[]``
     - Array of field specifications.
   * - ``onSettingsChange``
     - ``function``
     - ``(key: string, value: number) => void``

SettingInputFieldSpec
---------------------

.. list-table::
   :widths: 20 15 65
   :header-rows: 1

   * - Field
     - Type
     - Description
   * - ``name``
     - ``string``
     - Key name (also used for the label, title-cased).
   * - ``type``
     - ``string``
     - ``'int'`` or ``'float'``.
   * - ``min``
     - ``number``
     - Minimum allowed value.
   * - ``max``
     - ``number``
     - Maximum allowed value.
   * - ``default``
     - ``number``
     - Default value.

Features
--------

* Coercion and clamping on blur — out-of-range values are snapped to
  ``min`` / ``max``.
* Range hint text displayed under each field.
* Title-cased labels derived from the ``name`` field.

Source
------

``acai/ui/src/components/ModelSettingsForm.tsx``
