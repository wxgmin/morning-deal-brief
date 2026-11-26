"""Render the market snapshot as HTML (shared partial), Markdown and Slack blocks from one data structure."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from .guardrails import safe_http_url
from .macro_read import fmt_change, fmt_level, ordinal  # noqa: F401

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
STATIC = Path(__file__).resolve().parent.parent / "static"
_DASH = "—"


def dirclass(q: dict) -> str:
    chg = q.get("chg")
    return "flat" if not isinstance(chg, (int, float)) or abs(chg) < 1e-9 else "up" if chg > 0 else "down"


def lt(iso, tz, fmt="%b %d %H:%M"):
    try:
        return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime(fmt).replace(" 0", " ")
    except (ValueError, TypeError):
        return ""


def pct(value, spec="+.1f"):
    return format(value, spec) + "%" if isinstance(value, (int, float)) else _DASH


def signed(value, unit):
    if not isinstance(value, (int, float)):
        return _DASH
    return f"{value:+.0f}bp" if unit == "bp" else f"{value:+.2f}%"


def money(value):
    if not isinstance(value, (int, float)):
        return _DASH
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= limit:
            return f"${value / limit:,.1f}{suffix}"
    return f"${value:,.0f}"


def spark_points(values, width=84, height=22):
    values = [v for v in (values or []) if isinstance(v, (int, float))]
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = width / (len(values) - 1)
    return " ".join(f"{i * step:.1f},{height - 2 - (v - low) / span * (height - 4):.1f}" for i, v in enumerate(values))


def curve_geometry(curve, width=360, height=96):
    points = (curve or {}).get("points") or []
    if len(points) < 2:
        return None
    values = [p["yield"] for p in points] + [p["prev"] for p in points if p.get("prev") is not None]
    low, high = min(values) - 0.05, max(values) + 0.05
    span = high - low
    step = (width - 24) / (len(points) - 1)

    def line(key):
        rows = [(12 + i * step, height - 16 - (p[key] - low) / span * (height - 30))
                for i, p in enumerate(points) if p.get(key) is not None]
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in rows)

    labels = [(round(12 + i * step, 1), p["tenor"]) for i, p in enumerate(points)]
    return {"today": line("yield"), "prior": line("prev"), "labels": labels, "w": width, "h": height}


HELPERS = {
    "lvl": fmt_level, "chg": fmt_change, "ordinal": ordinal, "dirclass": dirclass, "lt": lt, "pct": pct, "signed": signed,
    "money": money, "spark_points": spark_points, "curve_geometry": curve_geometry,
}


def _env():
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.filters["safeurl"] = safe_http_url
    env.globals.update(HELPERS)
    return env


_ENV = None


def shared_css() -> str:
    try:
        return (STATIC / "markets.css").read_text(encoding="utf-8")
    except OSError:
        return ""


def html_block(snap: dict, part: str = "all") -> Markup:
    """Server-rendered, autoescaped HTML for the brief and email. `part` splits around the deals section."""
    global _ENV
    if _ENV is None:
        _ENV = _env()
    return Markup(_ENV.get_template("_snapshot.html").render(snap=snap, part=part, live=False))


# ───────────────────────────── Markdown ─────────────────────────────

def _split(snap, part):
    sections = [s for s in snap.get("sections", []) if s["enabled"]]
    ids = [s["id"] for s in sections]
    if "deals" in ids:
        cut = ids.index("deals")
        before, after = ids[:cut], ids[cut + 1:]
    else:
        before, after = ids, []
    return {"before": before, "after": after, "all": [i for i in ids if i != "deals"]}[part]


def _release_time(entry, tz, spec):
    if entry.get("all_day") and entry.get("local_date"):
        return entry["local_date"]
    return lt(entry["time"], tz, spec)


def markdown(snap: dict, part: str = "all") -> list:
    from .brief import _link, _md

    tz = snap.get("timezone", "UTC")
    out = []

    def table(headers, rows):
        out.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
        out.extend("| " + " | ".join(row) + " |" for row in rows)
        out.append("")

    def qrow(q):
        if q.get("state") == "unavailable":
            return [_md(q.get("label")), "unavailable", _DASH, _DASH, _link(q.get("source_url"), "source")]
        source = _link(q.get("source_url"), q.get("provider", "source"))
        label = " (stale)" if q.get("state") == "stale" else ""
        return [_md(q.get("label")), _md(fmt_level(q)), _md(fmt_change(q)),
                _md(signed(q.get("chg_5d"), q.get("chg_unit"))),
                source + " " + _md(lt(q.get("as_of"), tz) + label)]

    def note(read):
        if not read:
            return ""
        context = read.get("context")
        prefix = ("Possible catalyst · " + _link(context["url"], context["title"]) + " · ") if context else ""
        return prefix + _md(read["impact"])

    if part in ("before", "all"):
        desk = snap.get("desk_read") or {}
        if desk.get("items"):
            heading = "## Before the open" + (" · ChatGPT plan" if desk["state"] == "chatgpt" else "")
            out += [heading, ""]
            for item in desk["items"]:
                stamp = " · " + _md(_release_time(item, tz, "%a %b %d %H:%M")) if item.get("time") else ""
                out.append(f"- {_link(item['source']['url'], item['title'])}{stamp} · {note(item)}")
            out.append("")
        issues = [f"{name} ({status['state']})" for name, status in snap.get("sources", {}).items()
                  if status["state"] not in ("ok", "disabled")]
        if issues:
            out.extend(["Market source issues: " + _md(", ".join(issues)), ""])

    ai_panels = (snap.get("morning_note") or {}).get("panels") or {}
    for sid in _split(snap, part):
        start = len(out)
        _section_markdown(sid, snap, out, table, qrow, note, tz)
        # A checked AI read goes directly under its panel heading (heading line, blank line, then the read).
        if sid in ai_panels and len(out) > start + 1:
            read = " ".join(x["text"] for x in ai_panels[sid])
            out[start + 2:start + 2] = [f"> **AI read:** {_md(read)}", ""]
    return out


def _section_markdown(sid, snap, out, table, qrow, note, tz):
    from .brief import _link, _md
    if True:
        if sid == "summary" and snap.get("summary"):
            out += ["## Top of the morning", ""]
            ai_note = snap.get("morning_note") or {}  # not "note": that name is a helper in this function
            if ai_note.get("state") == "ok":
                out += [_md(" ".join(s["text"] for s in ai_note["sentences"])),
                        "", "_AI-written from the facts below; every figure checked against its source panel._", ""]
            out += [f"{n}. {_md(b['text'])}" for n, b in enumerate(snap["summary"], 1)]
            since = snap.get("since")
            if since and since.get("markets"):
                out += ["", f"**Since the last brief ({_md(since['prior_date'])}):** " + _md("; ".join(
                    f"{r['label']} {r['shown']}" for r in since["markets"]))]
            out.append("")
        elif sid == "ratepath" and snap.get("rate_path"):
            rp = snap["rate_path"]
            out += [f"## Fed path (market-implied, as of {_md(rp['as_of'])})", ""]
            table(["Window from", "Most likely", "Cut", "Hold", "Hike", "Mean"], [[
                _md(w["window_start"]),
                _md(f"{w['most_likely']['low']:.2f}–{w['most_likely']['high']:.2f}% ({w['most_likely']['p']:.0f}%)") if w.get("most_likely") else _DASH,
                f"{w.get('p_cut') or 0:.0f}%", f"{w.get('p_hold') or 0:.0f}%", f"{w.get('p_hike') or 0:.0f}%",
                _DASH if w.get("mean_pct") is None else f"{w['mean_pct']:.2f}%"] for w in rp["windows"]])
            out += [_md(rp["note"]) + " " + _link(rp["source_url"], "Atlanta Fed Market Probability Tracker"), ""]
        elif sid == "heartbeats" and snap.get("heartbeats"):
            out += ["## Key markets", ""]
            table(["Market", "Level", "Change", "5-day", "Source"], [qrow(q) for q in snap["heartbeats"]])
            for q in snap["heartbeats"]:
                if q.get("desk_read"):
                    out.append(f"- **{_md(q['label'])}** · {note(q['desk_read'])}")
            out.append("")
        elif sid == "tone" and snap.get("tone"):
            tone = snap["tone"]
            out += [f"## Risk tone: {_md(tone['label'])} ({tone['score']:+d} of ±{tone['max']})", ""]
            out += [f"- {_md(c['name'])}: {c['score']:+d} · {_md(c['detail'])}" for c in tone["components"]]
            out += ["", _md(tone["note"]), ""]
        elif sid == "reads" and snap.get("reads"):
            out += ["## What the tape is saying", ""]
            out += [f"- {_md(r['text'])}" for r in snap["reads"]]
            out += ["", "Rule-based reads assembled from the quotes above. Tendencies are not forecasts.", ""]
        elif sid == "signals" and snap.get("signals"):
            out += ["## What this usually means", ""]
            for g in snap["signals"]:
                cites = ", ".join(_link(c["url"], c["title"]) for c in g.get("citations", [])[:2])
                out.append(f"- **{_md(g['title'])}**: {_md(g['text'])}" + (f" Sources: {cites}" if cites else ""))
            out += ["", "Sourced historical tendencies, not forecasts. Each fires only when its condition is met by data above.", ""]
        elif sid == "movers" and snap.get("movers"):
            out += ["## Abnormal moves (≥2σ of recent daily changes)", ""]
            out += [f"- {_md(m['label'])} {_md(m['change'])} at {_md(m['level'])} ({m['z']:+.1f}σ)" for m in snap["movers"]]
            out.append("")
        elif sid == "calendar":
            cal = snap.get("calendar") or {}
            econ = cal.get("economic")
            out += ["## Catalyst calendar", ""]
            if econ:
                for key, heading in (("items", ""), ("recent_results", "### Recent results")):
                    entries = econ.get(key)
                    if not entries:
                        continue
                    if heading:
                        out += [heading, ""]
                    rows = []
                    for e in entries:
                        actual_url = safe_http_url(e.get("actual_source_url"))
                        actual = e.get("actual") if actual_url else None
                        metric = e.get("actual_metric")
                        label = f"{actual} ({metric})" if actual and metric else actual
                        rows.append([_md(_release_time(e, tz, "%a %b %d %H:%M")),
                                     _md(e["country"]), _link(e.get("source_url") or econ.get("source_url"), e["title"]),
                                     _md(e["impact"]), _link(actual_url, label) if label else _DASH,
                                     _link(e.get("estimate_source_url"), e["forecast"]) if e.get("forecast") else _DASH,
                                     _link(e.get("estimate_source_url"), e["previous"]) if e.get("previous") else _DASH])
                    table(["Time", "Region", "Event", "Impact", "Actual", "Forecast", "Previous"], rows)
                if econ.get("note"):
                    out += [_md(econ["note"]), ""]
                elif not econ.get("items"):
                    out += ["No scheduled releases at this impact level for the selected regions.", ""]
                if econ.get("results_state") in {"unavailable", "partial"}:
                    out += ["Published actuals may be incomplete.", ""]
            for day in (cal.get("earnings") or {}).get("days", []):
                names = [f"{r['symbol']} ({r['when'].lower()})" for r in day["rows"][:12]]
                if names:
                    out += [f"**Earnings {day['date']}:** " + _md(", ".join(names)), ""]
            auctions = (cal.get("auctions") or {})
            if auctions.get("items"):
                out += ["**Treasury auctions:** " + _md("; ".join(
                    f"{a['auction_date']} {a['term']} {a['type']}" for a in auctions["items"][:8])), ""]
            elif auctions.get("note"):
                out += [_md(auctions["note"]), ""]
            for source in (econ, cal.get("earnings"), auctions):
                if source and source.get("source_url"):
                    out.append(_link(source["source_url"], source.get("provider", "source")) + " ")
            out.append("")
        elif sid == "curve" and snap.get("curve"):
            curve = snap["curve"]
            out += [f"## Treasury curve · {curve['as_of']}", ""]
            table(["Tenor", "Yield", "Change"], [[p["tenor"], f"{p['yield']:.2f}%",
                  _DASH if p["chg_bp"] is None else f"{p['chg_bp']:+.0f}bp"] for p in curve["points"]])
            out += ["Spreads: " + "; ".join(f"{s['name']} {s['bp']:+.0f}bp" for s in curve["spreads"]) + ". "
                    + _link(curve.get("source_url"), "U.S. Treasury"), ""]
        elif sid == "boards":
            for board in snap.get("boards", []):
                out += [f"### {_md(board['group']['label'])}", ""]
                table(["Market", "Level", "Change", "5-day", "Source"], [qrow(q) for q in board["quotes"]])
        elif sid == "watchlist" and snap.get("watchlist"):
            out += ["## Watchlist", ""]
            rows = []
            for q in snap["watchlist"]:
                if q.get("state") == "unavailable":
                    rows.append([_md(q["label"]), "unavailable", _DASH, _DASH, _DASH])
                    continue
                ext = q.get("extended")
                rows.append([_md(q["label"]), _md(fmt_level(q)), _md(fmt_change(q)),
                             _md(f"{ext['label']} {ext['pct']:+.2f}%") if ext else _DASH, qrow(q)[-1]])
            table(["Ticker", "Last", "Change", "Extended hours", "Source"], rows)
        elif sid == "capital" and snap.get("capital"):
            out += ["## Capital markets", ""]
            capital = snap["capital"]
            spreads, ipos = capital.get("spreads"), capital.get("ipos")
            if spreads:
                table(["Credit benchmark", "OAS", "Change", "As of"], [
                    [_link(r["source_url"], r["label"]), f"{r['level_bp']:.0f} bp",
                     f"{r['change_bp']:+.0f} bp", r["as_of"] + (" (stale)" if r["stale"] else "")]
                    for r in spreads["rows"]
                ])
            if ipos and ipos["rows"]:
                out += [_link(ipos["source_url"], "Nasdaq IPO calendar") + " (upcoming dates are estimates)", ""]
                table(["Company", "Status", "Date", "Offer amount"], [
                    [_md(r["name"]), r["status"], r["date"], _md(r["amount"] or _DASH)] for r in ipos["rows"]
                ])
            groups = (snap.get("headlines") or {}).get("by_theme", {})
            for key in ("deals", "ecm", "credit_banks"):
                if key in groups:
                    group = groups[key]
                    out += [f"### {_md(group['label'])}", "", *[
                        f"- {_link(row['url'], row['title'])} ({_md(row['publisher'])})" for row in group["items"][:2]
                    ], ""]
        elif sid == "headlines" and snap.get("headlines"):
            out += ["## Headlines", ""]
            for e in snap["headlines"]["top"]:
                label = f"- {_link(e['url'], e['title'])} · {_md(e['publisher'])} · {_md(lt(e['published_at'], tz))}"
                out.append(label + (" · " + note(e["desk_read"]) if e.get("desk_read") else ""))
            out.append("")
        elif sid == "dealflow" and snap.get("dealflow"):
            out += ["## Deal flow (SEC filings)", ""]
            for key in ("mna", "ipo_filings", "pricings", "tender", "activist", "insiders", "proxies"):
                g = (snap["dealflow"].get("groups") or {}).get(key) or {}
                if g.get("items"):
                    out.append(f"**{_md(g['label'])}** ({g.get('count', len(g['items']))}): " + "; ".join(
                        _link(r["url"], r["company"]) + (f" {_md(r['side'])} {_md(money(r['value']))}" if r.get("side") else "")
                        + (" (cluster buy)" if r.get("cluster") else "") for r in g["items"][:5]))
            out.append("")
        elif sid == "stockmovers" and snap.get("stock_movers"):
            sm = snap["stock_movers"]
            out += ["## Stock movers ($2B+)", ""]
            for key, label in (("gainers", "Gainers"), ("losers", "Decliners"), ("active", "Most active")):
                if sm.get(key):
                    out.append(f"**{label}:** " + ", ".join(
                        f"{_md(r['symbol'])} {r['pct']:+.1f}%" + (f" ({r['rel_volume']:.1f}x vol)" if r.get("rel_volume") else "")
                        for r in sm[key][:6]))
            out.append("")
        elif sid == "positioning" and (snap.get("positioning") or snap.get("global_yields") or snap.get("oil_curve")):
            out += ["## Positioning & global curves", ""]
            for r in (snap.get("global_yields") or {}).get("rows") or []:
                out.append(f"- {_md(r['label'])}: {r['yield']:.2f}% ({r['chg_bp']:+.0f}bp, {_md(r['as_of'])})")
            oc = snap.get("oil_curve") or {}
            if oc.get("state") == "ok":
                out.append(f"- WTI curve {_md(oc['shape'])}: front minus 12th month {oc['m1_m12']:+.2f}")
            rows = (snap.get("positioning") or {}).get("rows") or []
            if rows:
                table(["Market", "Net contracts", "Week change", "3-year percentile"], [
                    [_md(r["label"]), f"{r['net']:+,}", f"{r['change']:+,}",
                     (ordinal(r["pctile_3y"]) if r.get("pctile_3y") is not None else _DASH) + (f" ({_md(r['extreme'])})" if r.get("extreme") else "")]
                    for r in rows])
            out.append("")
        elif sid == "sentiment" and (snap.get("breadth") or snap.get("sentiment")):
            out += ["## Breadth & sentiment", ""]
            b, fg = snap.get("breadth") or {}, snap.get("sentiment") or {}
            if b.get("pct_above_200") is not None:
                out.append(f"- {b['pct_above_200']:.1f}% of S&P 500 stocks above their 200-day average, {b['pct_above_50']:.1f}% "
                           f"above their 50-day; {b['advancers']} advancers / {b['decliners']} decliners; "
                           f"{b['new_highs']} new highs / {b['new_lows']} new lows" + ("; breadth thrust" if b.get("thrust") else ""))
            if fg.get("score") is not None:
                out.append(f"- CNN Fear & Greed {fg['score']:.0f} ({_md(fg['rating'])}), week ago {fg.get('week_ago')}")
            out.append("")
        elif sid == "economy" and snap.get("economy"):
            ec = snap["economy"]
            out += ["## US economy & liquidity", ""]
            if ec.get("claims"):
                out.append(f"- Initial jobless claims {ec['claims']['latest']:,} (4-week avg {ec['claims']['avg4']:,}, "
                           f"{ec['claims']['vs_low_pct']:+.1f}% vs 12-month low)")
            if ec.get("unemployment") and ec.get("sahm"):
                out.append(f"- Unemployment {ec['unemployment']['latest']:.1f}%; Sahm indicator {ec['sahm']['latest']:.2f} (trigger 0.50)")
            if ec.get("term_premium"):
                out.append(f"- 10Y term premium (Kim-Wright) {ec['term_premium']['latest']:.2f}% "
                           f"({ec['term_premium']['chg20_bp']:+.0f}bp over 20 sessions)")
            if ec.get("reserves_bn") and ec.get("rrp_bn"):
                out.append(f"- Reserves ${ec['reserves_bn']['latest']:,}bn; overnight reverse repo ${ec['rrp_bn']['latest']:,.1f}bn")
            if ec.get("ebp"):
                out.append(f"- Excess bond premium {ec['ebp']['ebp_bp']:+d}bp; Fed model recession odds {ec['ebp']['recession_prob_pct']:.1f}%")
            out += ["", _link(ec.get("source_url"), "Source: FRED, St. Louis Fed"), ""]


# ───────────────────────────── Slack ─────────────────────────────

def slack_blocks(snap: dict) -> tuple:
    """(blocks, fallback text). Plain, bounded, and every link goes through safe_http_url."""
    def escaped(text):
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def link(url, label):
        url = safe_http_url(url)
        label = escaped(label.replace("|", "/"))
        if not url:
            return label
        return "<" + url.replace("&", "&amp;").replace("<", "%3C").replace(">", "%3E").replace("|", "%7C") + "|" + label + ">"

    blocks, lines = [], []
    summary = snap.get("summary") or []
    tz = snap.get("timezone", "UTC")

    def section(text):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})

    ids = set(_split(snap, "all"))
    if "summary" in ids and summary:
        section("*Top of the morning*" + "".join(f"\n{n}. {b['text'][:300]}" for n, b in enumerate(summary, 1)))
        lines += [b["text"] for b in summary]
    desk = snap.get("desk_read") or {}
    if desk.get("items"):
        rows = []
        for item in desk["items"][:4]:
            title = link(item["source"]["url"], item["title"][:140])
            stamp = " · " + _release_time(item, tz, "%a %b %d %H:%M") if item.get("time") else ""
            rows.append(f"• {title}{stamp} · {escaped(item['impact'])}")
            lines.append(f"{escaped(item['title'])}{stamp} · {escaped(item['impact'])}")
        section("*Before the open*\n" + "\n".join(rows))
    if "heartbeats" in ids and snap.get("heartbeats"):
        rows = []
        for q in snap["heartbeats"]:
            label = link(q.get("source_url"), q["label"])
            age = " (stale)" if q.get("state") == "stale" else ""
            rows.append(f"{label}: unavailable" if q.get("state") == "unavailable"
                        else f"{label}  {fmt_level(q)}  {fmt_change(q)}{age}")
        lines += ["Key markets", *rows]
        section("*Key markets*\n" + "\n".join(rows))
    tone = snap.get("tone") if "tone" in ids else None
    if tone:
        text = f"Risk tone: {tone['label']} ({tone['score']:+d}/±{tone['max']})"
        lines.append(text)
        section("*" + text + "*")
    reads = snap.get("reads") if "reads" in ids else []
    if reads:
        section("*What the tape is saying*\n" + "\n".join("• " + r["text"] for r in reads[:4]))
        lines += [r["text"] for r in reads[:4]]
    sigs = snap.get("signals") if "signals" in ids else []
    if sigs:
        section("*What this usually means*\n" + "\n".join("• " + g["text"][:400] for g in sigs[:4]))
        lines += [g["text"] for g in sigs[:4]]
    movers = snap.get("movers") if "movers" in ids else []
    if movers:
        section("*Abnormal moves*\n" + "\n".join(f"• {m['label']} {m['change']} ({m['z']:+.1f}σ)" for m in movers[:5]))
    econ = ((snap.get("calendar") or {}).get("economic") or {}) if "calendar" in ids else {}
    def release_line(e):
        return (f"• {_release_time(e, tz, '%a %b %d %H:%M')} {escaped(e['country'])} "
                f"{link(e.get('source_url') or econ.get('source_url'), e['title'])}")

    scheduled = econ.get("items", [])
    items = [e for e in scheduled if not e.get("actual") and e["impact"] in {"High", "Medium"}][:6]
    if items:
        section("*Scheduled data*\n" + "\n".join(map(release_line, items)))
    published = [e for e in [*reversed(scheduled), *econ.get("recent_results", [])]
                 if e.get("actual") and safe_http_url(e.get("actual_source_url"))][:4]
    if published:
        results = []
        for e in published:
            metric = f" {e['actual_metric']}" if e.get("actual_metric") else ""
            actual = link(e["actual_source_url"], e["actual"])
            comparison = f" vs {link(e['actual_source_url'], e['forecast'])} forecast" if e.get("forecast") else ""
            results.append(f"{release_line(e)} · Actual{escaped(metric)} {actual}{comparison}")
        section("*Recent data results*\n" + "\n".join(results))
        lines += ["Recent data results", *results]
    spreads = (snap.get("capital") or {}).get("spreads") if "capital" in ids else None
    if spreads:
        section("*US credit spreads*\n" + "\n".join(
            f"{link(row['source_url'], row['label'])}  {row['level_bp']:.0f} bp ({row['change_bp']:+.0f} bp)  {row['as_of']}"
            + (" (stale)" if row.get("stale") else "") for row in spreads["rows"]))
    mnote = snap.get("morning_note") or {}
    if mnote.get("state") == "ok":
        section("*Morning note* (AI-written, every figure checked)\n" + " ".join(x["text"] for x in mnote["sentences"])[:2800])
    if snap.get("alerts"):
        section("*Alerts*\n" + "\n".join("• " + escaped(a["text"][:200]) for a in snap["alerts"][:6]))
    deals = (snap.get("dealflow") or {}).get("groups") or {} if "dealflow" in ids else {}
    deal_lines = [f"• {escaped(g['label'])}: " + ", ".join(link(r["url"], r["company"][:60]) for r in g["items"][:3])
                  for key in ("mna", "tender", "activist") for g in [deals.get(key) or {}] if g.get("items")]
    clusters = [r for r in (deals.get("insiders") or {}).get("items") or [] if r.get("cluster")]
    deal_lines += [f"• Insider cluster buy: {link(r['url'], r['company'][:60])} ({escaped(r['insider'])})" for r in clusters[:2]]
    if deal_lines:
        section("*Deal flow*\n" + "\n".join(deal_lines))
    ec = snap.get("economy") or {} if "economy" in ids else {}
    if ec.get("claims") and ec.get("sahm"):
        section(f"*Economy* claims {ec['claims']['latest']:,} · Sahm {ec['sahm']['latest']:.2f}"
                + (f" · EBP {ec['ebp']['ebp_bp']:+d}bp" if ec.get("ebp") else ""))
    fg, br = (snap.get("sentiment") or {}), (snap.get("breadth") or {})
    if "sentiment" in ids and (fg.get("score") is not None or br.get("pct_above_200") is not None):
        section("*Breadth & sentiment* " + " · ".join(x for x in (
            f"{br['pct_above_200']:.0f}% above 200-day" if br.get("pct_above_200") is not None else "",
            f"Fear & Greed {fg['score']:.0f} ({escaped(fg['rating'])})" if fg.get("score") is not None else "") if x))
    heads = (snap.get("headlines") or {}).get("top", []) if "headlines" in ids else []
    if heads:
        section("*Headlines*\n" + "\n".join(f"• {link(h['url'], h['title'][:150])} ({escaped(h['publisher'][:30])})" for h in heads[:5]))
        lines += [f"{escaped(h['title'])} ({escaped(h['publisher'])})" for h in heads[:5]]
    issues = [f"{name} ({status['state']})" for name, status in snap.get("sources", {}).items()
              if status["state"] not in ("ok", "disabled")]
    if issues:
        line = "Market source issues: " + ", ".join(issues)
        section(line)
        lines.append(line)
    return blocks[:24], "\n".join(lines)
