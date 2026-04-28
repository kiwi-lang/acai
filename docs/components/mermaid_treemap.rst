MermaidTreemap
==============

Renders a Mermaid treemap diagram from a text definition into inline SVG.

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``definition``
     - ``string``
     - Mermaid treemap source text.
   * - ``id``
     - ``string``
     - Stable ID used for the render target and effect dependencies.

Features
--------

* Uses ``mermaid.render`` with dark theme and treemap-specific config
  (``valueFormat``, ``showValues``, ``padding``).
* Returns ``null`` when the definition is empty.
* Displays a red error message with the raw definition on parse failure.
* Security level set to ``loose`` for full rendering support.

Source
------

``acai/ui/src/components/MermaidTreemap.tsx``
