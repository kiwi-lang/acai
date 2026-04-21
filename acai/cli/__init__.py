"""Acai command-line interface.

Usage::

    acai orchestrator       # orchestrator only
    acai worker             # worker only
    acai uber               # orchestrator + worker (GB10 mode)
    acai uber --extern-llm  # uber without internal LLM management
    acai serve              # launch LLM server standalone
    acai mcp                # standalone tool server (for sandbox containers)
    acai server             # legacy model-serving server
    acai agent-server       # agent HTTP API server
    acai knowledge list     # list knowledge documents
    acai knowledge show ID  # show a knowledge document
    acai knowledge search Q # search knowledge documents
    acai knowledge tags     # list all tags
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

from argklass import argument
from argklass.cli import CommandLineInterface

import acai.cli
from acai.orchestrator.env import apply_env

apply_env()


@dataclass
class CommonArguments:
    """Shared arguments for all agent commands."""

    config: str  = argument(default=None, help="path to a YAML config file")
    db: str      = argument(default=None, help="override queue database URL")
    verbose: bool = argument(default=False, help="enable DEBUG logging")


def setup(args):
    """Load config, create queue, return (config, queue)."""
    from acai.orchestrator.config import AcaiConfig, load_config, apply_config
    from acai.queue.work import WorkQueue

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.config:
        load_config(args.config)
    else:
        ws = os.path.abspath(os.environ.get("ACAI_WORKSPACE", "workspace"))
        auto_yaml = os.path.join(ws, "acai.yaml")
        if os.path.isfile(auto_yaml):
            log.info("auto-discovered config: %s", auto_yaml)
            load_config(auto_yaml)

    overrides = {}
    if args.db:
        overrides["queue.url"] = args.db

    if overrides:
        with apply_config(overrides):
            config = AcaiConfig()
    else:
        config = AcaiConfig()

    queue = WorkQueue(config.queue.url)
    return config, queue


def main(argv=None):
    from argklass.plugin import with_cache_location

    with with_cache_location("acai"):
        cli = CommandLineInterface(acai.cli, prog="acai", description="Acai — AI agent swarm CLI")
        cli.run(argv)
