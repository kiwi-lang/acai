"""Model routing — select the best model from a set given complexity and budget."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from acai.provider.config import COMPLEXITY_LEVELS, ModelSet, ModelSetEntry

if TYPE_CHECKING:
    from acai.provider.config import ModelConfig, ProviderConfig

log = logging.getLogger(__name__)

__all__ = ["ModelRouter"]

_COMPLEXITY_RANK = {level: idx for idx, level in enumerate(COMPLEXITY_LEVELS)}

# Assumed average tokens per call for budget estimation (input + output).
_DEFAULT_INPUT_TOKENS = 4000
_DEFAULT_OUTPUT_TOKENS = 2000


class ModelRouter:
    """Select the best model from a :class:`ModelSet` given task constraints.

    The router considers:
    - Task complexity (only models whose ``complexity_min`` is at or below
      the requested complexity are eligible).
    - Remaining session budget (models whose estimated cost per call would
      exceed the remaining budget are excluded).
    - Model capability (``smart_weight`` from :class:`ModelConfig`) — among
      eligible models, prefer the most capable one.
    """

    def __init__(self, providers: list[ProviderConfig]):
        self._providers: dict[str, ProviderConfig] = {p.name: p for p in providers}

    def reload(self, providers: list[ProviderConfig]) -> None:
        self._providers = {p.name: p for p in providers}

    def _resolve_entry(
        self, entry: ModelSetEntry
    ) -> tuple[ProviderConfig, ModelConfig] | None:
        """Resolve an entry to a concrete (provider, model) pair."""
        prov = self._providers.get(entry.provider)
        if prov is None:
            return None
        model = prov.get_model(entry.model)
        if model is None:
            return None
        return prov, model

    def _estimate_call_cost(self, entry: ModelSetEntry) -> float:
        """Estimate the cost of a single LLM call in dollars."""
        input_cost = (_DEFAULT_INPUT_TOKENS * entry.price_input) / 1_000_000
        output_cost = (_DEFAULT_OUTPUT_TOKENS * entry.price_output) / 1_000_000
        return input_cost + output_cost

    def select(
        self,
        model_set: ModelSet,
        complexity: str = "medium",
        remaining_budget: float | None = None,
    ) -> tuple[ProviderConfig, ModelConfig, ModelSetEntry] | None:
        """Pick the best model from *model_set* for the given constraints.

        Returns ``(provider_config, model_config, entry)`` or ``None`` if
        no model qualifies.
        """
        task_rank = _COMPLEXITY_RANK.get(complexity, 1)
        candidates: list[tuple[ProviderConfig, ModelConfig, ModelSetEntry, int]] = []

        for entry in model_set.entries:
            entry_rank = _COMPLEXITY_RANK.get(entry.complexity_min, 0)
            if entry_rank > task_rank:
                continue

            if remaining_budget is not None and remaining_budget > 0:
                est_cost = self._estimate_call_cost(entry)
                if est_cost > remaining_budget:
                    continue

            resolved = self._resolve_entry(entry)
            if resolved is None:
                log.debug(
                    "model set entry %s/%s not resolvable, skipping",
                    entry.provider, entry.model,
                )
                continue

            prov, model = resolved
            candidates.append((prov, model, entry, model.smart_weight))

        if not candidates:
            return None

        # Pick the most capable model (highest smart_weight).
        # Tie-break: cheapest first.
        candidates.sort(key=lambda c: (-c[3], self._estimate_call_cost(c[2])))
        best = candidates[0]
        log.info(
            "router selected %s/%s (smart=%d, est_cost=%.6f)",
            best[2].provider, best[2].model, best[3],
            self._estimate_call_cost(best[2]),
        )
        return best[0], best[1], best[2]

    def estimate_cost(
        self,
        entry: ModelSetEntry,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Compute actual cost for a completed call."""
        return (
            (input_tokens * entry.price_input) / 1_000_000
            + (output_tokens * entry.price_output) / 1_000_000
        )
