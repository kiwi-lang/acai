"""Tests for acai.orchestrator.projects — ProjectStore, scaffold, clone."""

from __future__ import annotations

import json
import os

import pytest

from acai.orchestrator.projects import Project, ProjectStore, scaffold, _write


@pytest.fixture()
def store(tmp_path):
    return ProjectStore(str(tmp_path / "projects"))


class TestProject:
    def test_defaults(self):
        p = Project(name="my-app")
        assert p.name == "my-app"
        assert p.language == "python"
        assert p.source == "new"
        assert p.id  # auto-generated
        assert p.created_at

    def test_custom_fields(self):
        p = Project(
            name="web",
            language="typescript",
            source="clone",
            repo_url="https://github.com/x/y",
            provider="github",
            refiner="architect",
        )
        assert p.language == "typescript"
        assert p.source == "clone"
        assert p.repo_url == "https://github.com/x/y"
        assert p.refiner == "architect"


class TestProjectStore:
    def test_save_and_get(self, store):
        p = Project(name="alpha", language="python")
        store.save(p)
        got = store.get("alpha")
        assert got is not None
        assert got.name == "alpha"
        assert got.language == "python"
        assert got.id == p.id

    def test_get_nonexistent(self, store):
        assert store.get("nope") is None

    def test_list_empty(self, store):
        assert store.list() == []

    def test_list_multiple(self, store):
        store.save(Project(name="a"))
        store.save(Project(name="b"))
        store.save(Project(name="c"))
        names = [p.name for p in store.list()]
        assert sorted(names) == ["a", "b", "c"]

    def test_delete(self, store):
        store.save(Project(name="d"))
        assert store.get("d") is not None
        store.delete("d")
        assert store.get("d") is None

    def test_delete_nonexistent_noop(self, store):
        store.delete("ghost")

    def test_save_overwrites(self, store):
        p = Project(name="x", language="python")
        store.save(p)
        p.language = "rust"
        store.save(p)
        got = store.get("x")
        assert got.language == "rust"


class TestScaffold:
    def test_scaffold_python(self, tmp_path):
        proj = Project(name="my-proj", language="python", path=str(tmp_path / "my-proj"))
        scaffold(proj)

        assert os.path.isfile(os.path.join(proj.path, "pyproject.toml"))
        assert os.path.isfile(os.path.join(proj.path, "README.md"))
        assert os.path.isdir(os.path.join(proj.path, "my_proj"))
        assert os.path.isfile(os.path.join(proj.path, "my_proj", "__init__.py"))
        assert os.path.isdir(os.path.join(proj.path, "docs"))
        assert os.path.isfile(os.path.join(proj.path, "docs", "goal.md"))
        assert os.path.isfile(os.path.join(proj.path, "docs", "overview.md"))
        assert os.path.isdir(os.path.join(proj.path, "tests"))
        assert os.path.isfile(os.path.join(proj.path, "tests", "test_my_proj.py"))
        assert os.path.isdir(os.path.join(proj.path, ".git"))

    def test_scaffold_non_python(self, tmp_path):
        proj = Project(name="web-app", language="typescript", path=str(tmp_path / "web-app"))
        scaffold(proj)

        assert os.path.isfile(os.path.join(proj.path, "README.md"))
        assert os.path.isdir(os.path.join(proj.path, "docs"))
        assert not os.path.isfile(os.path.join(proj.path, "pyproject.toml"))


class TestWrite:
    def test_creates_parents(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c.txt")
        _write(path, "hello")
        with open(path) as f:
            assert f.read() == "hello"
