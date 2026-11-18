"""Deduplicate the same story reported by multiple outlets."""
from __future__ import annotations

from rapidfuzz import fuzz

from .models import Event


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _richness(ev: Event) -> int:
    return len(ev.source_text or "") + len(ev.item.summary or "")


def _same_co(a: Event, b: Event) -> bool:
    ta = (a.item.ticker or "").upper()
    tb = (b.item.ticker or "").upper()
    if ta and tb:
        return ta == tb
    if ta or tb:
        return False
    na = _norm(a.item.company)
    nb = _norm(b.item.company)
    return bool(na and nb and na == nb)


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
            # Distinct EDGAR filings are never duplicates (they have unique accessions;
            # only merge when both accessions are present and equal).
            if ev.item.source == "edgar" and k.item.source == "edgar":
                if not (ev.item.accession and k.item.accession) or ev.item.accession != k.item.accession:
                    continue
            if not _same_co(ev, k):
                continue

            ratio = fuzz.token_set_ratio(_norm(ev.item.title), _norm(k.item.title))
            if ratio >= threshold:
                if _richness(ev) > _richness(k):
                    # k loses; record its URL, then adopt ev's fields.
                    if k.item.url and k.item.url not in k.also_reported_by:
                        k.also_reported_by.append(k.item.url)
                    k.item = ev.item
                    k.source_text = ev.source_text
                    k.event_type = ev.event_type
                    k.confidence = ev.confidence
                    k.magnitude = ev.magnitude
                else:
                    # ev loses; record ev's URL (never its own).
                    if ev.item.url and ev.item.url != k.item.url and ev.item.url not in k.also_reported_by:
                        k.also_reported_by.append(ev.item.url)
                matched = True
                break
        if not matched:
            kept.append(ev)
    return kept
