"""Assai command-line interface.

Usage::

    assai orchestrator       # orchestrator only
    assai worker             # worker only
    assai uber               # orchestrator + worker (GB10 mode)
    assai uber --extern-llm  # uber without internal LLM management
    assai serve              # launch LLM server standalone
    assai server             # legacy model-serving server
    assai agent-server       # agent HTTP API server
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

from argklass import argument
from argklass.cli import CommandLineInterface

import assai.cli
from assai.core.env import apply_env

apply_env()


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
    else:
        ws = os.path.abspath(os.environ.get("ASSAI_WORKSPACE", "workspace"))
        auto_yaml = os.path.join(ws, "assai.yaml")
        if os.path.isfile(auto_yaml):
            log.info("auto-discovered config: %s", auto_yaml)
            load_config(auto_yaml)

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
    from argklass.plugin import with_cache_location

    with with_cache_location("assai"):
        cli = CommandLineInterface(assai.cli, prog="assai", description="Assai — AI agent swarm CLI")
        cli.run(argv)
