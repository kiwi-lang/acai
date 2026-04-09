"""Assai command-line interface.

Usage::

    assai orchestrator       # orchestrator only
    assai worker             # worker only
    assai uber               # orchestrator + worker (GB10 mode)
    assai server             # legacy model-serving server
    assai agent-server       # agent HTTP API server
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from argklass import argument
from argklass.cli import CommandLineInterface

import assai.cli


@dataclass
class CommonArguments:
    """Shared arguments for all agent commands."""

    config: str  = argument(default=None, help="path to a YAML config file")
    db: str      = argument(default=None, help="override queue database URL")
    verbose: bool = argument(default=False, help="enable DEBUG logging")


def setup(args):
    """Load config, create queue, return (config, queue)."""
    from assai.core.config import AssaiConfig, load_config, apply_config
    from assai.queue.work import WorkQueue

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.config:
        load_config(args.config)

    overrides = {}
    if args.db:
        overrides["queue.url"] = args.db

    if overrides:
        with apply_config(overrides):
            config = AssaiConfig()
    else:
        config = AssaiConfig()

    queue = WorkQueue(config.queue.url)
    return config, queue


def main(argv=None):
    cli = CommandLineInterface(assai.cli, prog="assai", description="Assai — AI agent swarm CLI")
    cli.run(argv)
