"""Skill store — discover, register, and execute user-defined skills.

Skills are ad-hoc tools that live on disk as simple directories::

    workspace/skills/<namespace>/<skill_name>/
        tool.json    # MCP-style tool definition (description + parameters)
        run.py       # Python entry point (stdin JSON → stdout JSON)
        README.md    # Development notes for iterative improvement

The :class:`SkillStore` scans the skills directory, builds dynamic
callables for each valid skill, and registers them into a
:class:`ToolRegistry` under ``skills.<namespace>`` namespaces.

Because skills execute arbitrary user code, every skill tool is
annotated with ``sandbox=True`` and ``permissions=("execute",)``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_generation: int = 0


def _bump_generation() -> None:
    """Increment the module-level generation counter.

    All ``SkillStore`` instances in the same process see the bump
    and will re-discover on next read.
    """
    global _generation
    _generation += 1


@dataclass
class SkillDef:
    """Metadata for a discovered skill."""

    namespace: str
    name: str
    path: str
    description: str = ""
    parameters: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    has_requirements: bool = False


_TOOL_JSON = "tool.json"
_RUN_PY = "run.py"
_README = "README.md"
_REQUIREMENTS = "requirements.txt"


class SkillStore:
    """Discovers skills on disk and registers them as tools.

    Parameters
    ----------
    skills_dir:
        Absolute path to the ``workspace/skills`` directory.
    """

    def __init__(self, skills_dir: str) -> None:
        self.skills_dir = skills_dir
        self._skills: dict[str, SkillDef] = {}
        self._extra_dirs: list[str] = []
        self._gen: int = -1

    @property
    def dir(self) -> str:
        return self.skills_dir

    def scoped(self, *extra_dirs: str):
        """Context manager that temporarily adds skill directories.

        Skills from *extra_dirs* are discovered and merged into
        ``_skills`` on entry and removed on exit.

        Usage::

            with skill_store.scoped(workflow_skills_dir):
                skills = skill_store.all_skills()
        """
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            added_keys: list[str] = []
            added_dirs: list[str] = []
            for d in extra_dirs:
                if d and os.path.isdir(d) and d not in self._extra_dirs:
                    self._extra_dirs.append(d)
                    added_dirs.append(d)
                    tmp = SkillStore(d)
                    for sd in tmp.discover():
                        key = f"skills.{sd.namespace}.{sd.name}"
                        if key not in self._skills:
                            self._skills[key] = sd
                            added_keys.append(key)
            try:
                yield self
            finally:
                for key in added_keys:
                    self._skills.pop(key, None)
                for d in added_dirs:
                    try:
                        self._extra_dirs.remove(d)
                    except ValueError:
                        pass

        return _ctx()

    def discover(self) -> list[SkillDef]:
        """Scan *skills_dir* for valid skill directories.

        A valid skill has both ``tool.json`` and ``run.py``.
        """
        skills: list[SkillDef] = []
        if not os.path.isdir(self.skills_dir):
            return skills

        for ns_name in sorted(os.listdir(self.skills_dir)):
            ns_path = os.path.join(self.skills_dir, ns_name)
            if not os.path.isdir(ns_path) or ns_name.startswith((".", "_")):
                continue

            for skill_name in sorted(os.listdir(ns_path)):
                skill_path = os.path.join(ns_path, skill_name)
                if not os.path.isdir(skill_path) or skill_name.startswith((".", "_")):
                    continue

                tool_json = os.path.join(skill_path, _TOOL_JSON)
                run_py = os.path.join(skill_path, _RUN_PY)
                if not (os.path.isfile(tool_json) and os.path.isfile(run_py)):
                    log.debug("skipping %s/%s (missing tool.json or run.py)", ns_name, skill_name)
                    continue

                try:
                    defn = _load_json(tool_json)
                except Exception as exc:
                    log.warning("invalid tool.json in %s/%s: %s", ns_name, skill_name, exc)
                    continue

                params = defn.get("parameters", {})
                sd = SkillDef(
                    namespace=ns_name,
                    name=skill_name,
                    path=skill_path,
                    description=defn.get("description", ""),
                    parameters=params.get("properties", {}),
                    required=params.get("required", []),
                    has_requirements=os.path.isfile(
                        os.path.join(skill_path, _REQUIREMENTS)
                    ),
                )
                skills.append(sd)

        self._skills = {f"skills.{s.namespace}.{s.name}": s for s in skills}
        self._gen = _generation
        return skills

    def _maybe_refresh(self) -> None:
        """Re-discover if the global generation has moved past our snapshot."""
        if not self._extra_dirs and self._gen != _generation:
            self.discover()

    def get(self, qualified_name: str) -> SkillDef | None:
        self._maybe_refresh()
        return self._skills.get(qualified_name)

    def all_skills(self) -> list[SkillDef]:
        self._maybe_refresh()
        return list(self._skills.values())

    def register_all(self, registry: "ToolRegistry") -> int:
        """Discover skills and register each as a tool in *registry*.

        Returns the number of skills registered.
        """
        from acai.orchestrator.tools import ToolDef

        skills = self.discover()
        count = 0
        for sd in skills:
            try:
                td = _skill_to_tooldef(sd)
                registry._tools[td.qualified_name] = td
                registry._namespaces.setdefault(td.namespace, [])
                if td.qualified_name not in registry._namespaces[td.namespace]:
                    registry._namespaces[td.namespace].append(td.qualified_name)
                count += 1
            except Exception as exc:
                log.warning(
                    "failed to register skill %s.%s: %s",
                    sd.namespace, sd.name, exc,
                )

        if count:
            log.info("registered %d skills from %s", count, self.skills_dir)
        return count

    def scaffold(
        self,
        namespace: str,
        name: str,
        description: str,
        parameters: dict | None = None,
        code: str = "",
        readme: str = "",
        requirements: str = "",
    ) -> str:
        """Create a new skill directory with starter files.

        Returns the absolute path to the new skill directory.
        """
        skill_dir = os.path.join(self.skills_dir, namespace, name)
        os.makedirs(skill_dir, exist_ok=True)

        if parameters is None:
            parameters = {"type": "object", "properties": {}, "required": []}

        tool_def = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }
        _write(os.path.join(skill_dir, _TOOL_JSON), json.dumps(tool_def, indent=2))

        if not code:
            code = _DEFAULT_RUN_PY

        _write(os.path.join(skill_dir, _RUN_PY), code)

        if not readme:
            readme = f"# {namespace}.{name}\n\n{description}\n"

        _write(os.path.join(skill_dir, _README), readme)

        if requirements:
            _write(os.path.join(skill_dir, _REQUIREMENTS), requirements)

        _bump_generation()
        return skill_dir

    def read_file(self, namespace: str, name: str, filename: str) -> str | None:
        """Read a file from a skill directory."""
        path = os.path.join(self.skills_dir, namespace, name, filename)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def write_file(self, namespace: str, name: str, filename: str, content: str) -> str:
        """Write (or overwrite) a file in a skill directory."""
        skill_dir = os.path.join(self.skills_dir, namespace, name)
        os.makedirs(skill_dir, exist_ok=True)
        path = os.path.join(skill_dir, filename)
        _write(path, content)
        _bump_generation()
        return path


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _ensure_deps(skill_dir: str) -> None:
    """Install ``requirements.txt`` if present and not already up-to-date.

    Uses a ``.deps_installed`` marker containing a hash of the
    requirements content to avoid redundant pip invocations.
    """
    import hashlib

    req_path = os.path.join(skill_dir, _REQUIREMENTS)
    if not os.path.isfile(req_path):
        return

    with open(req_path, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return

    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    marker = os.path.join(skill_dir, ".deps_installed")
    if os.path.isfile(marker):
        with open(marker) as f:
            if f.read().strip() == content_hash:
                return

    log.info("installing skill deps from %s", req_path)
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-r", req_path],
            capture_output=True, text=True, timeout=120,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        log.error("pip install failed for %s: %s", req_path,
                  exc.stderr[:2000] if exc.stderr else "")
        raise RuntimeError(
            f"Failed to install skill dependencies: {exc.stderr[:500]}"
        ) from exc

    with open(marker, "w") as f:
        f.write(content_hash)


def execute_skill(run_py: str, args: dict, cwd: str, timeout: int = 300) -> str:
    """Run a skill's ``run.py`` with *args* as JSON on stdin.

    Returns a JSON string with either the skill's output or an error.
    """
    try:
        _ensure_deps(cwd)
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})

    input_data = json.dumps(args, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [sys.executable, run_py],
            input=input_data,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "skill execution timed out", "timeout": timeout})
    except Exception as exc:
        return json.dumps({"error": f"skill execution failed: {exc}"})

    if proc.returncode != 0:
        return json.dumps({
            "error": f"skill exited with code {proc.returncode}",
            "stderr": proc.stderr[:4000] if proc.stderr else "",
            "stdout": proc.stdout[:2000] if proc.stdout else "",
        })

    stdout = proc.stdout.strip()
    try:
        json.loads(stdout)
        return stdout
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"output": stdout})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _skill_to_tooldef(sd: SkillDef) -> "ToolDef":
    """Build a :class:`ToolDef` for a skill, wrapping execution in a closure."""
    from acai.orchestrator.tools import ToolDef

    run_py = os.path.join(sd.path, _RUN_PY)
    skill_path = sd.path

    def _run(**kwargs: Any) -> str:
        return execute_skill(run_py, kwargs, skill_path)

    _run.__name__ = sd.name
    _run.__qualname__ = f"skills.{sd.namespace}.{sd.name}"
    _run.__doc__ = sd.description

    namespace = f"skills.{sd.namespace}"
    return ToolDef(
        namespace=namespace,
        name=sd.name,
        qualified_name=f"{namespace}_{sd.name}",
        description=sd.description,
        parameters=sd.parameters,
        required=sd.required,
        fn=_run,
        gpu=False,
        permissions=("execute",),
        sandbox=True,
    )


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


_DEFAULT_RUN_PY = '''\
"""Skill entry point.

Reads JSON input from stdin, processes it, and writes JSON output to stdout.
"""

import json
import sys


def main():
    args = json.load(sys.stdin)

    # TODO: implement skill logic here
    result = {"message": "skill executed", "input": args}

    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
'''
