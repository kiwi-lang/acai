"""Provider selection by priority."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acai.provider.config import ProviderConfig

__all__ = ["ProviderScheduler"]


class ProviderScheduler:
    """Select providers by priority.

    ``default()`` returns the highest-priority provider.
    ``select(role)`` is kept for backward compat but simply
    delegates to ``default()``.
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

    def select(self, role: str = "") -> ProviderConfig | None:
        """Backward-compat: delegates to :meth:`default`."""
        return self.default()

    def all_for_role(self, role: str = "") -> list[ProviderConfig]:
        """Return all providers sorted by priority (highest first)."""
        return sorted(self.providers, key=lambda p: -p.priority)
