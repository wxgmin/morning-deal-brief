"""General market headlines: collected from the user's chosen feeds, tagged by theme, de-duplicated, ranked.

A headline is shown verbatim with its publisher, time and link. Nothing here is summarised or rewritten,
so every line on the page is exactly what a named source published.
"""
from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urljoin

import feedparser

from .. import catalog, netsafe
from ..guardrails import safe_http_url
from ..models import Company
from .rss import _attribute_company, _clean, _published

logger = logging.getLogger(__name__)
_STOP = set("a an the of to in on for and or at by with from as is are was were be it its this that new says say".split())
_SOURCE_WEIGHT = {"policy": 1.2, "markets": 1.0, "macro": 1.0, "general": 0.9, "commodities": 0.95,
                  "deals": 0.85, "credit": 0.85}
_HALF_LIFE_HOURS = 14.0
_DUPLICATE_OVERLAP = 0.7
# Institutional feeds (Fed, ECB, SEC) mix market-moving releases with routine administrative notices.
_CHAFF = re.compile(r"(?i)\b(?:challenge winners?|advisory council|community (?:development|depository)|"
                    r"reappoints?|appoints?|holiday schedule|call for nominations|meeting of the)\b")
_UNKNOWN_PUBLISHER = 0.55
_PICKUP_BONUS = 0.15  # per additional outlet carrying the same story, capped at four
_TERMS = re.compile(
    r"[-+−]?(?:[$€£]\s*)?\d[\d,.]*(?:\s?(?:%|bp|billion|million|trillion))?"
    r"|\b(?:up|down|higher|lower|rise[sn]?|fall[sn]?|gains?|drops?|rose|fell|raised?|cut|"
    r"approved?|blocked?|filed|closed?|shelved?|signed|withdrawn|denied|denies)\b", re.IGNORECASE
)


def _compile(keywords):
    return [re.compile(r"(?<!\w)" + re.escape(k.strip()) + r"s?(?!\w)", re.IGNORECASE) for k in keywords]


_THEME_PATTERNS = {key: _compile(theme["keywords"]) for key, theme in catalog.THEMES.items()}


def tag_themes(title: str) -> list:
    return [key for key, patterns in _THEME_PATTERNS.items() if any(p.search(title) for p in patterns)]


def _tokens(title: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _STOP and len(w) > 1)


def _overlap(a: frozenset, b: frozenset) -> float:
    return len(a & b) / max(len(a), len(b)) if a and b else 0.0


def _terms(title: str) -> tuple:
    return tuple(sorted(match.rstrip(".,") for match in _TERMS.findall(title.casefold())))


def authority(publisher: str) -> float:
    name = (publisher or "").lower()
    return 1.0 if any(t in name for t in catalog.TRUSTED_PUBLISHERS) else _UNKNOWN_PUBLISHER


def score(entry: dict, now: datetime, theme_weights: dict) -> float:
    age_h = max(0.0, (now - datetime.fromisoformat(entry["published_at"])).total_seconds() / 3600)
    recency = math.exp(-math.log(2) * age_h / _HALF_LIFE_HOURS)
    theme = max((theme_weights.get(t, 0.0) for t in entry["themes"]), default=0.3)
    source = _SOURCE_WEIGHT.get(entry["category"], 0.9)
    boost = 1.6 if entry["watch"] else 1.0
    pickup = 1 + _PICKUP_BONUS * min(entry.get("also", 0), 4)
    return round(theme * recency * source * boost * authority(entry["publisher"]) * pickup, 5)


def rank(entries: list, now: datetime, profile: dict) -> dict:
    cfg = profile["headlines"]
    weights = cfg["themes"]
    oldest = now - timedelta(hours=cfg["max_age_hours"])
    fresh = [e for e in entries if oldest <= datetime.fromisoformat(e["published_at"]) <= now + timedelta(minutes=10)]
    for e in fresh:
        e["score"] = score(e, now, weights)
    fresh.sort(key=lambda e: (-e["score"], e["published_at"]))

    unique, seen = [], []
    for e in fresh:
        tokens, terms = _tokens(e["title"]), _terms(e["title"])
        match = next((i for i, (other, signature, _) in enumerate(seen)
                      if terms == signature and _overlap(tokens, other) >= _DUPLICATE_OVERLAP), None)
        publisher = e["publisher"].casefold()
        if match is not None:
            outlets = seen[match][2]
            outlets.add(publisher)
            target = unique[match]
            target["also"] = len(outlets) - 1
            if publisher != target["publisher"].casefold() and len(target["also_sources"]) < 4:
                if all(source["publisher"].casefold() != publisher for source in target["also_sources"]):
                    target["also_sources"].append({"publisher": e["publisher"], "url": e["url"]})
            continue
        e["also"] = 0
        e["also_sources"] = []
        unique.append(e)
        seen.append((tokens, terms, {publisher}))
    for e in unique:  # a story several outlets carry is more likely to matter
        e["score"] = score(e, now, weights)
    unique.sort(key=lambda e: (-e["score"], e["published_at"]))

    labels = {key: theme["label"] for key, theme in catalog.THEMES.items()}
    top = unique[:cfg["max_items"]]
    by_theme = {}
    for key in catalog.THEMES:
        rows = [e for e in unique if key in e["themes"]][:4]
        if rows and weights.get(key, 0) > 0:
            by_theme[key] = {"label": labels[key], "items": rows}
    watch = [e for e in unique if e["watch"]][:10]
    for bucket in (top, watch, *(group["items"] for group in by_theme.values())):
        for e in bucket:
            e["theme_labels"] = [labels[t] for t in e["themes"]]
    return {"top": top, "by_theme": by_theme, "watchlist": watch, "considered": len(unique), "candidates": unique[:120]}


def _fetch_feed(feed: dict, coverage) -> tuple:
    url = feed["url"]
    try:
        parsed = feedparser.parse(netsafe.fetch_feed(url))
        if not parsed.version or (parsed.bozo and not parsed.entries):
            raise ValueError("invalid feed")
        out = []
        for entry in parsed.entries[:120]:
            published = _published(entry)
            title = _clean(entry.get("title") or "")
            link = safe_http_url(urljoin(url, entry.get("link") or "")) if entry.get("link") else ""
            if not (published and title and link):
                continue
            publisher = _clean((entry.get("source") or {}).get("title") or "") or feed["label"].split(" · ")[0]
            if publisher and title.endswith(" - " + publisher):  # Google News appends the outlet to the title
                title = title[: -len(publisher) - 3].rstrip()
            if feed.get("category") == "policy" and _CHAFF.search(title):
                continue
            companies = _attribute_company(title, coverage)
            out.append({"title": title, "url": link, "publisher": publisher, "feed": feed["id"],
                        "excerpt": _clean(entry.get("summary") or "")[:440],
                        "category": feed.get("category", "general"), "published_at": published.isoformat(),
                        "themes": tag_themes(title), "watch": sorted({c.ticker for c in companies if c.ticker})})
        return out, "ok"
    except Exception as exc:
        logger.warning("Headline feed %s unavailable (%s)", feed.get("id"), type(exc).__name__)
        return [], "unavailable"


def collect_headlines(profile: dict, now: datetime | None = None, *, include_candidates=False) -> dict:
    now = now or datetime.now(timezone.utc)
    feeds = [f for f in profile["feeds"] if f["enabled"]]
    coverage = SimpleNamespace(companies=[Company(r["name"], r["ticker"], r["cik"], r["role"])
                                          for r in profile["watchlist"]])
    status, entries = {}, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for feed, (items, state) in zip(feeds, pool.map(lambda f: _fetch_feed(f, coverage), feeds)):
            entries.extend(items)
            status[feed["id"]] = state
    if feeds and all(v == "unavailable" for v in status.values()):
        raise OSError("no headline feed was available")
    # A Monday brief should still show what broke over the weekend.
    effective = {**profile, "headlines": {**profile["headlines"]}}
    if now.astimezone(timezone.utc).weekday() in (0, 5, 6):
        effective["headlines"]["max_age_hours"] = min(168, profile["headlines"]["max_age_hours"] + 48)
    result = rank(entries, now, effective)
    if not include_candidates:
        result.pop("candidates", None)
    ok = sum(1 for v in status.values() if v == "ok")
    result.update({"state": "ok" if ok == len(status) else "partial", "feeds": status,
                   "feeds_ok": ok, "feeds_total": len(status)})
    return result
