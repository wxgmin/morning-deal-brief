"""Alerts: the few things worth interrupting someone for, derived from a snapshot.

Rules (thresholds in the profile, editable in Preferences):
  * a watchlist name moves at least `move_pct` (regular session, pre-market or after-hours);
  * a sourced signal fires that was not firing in the last brief;
  * a data release misses its forecast by a notable margin;
  * a watchlist company appears in market-wide SEC deal filings;
  * a headline matches one of the user's keywords.
Pushing is optional: set NTFY_TOPIC (and optionally NTFY_SERVER) to receive new alerts on a phone via ntfy.
Already-sent alerts are remembered per day so a 15-minute schedule never repeats itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from .archive import _atomic_write
from .guardrails import safe_http_url

logger = logging.getLogger(__name__)


def evaluate(snap: dict, rules: dict) -> list:
    if not rules.get("enabled", True):
        return []
    out = []

    def add(kind, text, section, url=None):
        key = hashlib.sha1(f"{snap.get('brief_date')}|{kind}|{text}".encode()).hexdigest()[:16]
        out.append({"id": key, "kind": kind, "text": text, "section": section, "url": url})

    threshold = float(rules.get("move_pct", 3.0))
    for q in snap.get("watchlist") or []:
        if q.get("state") != "ok" or q.get("chg") is None:
            continue
        if abs(q["chg"]) >= threshold:
            add("move", f"{q['label']} {q['chg']:+.2f}% to {q['last']:,.2f}", "watchlist", q.get("source_url"))
        ext = q.get("extended")
        if ext and abs(ext.get("pct") or 0) >= threshold:
            add("move", f"{q['label']} {ext['label'].lower()} {ext['pct']:+.2f}%", "watchlist", q.get("source_url"))
    new = (snap.get("since") or {}).get("new_signals") or []
    if new:  # one alert, not one per signal
        add("signal", ("New signal: " if len(new) == 1 else f"{len(new)} new signals: ")
            + ", ".join(x["title"] for x in new), "signals")
    for item in snap.get("surprises") or []:
        if item["surprise"]["notable"]:
            add("data", f"{item.get('country', '')} {item['title']} {item['actual']} vs {item['forecast']} forecast "
                        f"({item['surprise']['read']})", "calendar", item.get("actual_source_url"))
    for key, group in ((snap.get("dealflow") or {}).get("groups") or {}).items():
        for r in group.get("items") or []:
            if r.get("watch"):
                add("filing", f"{r['company']}: {r['form']} filed {r['date']} ({group.get('label', key)})", "dealflow", r.get("url"))
    for r in (((snap.get("dealflow") or {}).get("groups") or {}).get("insiders") or {}).get("items") or []:
        if r.get("cluster") and not r.get("watch"):  # watchlist rows already alerted above
            add("filing", f"Insider cluster buy at {r['company']}: {r['insider']}, ${r['value']:,.0f}", "dealflow", r.get("url"))
    words = [w.strip() for w in rules.get("keywords") or [] if w.strip()]
    if words:
        pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in words) + r")(?!\w)", re.IGNORECASE)
        for h in ((snap.get("headlines") or {}).get("top") or []):
            m = pattern.search(h["title"])
            if m:
                add("keyword", f"“{m.group(1)}” in {h['publisher']}: {h['title']}", "headlines", h.get("url"))
    seen, unique = set(), []
    for a in out:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique.append(a)
    return unique[:20]


def _state_path(settings) -> Path:
    return Path(settings.archive_dir).parent / "alerts-sent.json"


def push_new(alerts: list, settings, *, post=requests.post) -> list:
    """Send alerts not sent before today. Returns those sent. A no-op unless NTFY_TOPIC is configured."""
    topic = os.getenv("NTFY_TOPIC")
    if not topic or not alerts:
        return []
    server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not safe_http_url(server) or not server.startswith("https://") or not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", topic):
        logger.warning("Alert push skipped: NTFY_SERVER must be https and NTFY_TOPIC alphanumeric")
        return []
    path = _state_path(settings)
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    sent_today = set(state.get(today, []))
    sent = []
    for a in alerts:
        if a["id"] in sent_today:
            continue
        try:
            response = post(f"{server}/{topic}", data=a["text"].encode("utf-8"), timeout=(4, 8), allow_redirects=False,
                            headers={"Title": "Morning brief alert", "Tags": a["kind"],
                                     **({"Click": a["url"]} if a.get("url") and safe_http_url(a["url"]) else {})})
            if response.status_code < 300:
                sent.append(a)
                sent_today.add(a["id"])
        except requests.RequestException:
            logger.warning("Alert push failed for one alert")
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps({today: sorted(sent_today)}))  # only today's ledger is kept
    return sent
