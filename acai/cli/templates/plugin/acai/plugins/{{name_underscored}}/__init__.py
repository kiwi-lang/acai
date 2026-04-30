"""Acai plugin: {{name}}.

This module is auto-discovered by the acai orchestrator.
Public functions decorated with ``@tool`` in submodules are
registered as tools.  The ``register()`` hook below lets
you also bundle agents, skills, and workflows.
"""

from __future__ import annotations

import os

_HERE = os.path.dirname(__file__)


def register(registry, config=None):
    """Called by the orchestrator after tool discovery.

    Return a dict of resource directories to merge into the
    agent, skill, and workflow stores.
    """
    result = {}
    agents_dir = os.path.join(_HERE, "agents")
    if os.path.isdir(agents_dir):
        result["agents_dir"] = agents_dir
    skills_dir = os.path.join(_HERE, "skills")
    if os.path.isdir(skills_dir):
        result["skills_dir"] = skills_dir
    workflows_dir = os.path.join(_HERE, "workflows")
    if os.path.isdir(workflows_dir):
        result["workflows_dir"] = workflows_dir
    return result
