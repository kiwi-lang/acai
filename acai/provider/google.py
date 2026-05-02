"""Google Generative AI model fetching (no adapter yet)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from acai.provider.config import ProviderConfig

log = logging.getLogger(__name__)


def fetch_models(prov: ProviderConfig) -> list[dict]:
    """Fetch available models from the Google /v1beta/models endpoint."""
    ep = prov.endpoint.rstrip("/")
    url = f"{ep}/v1beta/models"
    params = {}
    if prov.api_key:
        params["key"] = prov.api_key
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("models", [])
    models = []
    for m in data:
        mid = m.get("name", "").replace("models/", "")
        display = m.get("displayName", mid)
        models.append({
            "name": display,
            "slug": mid,
            "max_tokens": m.get("outputTokenLimit", 0),
            "context_window": m.get("inputTokenLimit", 0),
            "cost_weight": 10,
            "smart_weight": 10,
        })
    return models
