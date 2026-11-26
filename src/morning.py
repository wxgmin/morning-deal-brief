"""The first screen: "Top of the morning" bullets and "since the last brief" changes.

Every bullet is assembled from figures and sentences already in the snapshot (quotes, signals, rate path,
calendar, headlines). Nothing is generated freely, so the summary can never say something the panels don't.
Each bullet links to the panel it summarises.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .macro_read import fmt_change, fmt_level, ordinal


def _ok(q):
    return q and q.get("state") == "ok" and q.get("chg") is not None


def _all_quotes(snap):
    seen, out = set(), []
    for q in (snap.get("heartbeats") or []) + [x for b in snap.get("boards") or [] for x in b.get("quotes", [])]:
        if q["symbol"] not in seen and _ok(q):
            seen.add(q["symbol"])
            out.append(q)
    return out


def _pctile_words(p):
    if p is None:
        return ""
    if p >= 90:
        return f", near the top of its 1-year range ({ordinal(p)} percentile)"
    if p <= 10:
        return f", near the bottom of its 1-year range ({ordinal(p)} percentile)"
    return ""


def since_last(snap: dict, prior: dict | None) -> dict | None:
    if not prior:
        return None
    then = {q["symbol"]: q for q in _all_quotes(prior)}
    rows = []
    for q in snap.get("heartbeats") or []:
        old = then.get(q["symbol"])
        if not (_ok(q) and old and old.get("last")):
            continue
        if q.get("chg_unit") == "bp":
            delta = (q["last"] - old["last"]) * 100
            shown = "0bp" if abs(delta) < 0.5 else f"{delta:+.0f}bp"  # never print "-0bp"
        else:
            delta = (q["last"] / old["last"] - 1) * 100
            shown = "0.00%" if abs(delta) < 0.005 else f"{delta:+.2f}%"
        rows.append({"symbol": q["symbol"], "label": q["label"], "then": fmt_level(old), "now": fmt_level(q),
                     "delta": round(delta, 4), "shown": shown})
    now_ids = {s["id"]: s for s in snap.get("signals") or []}
    old_ids = {s["id"]: s for s in prior.get("signals") or []}
    comparable = "signals" in prior  # an edition from before the signal engine cannot say what is "new"
    tone, old_tone = (snap.get("tone") or {}).get("label"), (prior.get("tone") or {}).get("label")
    return {"prior_generated_at": prior.get("generated_at"), "prior_date": prior.get("brief_date"), "markets": rows,
            "tone": {"then": old_tone, "now": tone} if tone and old_tone and tone != old_tone else None,
            "new_signals": [{"id": i, "title": s["title"]} for i, s in now_ids.items() if comparable and i not in old_ids],
            "cleared_signals": [{"id": i, "title": s["title"]} for i, s in old_ids.items() if comparable and i not in now_ids]}


def top_of_morning(snap: dict, limit: int = 8) -> list:
    out = []

    def add(kind, text, section, url=None):
        out.append({"kind": kind, "text": text, "section": section, "url": url})

    tone = snap.get("tone")
    if tone:
        movers = [c for c in tone["components"] if c["score"]]
        why = "; ".join(f"{c['name']} {c['detail'].split(' (')[0]}" for c in movers[:3])
        add("tone", f"Risk tone is {tone['label'].lower()} ({tone['score']:+d} of ±{tone['max']})" + (f": {why}." if why else "."), "tone")

    quotes = _all_quotes(snap)
    scored = sorted((q for q in quotes if q.get("z") is not None), key=lambda q: -abs(q["z"]))
    if scored and abs(scored[0]["z"]) >= 1.5:
        q = scored[0]
        add("move", f"Biggest move versus normal: {q['label']} {fmt_change(q)} to {fmt_level(q)} "
                    f"({q['z']:+.1f}σ){_pctile_words(q.get('pctile_1y'))}.", "movers")

    beats = [q for q in snap.get("heartbeats") or [] if _ok(q)]
    if beats:
        def extreme(q):
            p = q.get("pctile_1y")
            return "; near 1-year high" if p is not None and p >= 90 else "; near 1-year low" if p is not None and p <= 10 else ""
        add("heartbeats", "Heartbeats: " + ", ".join(f"{q['label']} {fmt_level(q)} ({fmt_change(q)}{extreme(q)})"
                                                   for q in beats) + ".",
            "heartbeats")

    path = snap.get("rate_path")
    if path and path.get("windows"):
        w = path["windows"][0]
        side, odds = ("hike", w["p_hike"]) if (w.get("p_hike") or 0) >= (w.get("p_cut") or 0) else ("cut", w["p_cut"])
        likely = w.get("most_likely")
        text = (f"Fed path: markets price a {odds:.0f}% chance of a {side} for the window starting {w['window_start']}"
                + (f"; most likely range {likely['low']:.2f}–{likely['high']:.2f}% ({likely['p']:.0f}%)" if likely else "")
                + (f", {w['mean_chg_bp']:+.0f}bp vs the prior day" if w.get("mean_chg_bp") else "") + ".")
        add("fed", text, "ratepath", path.get("source_url"))

    signals = snap.get("signals") or []
    if signals:
        first = signals[0]["text"].split(". ")[0].rstrip(".") + "."
        add("signal", first + (f" (+{len(signals) - 1} more signals)" if len(signals) > 1 else ""), "signals")

    surprises = [i for i in snap.get("surprises") or [] if i["surprise"]["notable"]]
    if surprises:
        s = surprises[0]
        add("data", f"Data surprise: {s['country']} {s['title']} {s['actual']} vs {s['forecast']} forecast, "
                    f"{s['surprise']['read']}.", "calendar", s.get("actual_source_url"))

    results = snap.get("earnings_results") or []
    if results:
        add("earnings", "Reported earnings: " + "; ".join(
            f"{r['symbol']} EPS {r['eps_actual']} vs {r['eps_forecast']} est ({r['eps_surprise_pct']:+.1f}%, {r['result']})"
            for r in results[:3]) + ".", "calendar")

    econ = ((snap.get("calendar") or {}).get("economic") or {}).get("items") or []
    tz = snap.get("timezone", "UTC")
    today = snap.get("brief_date")
    high = [e for e in econ if e.get("impact") == "High" and not e.get("actual")
            and (e.get("local_date") or e.get("time", "")[:10]) == today]
    if high:
        def when(e):
            if e.get("all_day"):
                return ""
            try:
                return datetime.fromisoformat(e["time"]).astimezone(ZoneInfo(tz)).strftime("%H:%M ")
            except (ValueError, TypeError, KeyError):
                return ""
        add("calendar", "Due today: " + "; ".join(
            f"{when(e)}{e['country']} {e['title']}" + (f" (fcst {e['forecast']})" if e.get("forecast") else "")
            for e in high[:4]) + ".", "calendar")

    heads = (snap.get("headlines") or {}).get("top") or []
    if heads:
        h = heads[0]
        add("news", f"Top story: {h['title']} ({h['publisher']}).", "headlines", h.get("url"))
    return out[:limit]
