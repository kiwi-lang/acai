"""Dev tools — spawner, utilities, and local development helpers.

Usage::

    acai dev serve              # start all dev services
    acai dev serve --port 5055  # override spawner port
"""

from __future__ import annotations

from argklass.command import ParentCommand


class Dev(ParentCommand):
    """Dev tools — spawner and local development helpers."""

    name: str = "dev"

    @staticmethod
    def module():
        import acai.cli.dev
        return acai.cli.dev


COMMANDS = Dev
