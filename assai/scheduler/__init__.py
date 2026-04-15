"""Scheduler package — provider selection by role and priority.

Usage::

    from assai.scheduler import ProviderScheduler

    sched = ProviderScheduler(config.providers)
    prov = sched.select("worker")     # best provider for the worker role
    prov = sched.default()            # highest-priority provider overall
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assai.orchestrator.config import ProviderConfig

__all__ = ["ProviderScheduler"]


class ProviderScheduler:
    """Select providers by role preference and priority.

    Each provider has an ordered ``roles`` list (e.g. ``["worker", "curator"]``)
    and a numeric ``priority`` (higher = preferred).

    ``select(role)`` filters providers that list *role* in their roles,
    then sorts by ``(role_index, -priority)`` — providers who list the
    role earlier and have higher priority win.

    ``default()`` simply returns the highest-priority provider regardless
    of role.
    """

    def __init__(self, providers: list[ProviderConfig]):
        self.providers = list(providers)

    def reload(self, providers: list[ProviderConfig]) -> None:
        self.providers = list(providers)

    def default(self) -> ProviderConfig | None:
        """Return the highest-priority provider."""
        if not self.providers:
            return None
        return max(self.providers, key=lambda p: p.priority)

    def select(self, role: str) -> ProviderConfig | None:
        """Pick the best provider for *role*.

        Returns ``None`` if no provider lists *role* (falls back to
        ``default()`` at the call site).
        """
        candidates = []
        for p in self.providers:
            if role in p.roles:
                idx = p.roles.index(role)
                candidates.append((idx, -p.priority, p))
        if not candidates:
            return self.default()
        candidates.sort(key=lambda t: (t[0], t[1]))
        return candidates[0][2]

    def all_for_role(self, role: str) -> list[ProviderConfig]:
        """Return all providers that can serve *role*, best first."""
        candidates = []
        for p in self.providers:
            if role in p.roles:
                idx = p.roles.index(role)
                candidates.append((idx, -p.priority, p))
        candidates.sort(key=lambda t: (t[0], t[1]))
        return [c[2] for c in candidates]
