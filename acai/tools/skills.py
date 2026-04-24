"""Skill management tools — create, list, inspect, and update user-defined skills.

These tools let the LLM (or a human) manage the skill library stored
under ``workspace/skills/<namespace>/<name>/``.  The actual *execution*
of skills happens through dynamically registered tool functions whose
qualified names follow the pattern ``skills.<namespace>.<name>``.
"""

from __future__ import annotations

import json
from typing import Optional

from acai.orchestrator.tools import tool

from acai.orchestrator.skill_store import SkillStore

_store: Optional[SkillStore] = None


def _configure(store: SkillStore) -> None:
    """Bind the active :class:`SkillStore` (called during startup)."""
    global _store
    _store = store


def _get_store() -> SkillStore:
    if _store is None:
        raise RuntimeError("skill store not configured — call _configure() first")
    return _store


@tool(permissions=("read",))
def list_skills(namespace: str = "") -> str:
    """List available skills, optionally filtered by namespace.

    Args:
        namespace: If non-empty, only skills in this namespace.
    """
    store = _get_store()
    skills = store.all_skills()
    if namespace:
        skills = [s for s in skills if s.namespace == namespace]

    out = [
        {
            "qualified_name": f"skills.{s.namespace}.{s.name}",
            "namespace": s.namespace,
            "name": s.name,
            "description": s.description[:500] if s.description else "",
        }
        for s in skills
    ]
    return json.dumps({"skills": out, "count": len(out)})


@tool(permissions=("write",))
def create_skill(
    namespace: str,
    name: str,
    description: str,
    parameters: str = "",
    code: str = "",
    readme: str = "",
) -> str:
    """Create a new skill with the given namespace and name.

    Creates ``workspace/skills/<namespace>/<name>/`` containing
    ``tool.json``, ``run.py``, and ``README.md``.

    After creation the skill is immediately available as
    ``skills.<namespace>.<name>``.

    Args:
        namespace: Skill namespace (e.g. "data_processing").
        name: Skill name (e.g. "csv_parser").
        description: What the skill does (used in tool description).
        parameters: JSON string defining input parameters schema (OpenAPI-style properties/required).
        code: Python source for run.py.  Receives JSON on stdin, must print JSON to stdout.
        readme: Markdown content for README.md.
    """
    store = _get_store()
    params = None
    if parameters:
        try:
            params = json.loads(parameters)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"invalid parameters JSON: {exc}"})

    path = store.scaffold(
        namespace=namespace,
        name=name,
        description=description,
        parameters=params,
        code=code,
        readme=readme,
    )

    _auto_register(store, namespace, name)

    return json.dumps({
        "created": True,
        "path": path,
        "qualified_name": f"skills.{namespace}.{name}",
    })


@tool(permissions=("read",))
def get_skill(namespace: str, name: str) -> str:
    """Get full details about a skill: definition, code, and README.

    Args:
        namespace: Skill namespace.
        name: Skill name.
    """
    store = _get_store()
    tool_json = store.read_file(namespace, name, "tool.json")
    if tool_json is None:
        return json.dumps({"error": f"skill {namespace}.{name} not found"})

    code = store.read_file(namespace, name, "run.py") or ""
    readme = store.read_file(namespace, name, "README.md") or ""

    try:
        definition = json.loads(tool_json)
    except json.JSONDecodeError:
        definition = {"raw": tool_json}

    return json.dumps({
        "qualified_name": f"skills.{namespace}.{name}",
        "definition": definition,
        "code": code,
        "readme": readme,
    })


@tool(permissions=("write",))
def update_skill_code(namespace: str, name: str, code: str) -> str:
    """Update a skill's run.py implementation.

    Args:
        namespace: Skill namespace.
        name: Skill name.
        code: New Python source code for run.py.
    """
    store = _get_store()
    existing = store.read_file(namespace, name, "tool.json")
    if existing is None:
        return json.dumps({"error": f"skill {namespace}.{name} not found"})

    path = store.write_file(namespace, name, "run.py", code)
    return json.dumps({"updated": True, "path": path})


@tool(permissions=("write",))
def update_skill_definition(
    namespace: str,
    name: str,
    description: str = "",
    parameters: str = "",
) -> str:
    """Update a skill's tool.json definition.

    Args:
        namespace: Skill namespace.
        name: Skill name.
        description: New description (leave empty to keep existing).
        parameters: New parameters JSON (leave empty to keep existing).
    """
    store = _get_store()
    raw = store.read_file(namespace, name, "tool.json")
    if raw is None:
        return json.dumps({"error": f"skill {namespace}.{name} not found"})

    try:
        defn = json.loads(raw)
    except json.JSONDecodeError:
        defn = {}

    if description:
        defn["description"] = description
    if parameters:
        try:
            defn["parameters"] = json.loads(parameters)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"invalid parameters JSON: {exc}"})

    store.write_file(namespace, name, "tool.json", json.dumps(defn, indent=2))

    _auto_register(store, namespace, name)

    return json.dumps({"updated": True, "qualified_name": f"skills.{namespace}.{name}"})


@tool(permissions=("write",))
def update_skill_readme(namespace: str, name: str, readme: str) -> str:
    """Update a skill's README.md.

    Args:
        namespace: Skill namespace.
        name: Skill name.
        readme: New README content (Markdown).
    """
    store = _get_store()
    existing = store.read_file(namespace, name, "tool.json")
    if existing is None:
        return json.dumps({"error": f"skill {namespace}.{name} not found"})

    path = store.write_file(namespace, name, "README.md", readme)
    return json.dumps({"updated": True, "path": path})


def _auto_register(store: SkillStore, namespace: str, name: str) -> None:
    """Re-register a single skill into the live tool registry (if available)."""
    try:
        from acai.tools.meta import _registry
        if _registry is not None:
            store.register_all(_registry)
    except Exception:
        pass
