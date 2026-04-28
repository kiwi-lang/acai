Markdown
========

A memoised Markdown renderer used throughout the UI for message content,
knowledge documents, and README previews.

Props
-----

.. list-table::
   :widths: 25 15 60
   :header-rows: 1

   * - Prop
     - Type
     - Description
   * - ``content``
     - ``string``
     - The Markdown source to render.
   * - ``fontSize``
     - ``string``
     - Font size token (default: ``'sm'``).

Features
--------

* **react-markdown** with GFM support (``remark-gfm``).
* **Math** rendering via ``remark-math-extended`` and ``rehype-katex``
  (KaTeX CSS is loaded globally).
* **Syntax highlighting** for fenced code blocks using Prism with the
  ``oneDark`` theme.
* Custom CSS for headings, tables, blockquotes, and KaTeX overflow.

Source
------

``acai/ui/src/components/Markdown.tsx``
