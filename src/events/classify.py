"""Classification uses source rules in src.events.detect."""
from __future__ import annotations


def classify_events(events: list, cfg) -> list:
    """Compatibility entry point for callers of the previous refinement stage."""
    return events
