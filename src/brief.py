"""Portable Markdown and HTML versions of the source-attributed edition."""
from __future__ import annotations

from dataclasses import asdict

from jinja2 import Environment

from . import snapshot_render
from .guardrails import safe_http_url


def number(value, spec):
    return format(value, spec) if isinstance(value, (int, float)) else "—"


def change(value):
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "—"


def event_label(event_type):
    return (event_type or "other").replace("_", " ").title()


def _md(value):
    from html import escape
    value = escape(" ".join(str(value or "").split()), quote=False)
    for c in "\\*_[]()#!|~" + chr(96):
        value = value.replace(c, "\\" + c)
    return value


def _link(url, label):
    safe = safe_http_url(url)
    if not safe:
        return _md(label)
    safe = safe.replace("<", "%3C").replace(">", "%3E")
    return f"[{_md(label)}](<{safe}>)"


def recovery_note(meta):
    recovery = meta.get("recovery")
    if not recovery:
        return ""
    text = f"Expanded reporting window after the {recovery['last_finalized_date']} edition."
    if recovery["omitted_older_windows"]:
        text += " Older missed windows are outside the five-weekday recovery limit."
    if meta.get("sources", {}).get("news", {}).get("state") not in {None, "disabled"}:
        text += " News coverage depends on what the feeds still retain."
    return text


def coverage_note(meta):
    outcome = meta.get("outcome", "unverified")
    preview = "Pre-cutoff preview. The reporting window is still open. " if meta.get("is_preview") else ""
    problems = [f"{name} {data['state']}" for name, data in meta.get("sources", {}).items()
                if data["state"] in {"unavailable", "partial"}]
    if problems:
        status = "Source coverage is " + outcome + " (" + ", ".join(problems) + ")."
    elif outcome != "complete":
        status = "Source coverage is " + outcome + "."
    else:
        status = "Historical replay. News feeds were excluded." if meta.get("mode") == "backfill" else ""
    return " ".join(note for note in (preview.strip(), recovery_note(meta), status) if note)


def deals_enabled(meta) -> bool:
    """Without a snapshot there is no section config, so the original deal-flow layout applies."""
    snap = (meta or {}).get("snapshot")
    return not snap or any(sec["id"] == "deals" and sec["enabled"] for sec in snap.get("sections", []))


def render_markdown(brief_items, price_moves, coverage, date_str: str, meta=None) -> str:
    meta = meta or {}
    snap = meta.get("snapshot")
    lines = [f"# Morning Deal Brief · {date_str}", "", _md(meta.get("sector", coverage.sector)), ""]
    note = coverage_note(meta)
    if note:
        lines += [_md(note), ""]
    if snap:
        lines += snapshot_render.markdown(snap, "before")
    if deals_enabled(meta):
        lines += _deal_markdown(brief_items, price_moves, meta)
    if snap:
        lines += snapshot_render.markdown(snap, "after")
    return "\n".join(lines).rstrip() + ("\n" if snap else "")


def _deal_markdown(brief_items, price_moves, meta) -> list:
    lines = ["## Coverage events", ""]
    if not brief_items:
        lines += ["No events published for this edition.", ""]
    for item in brief_items:
        lines += [
            f"### {item.rank}. {_md(item.company)} · {_md(event_label(item.event_type))}",
            f"> {_md(item.summary)}", "",
            f"**Analyst follow-up** · {_md(item.why_it_matters)}",
            _link(item.source_url, item.publisher or "Open source"), "",
        ]
    session = meta.get("sources", {}).get("market", {}).get("session") or (price_moves[0].date if price_moves else "")
    lines += [f"## Coverage prices · {session}" if session else "## Coverage prices", ""]
    if not price_moves:
        lines.append("No market rows available.")
        return lines
    lines += ["| Ticker | Close | Change | Volume | Volume z | Return z | Source |",
              "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for m in price_moves:
        e = m.evidence
        close = f"{number(m.close, ',.2f')} {e.get('currency', '')}".strip()
        row = [_md(m.ticker), _md(close), change(m.pct_change), number(m.volume, ",.0f"),
               number(m.volume_z, "+.1f"), number(m.z_score, "+.1f"),
               _link(e.get("source_url"), e.get("provider", "Unverified"))]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "Change uses the provider’s adjusted close. Z-scores use the prior 60 trading sessions."]
    return lines


_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Morning Deal Brief · {{ date }}</title>
<style>
body{font:15px/1.55 Georgia,serif;color:#182321;background:#f7f6ef;margin:0}
main{max-width:860px;margin:auto;padding:28px 20px}
header{border-top:5px solid #164d3c;border-bottom:1px solid #cfd5cc;padding:8px 0}
h1{font-size:32px;margin:10px 0 4px}h2{font-size:22px;margin:24px 0 10px}
h3{font-size:17px;margin:12px 0 5px}p{margin:7px 0}a{color:#135541}
article{border-top:1px solid #d6dbd1;padding:5px 0 10px}
small,th{font:12px/1.45 system-ui,sans-serif;color:#41554d}
blockquote{margin:8px 0 8px 12px;padding-left:12px;border-left:2px solid #bfcfc2}
table{width:100%;border-collapse:collapse;font:13px/1.35 system-ui,sans-serif}
th,td{padding:7px;border-bottom:1px solid #d6dbd1;text-align:right}
th:first-child,td:first-child,th:last-child,td:last-child{text-align:left}
.market{overflow:auto}.note{background:#f0e6ce;padding:8px 12px;margin-top:16px}
@media print{body{background:white}main{padding:0}article{break-inside:avoid}}
:root{--ink:#182321;--ink-2:#33423d;--ink-3:#5b6b64;--rule:#d6dbd1;--card:#fff;--paper-2:#ebeee6;--up:#1d6b45;--down:#a3261d;--accent:#135541;--warn:#8a5200;--warn-wash:#f0e6ce;--bad:#a3261d}
{{ shared_css|safe }}
.snap{font-family:system-ui,Arial,sans-serif}.snap-sec h2{font-family:Georgia,serif}
</style></head>
<body><main>
<header><small>{{ date }} · {{ sector }}</small><h1>Morning Deal Brief</h1></header>
{% if note %}<p class="note">{{ note }}</p>{% endif %}
{{ snap_before }}
{% if show_deals %}<h2>Coverage events</h2>
{% for item in items %}
<article>
<h3>{{ item.rank }}. {{ item.company }} · {{ event_label(item.event_type) }}</h3>
<blockquote>{{ item.summary }}</blockquote>
<p><strong>Analyst follow-up</strong> · {{ item.why_it_matters }}</p>
{% if item.source_url|safeurl %}<small><a href="{{ item.source_url|safeurl }}">{{ item.publisher or "Open source" }}</a></small>{% endif %}
</article>
{% else %}<p>No events published for this edition.</p>{% endfor %}
<h2>Coverage prices{% if session %} · {{ session }}{% endif %}</h2>
{% if moves %}
<div class="market"><table>
<thead><tr><th scope="col">Ticker</th><th scope="col">Close</th><th scope="col">Change</th><th scope="col">Volume</th><th scope="col">Volume z</th><th scope="col">Return z</th><th scope="col">Source</th></tr></thead>
<tbody>
{% for m in moves %}<tr>
<td>{{ m.ticker }}</td><td>{{ number(m.close, ",.2f") }} {{ m.evidence.get("currency", "") }}</td>
<td>{{ change(m.pct_change) }}</td><td>{{ number(m.volume, ",.0f") }}</td><td>{{ number(m.volume_z, "+.1f") }}</td>
<td>{{ number(m.z_score, "+.1f") }}</td>
<td>{% if m.evidence.get("source_url")|safeurl %}<a href="{{ m.evidence.source_url|safeurl }}">{{ m.evidence.provider }}</a>{% else %}Unverified{% endif %}</td>
</tr>{% endfor %}
</tbody></table></div>
<p><small>Change uses the provider’s adjusted close. Z-scores use the prior 60 trading sessions.</small></p>
{% else %}<p>No market rows available.</p>{% endif %}
{% endif %}
{{ snap_after }}
</main></body></html>"""

_ENV = Environment(autoescape=True)
_ENV.filters["safeurl"] = safe_http_url
_TEMPLATE = _ENV.from_string(_HTML)


def render_html(brief_items, price_moves, coverage, date_str: str, meta=None) -> str:
    meta = meta or {}
    session = meta.get("sources", {}).get("market", {}).get("session") or (price_moves[0].date if price_moves else "")
    snap = meta.get("snapshot")
    return _TEMPLATE.render(
        snap_before=snapshot_render.html_block(snap, "before") if snap else "",
        snap_after=snapshot_render.html_block(snap, "after") if snap else "",
        show_deals=deals_enabled(meta), shared_css=snapshot_render.shared_css() if snap else "",
        date=date_str, sector=meta.get("sector", coverage.sector), note=coverage_note(meta), session=session,
        items=[asdict(item) for item in brief_items], moves=[asdict(move) for move in price_moves],
        number=number, change=change, event_label=event_label,
    )
