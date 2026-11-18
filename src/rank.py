"""Rank events by banking relevance for the coverage list."""
from __future__ import annotations

import math

from .models import Event


def _finite(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def rank_events(events: list, cfg) -> list:
    """Score each event and return them sorted descending by score.

    score = relevance_weight x event_weight x (1 + log1p(magnitude)) x confidence.
    The log compression stops large price moves from swamping higher-weight editorial
    signals; all factors are sanitized against NaN/inf/null.
    """
    scored = []
    for ev in events:
        relevance = _finite(cfg.coverage.relevance_weight(ev.item.role), 0.5)
        event_weight = _finite(cfg.coverage.event_weight(ev.event_type), 0.2)
        magnitude = max(0.0, _finite(ev.magnitude, 0.0))
        confidence = max(0.0, min(1.0, _finite(ev.confidence, 0.5)))
        mag_factor = 1.0 + math.log1p(magnitude)
        score = relevance * event_weight * mag_factor * confidence
        scored.append((score, ev))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [ev for _, ev in scored]
