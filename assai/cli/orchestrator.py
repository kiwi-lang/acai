"""Run the orchestrator (work-item chaining loop)."""

from __future__ import annotations

import logging

from argklass.command import Command

from assai.cli import CommonArguments, setup

log = logging.getLogger(__name__)


class Orchestrator(Command):
    """Run the orchestrator (work-item chaining loop)."""

    name = "orchestrator"

    Arguments = CommonArguments

    @staticmethod
    def execute(args) -> int:
        config, queue = setup(args)

        from assai.agents.server import Orchestrator as Orc

        orc = Orc(config, queue)
        log.info(
            "orchestrator started  (db=%s  poll=%ds)",
            config.queue.url, config.queue.poll_interval,
        )
        orc.run()
        return 0

COMMANDS = Orchestrator
