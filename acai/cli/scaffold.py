"""Simple scaffolder for acai plugins (no cookiecutter dependency)."""

from __future__ import annotations

import os
import re
import shutil

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates", "plugin")


def _underscored(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "_", name).strip("_").lower()


def scaffold_plugin(name: str, dest: str | None = None) -> str:
    """Create a new plugin project from the built-in template.

    Parameters
    ----------
    name:
        Human-friendly plugin name (e.g. ``"my-tools"``).
    dest:
        Parent directory where ``acai-plugin-<name>/`` will be created.
        Defaults to the current working directory.

    Returns
    -------
    str
        Absolute path to the created plugin directory.
    """
    name_underscored = _underscored(name)
    project_dir = os.path.join(dest or os.getcwd(), f"acai-plugin-{name}")

    if os.path.exists(project_dir):
        raise FileExistsError(f"Directory already exists: {project_dir}")

    replacements = {
        "{{name}}": name,
        "{{name_underscored}}": name_underscored,
    }

    for root, dirs, files in os.walk(_TEMPLATE_DIR):
        rel_root = os.path.relpath(root, _TEMPLATE_DIR)
        target_root = rel_root
        for old, new in replacements.items():
            target_root = target_root.replace(old, new)
        target_path = os.path.join(project_dir, target_root)
        os.makedirs(target_path, exist_ok=True)

        for fname in files:
            src = os.path.join(root, fname)
            target_fname = fname
            for old, new in replacements.items():
                target_fname = target_fname.replace(old, new)
            dst = os.path.join(target_path, target_fname)

            with open(src) as f:
                content = f.read()
            for old, new in replacements.items():
                content = content.replace(old, new)
            with open(dst, "w") as f:
                f.write(content)

    return os.path.abspath(project_dir)
