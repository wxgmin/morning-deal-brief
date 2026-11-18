"""Optional LLM refinement of rule-based event classification."""
from __future__ import annotations

from .. import llm


def classify_events(events: list, cfg) -> list:
    """Refine ambiguous/low-confidence classifications with the LLM when available."""
    if not cfg.settings.llm_enabled:
        return events

    for ev in events:
        if ev.event_type != "other" and ev.confidence >= 0.7:
            continue
        text = ev.source_text or ev.item.summary or ev.item.title
        if not (text or "").strip():
            continue
        try:
            result = llm.classify_text(cfg, ev.item.title, text)
            label = str(result.get("event_type", "")).strip()
            try:
                confidence = float(result.get("confidence", ev.confidence))
            except (TypeError, ValueError):
                confidence = ev.confidence
            confidence = max(0.0, min(1.0, confidence))
            if label in llm.TAXONOMY:
                ev.event_type = label
            ev.confidence = confidence
        except Exception:
            continue
    return events
