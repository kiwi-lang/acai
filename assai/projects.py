"""Project creation and scaffolding.

Metadata lives in ``workspace/projects/<name>/definition.json``.
Actual project files live in ``workspace/.worktrees/<name>/``.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class Project:
    id: str = ""
    name: str = ""
    language: str = "python"
    source: str = "new"                     # "new" | "clone"
    template: str = "default"
    repo_url: str = ""
    provider: str = ""                      # github | gitlab | bitbucket | ""
    path: str = ""                          # actual code location (worktree)
    python_version: str = "3.12"
    venv_path: str = ".venv"
    created_at: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class ProjectStore:
    """CRUD for project metadata.

    Layout::

        projects/
        └── my-project/
            └── definition.json
    """

    def __init__(self, projects_dir: str):
        self.root = projects_dir
        os.makedirs(self.root, exist_ok=True)

    def _dir(self, name: str) -> str:
        return os.path.join(self.root, name)

    def _path(self, name: str) -> str:
        return os.path.join(self._dir(name), "definition.json")

    def save(self, project: Project):
        d = self._dir(project.name)
        os.makedirs(d, exist_ok=True)
        with open(self._path(project.name), "w") as f:
            json.dump(asdict(project), f, indent=2)

    def get(self, name: str) -> Project | None:
        path = self._path(name)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return Project(**json.load(f))

    def list(self) -> list[Project]:
        projects = []
        if not os.path.isdir(self.root):
            return projects
        for entry in sorted(os.listdir(self.root)):
            defn = os.path.join(self.root, entry, "definition.json")
            if os.path.isfile(defn):
                with open(defn) as f:
                    projects.append(Project(**json.load(f)))
        return projects

    def delete(self, name: str):
        import shutil
        d = self._dir(name)
        if os.path.isdir(d):
            shutil.rmtree(d)


# ------------------------------------------------------------------
# Scaffolding
# ------------------------------------------------------------------

def _write(path: str, content: str = ""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def scaffold(project: Project):
    """Create directory structure for a new project at ``project.path``."""
    root = project.path
    os.makedirs(root, exist_ok=True)

    _scaffold_docs(root, project)
    _scaffold_tests(root, project)

    if project.language == "python":
        _scaffold_python(root, project)
    else:
        _write(os.path.join(root, "README.md"), f"# {project.name}\n")

    _git_init(root)


def clone(project: Project):
    """Clone a repo into ``project.path``."""
    subprocess.run(
        ["git", "clone", project.repo_url, project.path],
        check=True,
    )
    _scaffold_docs(project.path, project)
    _scaffold_tests(project.path, project)


def _scaffold_docs(root: str, project: Project):
    docs = os.path.join(root, "docs")

    _write(os.path.join(docs, "goal.md"), textwrap.dedent(f"""\
        # Goal

        Describe the goal of **{project.name}** here.
    """))

    _write(os.path.join(docs, "overview.md"), textwrap.dedent(f"""\
        # Overview

        High-level architecture of **{project.name}**.
    """))

    os.makedirs(os.path.join(docs, "components"), exist_ok=True)
    _write(os.path.join(docs, "components", ".gitkeep"))

    os.makedirs(os.path.join(docs, "recipes"), exist_ok=True)
    _write(os.path.join(docs, "recipes", ".gitkeep"))


def _scaffold_tests(root: str, project: Project):
    tests_dir = os.path.join(root, "tests")
    os.makedirs(tests_dir, exist_ok=True)

    slug = project.name.replace("-", "_").replace(" ", "_").lower()
    test_file = os.path.join(tests_dir, f"test_{slug}.py")

    if project.language == "python":
        _write(test_file, textwrap.dedent(f"""\
            \"\"\"Tests for {project.name}.\"\"\"


            def test_placeholder():
                assert True
        """))
    else:
        _write(test_file, f"# Tests for {project.name}\n")


def _scaffold_python(root: str, project: Project):
    slug = project.name.replace("-", "_").replace(" ", "_").lower()

    _write(os.path.join(root, slug, "__init__.py"), "")

    _write(os.path.join(root, "pyproject.toml"), textwrap.dedent(f"""\
        [project]
        name = "{project.name}"
        version = "0.1.0"
        requires-python = ">={project.python_version}"

        [build-system]
        requires = ["setuptools>=68"]
        build-backend = "setuptools.build_meta"
    """))

    _write(os.path.join(root, "README.md"), f"# {project.name}\n")


def _git_init(root: str):
    if not os.path.isdir(os.path.join(root, ".git")):
        subprocess.run(["git", "init"], cwd=root, check=True,
                       capture_output=True)
