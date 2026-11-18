"""Deduplicate the same story reported by multiple outlets."""
from __future__ import annotations

from rapidfuzz import fuzz

from .models import Event


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _richness(ev: Event) -> int:
    return len(ev.source_text or "") + len(ev.item.summary or "")


def dedupe(events: list, threshold: int = 82) -> list:
    """Greedy single-pass clustering by company + headline similarity.

    Items are kept by richness (longest available text), and duplicates are recorded
    in ``also_reported_by`` with their source URL.
    """
    if not events:
        return []

    kept: list[Event] = []
    for ev in events:
        matched = False
        for k in kept:
            same_co = (ev.item.ticker and ev.item.ticker == k.item.ticker) or (
                not ev.item.ticker and not k.item.ticker
            )
            if not same_co:
                continue
            ratio = fuzz.token_set_ratio(_norm(ev.item.title), _norm(k.item.title))
            if ratio >= threshold:
                if ev.item.url and ev.item.url not in k.also_reported_by:
                    k.also_reported_by.append(ev.item.url)
                if _richness(ev) > _richness(k):
                    k.item = ev.item
                    k.source_text = ev.source_text
                matched = True
                break
        if not matched:
            kept.append(ev)
    return kept
