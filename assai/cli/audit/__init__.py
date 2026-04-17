"""Inspect and manage the request audit trail.

Usage::

    assai audit              # list recent audits
    assai audit clear        # delete all audit data
    assai audit plot         # save timeline PNG and open it
"""

from __future__ import annotations

import json
import os

from argklass.command import ParentCommand


class Audit(ParentCommand):
    """Inspect and manage the request audit trail."""

    name: str = "audit"

    @staticmethod
    def module():
        import assai.cli.audit
        return assai.cli.audit


# ------------------------------------------------------------------
# Shared helpers used by subcommands
# ------------------------------------------------------------------

def audit_dirs(audit_root: str) -> list[str]:
    """Return request directories sorted newest-first by mtime."""
    if not os.path.isdir(audit_root):
        return []
    dirs = []
    for name in os.listdir(audit_root):
        path = os.path.join(audit_root, name)
        if os.path.isdir(path) and not os.path.islink(path):
            dirs.append(path)
    dirs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return dirs


def load_audits(audit_root: str, limit: int) -> list[dict]:
    """Load the N most recent audit.json files."""
    audits = []
    for d in audit_dirs(audit_root)[:limit]:
        path = os.path.join(d, "audit.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                audits.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return audits


COMMANDS = Audit
