"""Rank coverage events with explicit editorial priority and bounded boosts."""
from __future__ import annotations

import math

_MATERIAL = {"M&A", "financing", "activist", "rating_change", "guidance_change",
             "bankruptcy", "impairment", "financial_reporting"}


def _finite(value, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def rank_events(events: list, cfg) -> list:
    def priority(event):
        relevance = max(0, _finite(cfg.coverage.relevance_weight(event.item.role), 0.5))
        weight = max(0, _finite(cfg.coverage.event_weight(event.event_type), 0.2))
        confidence = max(0, min(1, _finite(event.confidence, 0.5)))
        material_client = event.item.role == "client" and event.event_type in _MATERIAL and confidence >= 0.7
        magnitude = min(8, max(0, _finite(event.magnitude, 0)))
        event.score = relevance * weight * confidence * (1 + 0.10 * math.log1p(magnitude))
        timestamp = event.item.extra.get("available_at") or event.item.published
        return (weight > 0 and relevance > 0, material_client, event.score, timestamp,
                event.item.ticker, event.item.title)

    return sorted(events, key=priority, reverse=True)
