"""List recent audit trails.

Usage::

    assai audit list
    assai audit list --last 20
"""

from __future__ import annotations

from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup
from assai.cli.audit import load_audits


@dataclass
class ListArguments(CommonArguments):
    last: int = argument(default=20, help="number of recent audits to show")


class List(Command):
    """Show a summary table of recent audit trails."""

    name = "list"

    Arguments = ListArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)
        audit_root = config.audit.dir

        audits = load_audits(audit_root, args.last)
        if not audits:
            print(f"No audits in {audit_root}")
            return 0

        print(f"{'ID':<18} {'Endpoint':<18} {'Duration':>10}  Started")
        print("-" * 70)
        for a in audits:
            rid = a.get("request_id", "?")
            ep = a.get("meta", {}).get("endpoint", "?")
            dur = a.get("total_duration_ms", 0)
            ts = a.get("started_at_iso", "?")
            print(f"{rid:<18} {ep:<18} {dur:>8.1f}ms  {ts}")

        return 0


COMMANDS = List
