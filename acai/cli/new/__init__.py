"""Scaffold new acai components.

Usage::

    acai new plugin --name my-tools
"""

from __future__ import annotations

from argklass.command import ParentCommand


class New(ParentCommand):
    """Scaffold new acai components (plugins, etc.)."""

    name: str = "new"

    @staticmethod
    def module():
        import acai.cli.new
        return acai.cli.new


COMMANDS = New
