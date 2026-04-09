"""Run the worker (dispatch loop)."""

from __future__ import annotations

import logging

from argklass.command import Command

from assai.cli import CommonArguments, setup

log = logging.getLogger(__name__)


class Worker(Command):
    """Run the worker (dispatch loop)."""

    name = "worker"

    Arguments = CommonArguments

    @staticmethod
    def execute(args) -> int:
        config, queue = setup(args)

        from assai.agents.worker import Worker as W

        w = W(config, queue)
        log.info(
            "worker started  (db=%s  llm=%s  poll=%ds)",
            config.queue.url, config.llm.endpoint, config.queue.poll_interval,
        )
        w.run()
        return 0


COMMANDS = Worker
