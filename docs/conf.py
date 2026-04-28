import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "Açaí"
copyright = "2025, Açaí contributors"
author = "Açaí contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_static_path = ["_static"]
html_title = "Açaí Documentation"

html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#7c3aed",
        "color-brand-content": "#7c3aed",
    },
    "dark_css_variables": {
        "color-brand-primary": "#a78bfa",
        "color-brand-content": "#a78bfa",
    },
}
