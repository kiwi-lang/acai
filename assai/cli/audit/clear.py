"""Delete old audit data, keeping the most recent ones.

Usage::

    assai audit clear             # keep last 10 (default)
    assai audit clear --keep 0    # delete everything
    assai audit clear --keep 20   # keep last 20
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup
from assai.cli.audit import audit_dirs


@dataclass
class ClearArguments(CommonArguments):
    keep: int = argument(default=10, help="number of recent audits to keep (0 = delete all)")


class Clear(Command):
    """Remove old audit trails, keeping the N most recent."""

    name = "clear"

    Arguments = ClearArguments

    @staticmethod
    def execute(args) -> int:
        config, _ = setup(args)
        audit_root = config.audit.dir

        if not os.path.isdir(audit_root):
            print("Nothing to clear.")
            return 0

        dirs = audit_dirs(audit_root)
        keep = max(args.keep, 0)
        to_delete = dirs[keep:]

        if not to_delete:
            print(f"Nothing to clear ({len(dirs)} audit(s), keeping {keep}).")
            return 0

        for d in to_delete:
            shutil.rmtree(d, ignore_errors=True)

        latest = os.path.join(audit_root, "latest")
        if os.path.islink(latest):
            target = os.path.join(audit_root, os.readlink(latest))
            if not os.path.isdir(target):
                os.unlink(latest)

        print(f"Cleared {len(to_delete)} audit(s), kept {min(keep, len(dirs))}")
        return 0


COMMANDS = Clear
