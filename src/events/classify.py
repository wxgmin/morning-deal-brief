"""Optional LLM refinement of rule-based event classification."""
from __future__ import annotations

from .. import llm


def classify_events(events: list, cfg) -> list:
    """Refine ambiguous/low-confidence classifications with the LLM when available.

    Rule-based detection (src/events/detect.py) always runs first; this step only
    upgrades 'other' or low-confidence items, and silently keeps the rule-based
    label on any failure.
    """
    if not cfg.settings.llm_enabled:
        return events

    for ev in events:
        if ev.event_type != "other" and ev.confidence >= 0.7:
            continue
        try:
            text = ev.source_text or ev.item.summary or ev.item.title
            result = llm.classify_text(cfg, ev.item.title, text)
            label = str(result.get("event_type", "")).strip()
            if label in llm.TAXONOMY:
                ev.event_type = label
            ev.confidence = float(result.get("confidence", ev.confidence))
        except Exception:
            continue
    return events
