"""Render the one-page brief as Markdown and HTML."""
from __future__ import annotations

from urllib.parse import urlsplit

from jinja2 import Environment


def _event_label(event_type: str) -> str:
    return event_type.replace("_", " ").title()


def _md_cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _md_inline(value) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _safe_http_url(url: str) -> str:
    url = (url or "").strip()
    try:
        return url if urlsplit(url).scheme.lower() in ("http", "https") else ""
    except Exception:
        return ""


def render_markdown(brief_items, price_moves, coverage, date_str: str) -> str:
    lines = [
        f"# Morning Deal Brief — {date_str}",
        "",
        f"**Sector:** {_md_inline(coverage.sector)}",
        "",
        "## What matters today",
        "",
    ]

    if not brief_items:
        lines.append("_No coverage-relevant events met the threshold this morning._")
        lines.append("")
    else:
        for b in brief_items:
            lines.append(f"### {b.rank}. {_md_inline(b.company)} ({_md_inline(b.ticker)}) — {_event_label(b.event_type)}")
            lines.append(_md_inline(b.summary))
            lines.append("")
            lines.append(f"> Why it matters for us: {_md_inline(b.why_it_matters)}")
            lines.append("")
            lines.append(f"Source: {_md_inline(b.source_url) or '(none)'}")
            lines.append("")

    lines += [
        "## Coverage price moves",
        "",
        "| Ticker | Company | Role | Close | 1D % | Vol z | σ |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for m in price_moves:
        flag = " *" if m.large_move else ""
        lines.append(
            f"| {_md_cell(m.ticker)} | {_md_cell(m.company)} | {_md_cell(m.role)} | {m.close:.2f} | "
            f"{m.pct_change:+.2f}% | {m.volume_z:+.1f} | {m.z_score:+.1f}{flag} |"
        )
    if any(m.large_move for m in price_moves):
        lines += ["", "\\* = large move (>2σ vs trailing 60-day distribution)"]

    return "\n".join(lines)


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Morning Deal Brief — {{ date_str }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         max-width: 760px; margin: 40px auto; padding: 0 24px; color: #1a1a1a; line-height: 1.5; }
  h1 { font-size: 1.5rem; }
  h2 { font-size: 1.1rem; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 28px; }
  .item { margin-bottom: 18px; }
  .item h3 { margin-bottom: 4px; font-size: 1rem; }
  .why { color: #333; }
  .src { font-size: 0.85rem; }
  a { color: #0b5cad; }
  table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
  th, td { border: 1px solid #e3e3e3; padding: 4px 8px; text-align: left; }
  td.num { text-align: right; }
  .flag { color: #b00020; font-weight: 600; }
  .muted { color: #666; }
</style>
</head>
<body>
<h1>Morning Deal Brief — {{ date_str }}</h1>
<p class="muted">Sector: {{ sector }}</p>
<h2>What matters today</h2>
{% if items %}
  {% for b in items %}
  {% set u = b.source_url|safeurl %}
  <div class="item">
    <h3>{{ b.rank }}. {{ b.company }} ({{ b.ticker }}) — {{ b.event_type.replace('_', ' ').title() }}</h3>
    <p>{{ b.summary }}</p>
    <p class="why"><strong>Why it matters for us:</strong> {{ b.why_it_matters }}</p>
    <p class="src">Source: {% if u %}<a href="{{ u }}">{{ b.source_url }}</a>{% else %}(none){% endif %}</p>
  </div>
  {% endfor %}
{% else %}
  <p class="muted">No coverage-relevant events met the threshold this morning.</p>
{% endif %}
<h2>Coverage price moves</h2>
<table>
  <thead><tr><th>Ticker</th><th>Company</th><th>Role</th><th class="num">Close</th><th class="num">1D %</th><th class="num">Vol z</th><th class="num">&sigma;</th></tr></thead>
  <tbody>
  {% for m in moves %}
    <tr>
      <td>{{ m.ticker }}</td><td>{{ m.company }}</td><td>{{ m.role }}</td>
      <td class="num">{{ "%.2f"|format(m.close) }}</td>
      <td class="num">{{ "%+.2f"|format(m.pct_change) }}%</td>
      <td class="num">{{ "%+.1f"|format(m.volume_z) }}</td>
      <td class="num">{{ "%+.1f"|format(m.z_score) }}{% if m.large_move %} <span class="flag">*</span>{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% if moves|selectattr("large_move")|list %}
<p class="muted">* = large move (&gt;2&sigma; vs trailing 60-day distribution)</p>
{% endif %}
</body>
</html>
"""


def render_html(brief_items, price_moves, coverage, date_str: str) -> str:
    env = Environment(autoescape=True)
    env.filters["safeurl"] = _safe_http_url
    template = env.from_string(_HTML_TEMPLATE)
    return template.render(
        date_str=date_str,
        sector=coverage.sector,
        items=brief_items,
        moves=price_moves,
    )
