"""Scaffold a new acai plugin.

Usage::

    acai new plugin --name my-tools
    acai new plugin --name my-tools --dest /tmp/plugins
"""

from __future__ import annotations

from dataclasses import dataclass

from argklass import argument
from argklass.command import Command


@dataclass
class PluginArgs:
    name: str = argument(help="plugin name (e.g. 'my-tools')")
    dest: str = argument(default="", help="parent directory (default: cwd)")


class Plugin(Command):
    """Create a new acai plugin from the built-in template."""

    name = "plugin"
    Arguments = PluginArgs

    @staticmethod
    def execute(args) -> int:
        from acai.cli.scaffold import scaffold_plugin

        dest = args.dest or None
        try:
            path = scaffold_plugin(args.name, dest=dest)
        except FileExistsError as exc:
            print(f"Error: {exc}")
            return 1

        print(f"Created plugin at {path}")
        print()
        print("Next steps:")
        print(f"  cd {path}")
        print("  pip install -e .")
        print("  # your tools are now available in acai")
        return 0


COMMANDS = Plugin
