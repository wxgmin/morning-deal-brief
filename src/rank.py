"""Rank events by banking relevance for the coverage list."""
from __future__ import annotations

from .models import Event


def rank_events(events: list, cfg) -> list:
    """Score each event and return them sorted descending by score.

    score = relevance_weight(role) x event_weight(type) x (1 + magnitude) x confidence
    """
    scored = []
    for ev in events:
        relevance = cfg.coverage.relevance_weight(ev.item.role)
        event_weight = cfg.coverage.event_weight(ev.event_type)
        magnitude = float(ev.magnitude or 0.0)
        score = relevance * event_weight * (1.0 + magnitude) * float(ev.confidence or 0.5)
        scored.append((score, ev))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [ev for _, ev in scored]
