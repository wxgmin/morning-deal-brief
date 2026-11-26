"""Publication controls: source statements and fixed analyst follow-ups."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

MAX_EXCERPT = 2400

# These are editorial questions and actions. They make no claims about an issuer.
FOLLOW_UPS = {
    "M&A": ("Review deal terms and financing.", "Assess read-through for the coverage list."),
    "earnings": ("Review operating trends and cash flow.", "Compare reported results with prior guidance."),
    "guidance_change": ("Revisit the operating outlook and valuation assumptions.",),
    "financing": ("Review the impact on liquidity, leverage and capital allocation.",),
    "management_change": ("Review succession plans and coverage implications.",),
    "activist": ("Review the holder's stated intent and governance implications.",),
    "ownership": ("Review ownership changes and the holder's disclosed intent.",),
    "rating_change": ("Review potential implications for funding costs and debt capacity.",),
    "bankruptcy": ("Review the court filings, liquidity position and creditor exposure.",),
    "impairment": ("Review the charge, affected assets and implications for valuation.",),
    "financial_reporting": ("Confirm which periods are affected before relying on the reported figures.",),
    "large_price_move": ("Check for a disclosed catalyst and compare the move with peers.",),
    "other": ("Review the source for potential coverage implications.",),
}

_CURRENCY = r"(?:[$€£¥]|(?:USD|EUR|GBP|JPY|CAD|AUD)\s+)"
_NUMBER = r"(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
_UNIT = r"(?:\s?(?:%|x\b|bps?\b|basis points?\b|percent\b|(?:thousand|million|billion|trillion|bn|mn|mm|[kmbt])\b))"
_AMOUNT = rf"(?:[+\-−])?{_CURRENCY}?{_NUMBER}{_UNIT}?"
_FIGURE = re.compile(
    rf"(?<!\w)(?:\d{{4}}-\d{{2}}-\d{{2}}(?![\d-])|{_AMOUNT}(?:[ \t]*[–—-][ \t]*{_AMOUNT})?)",
    re.IGNORECASE,
)


def safe_http_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    if not value or any(ord(c) < 33 or ord(c) == 127 or c.isspace() for c in value) or "\\" in value:
        return ""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                and not parsed.password and (parsed.port is None or 0 < parsed.port <= 65535)):
            return value
    except ValueError:
        pass
    return ""


def extract_figures(text: str) -> list[str]:
    """Index figures for display. The full evidence statement is authoritative."""
    return list(dict.fromkeys(match.group() for match in _FIGURE.finditer(text or "")))


def verify_grounding(summary: str, why_it_matters: str, figures_cited: list, source_text: str):
    """Accept an exact source statement and an approved editorial follow-up."""
    failures = []
    statement = " ".join(summary.split())
    statements = [" ".join(s.split()) for s in source_text.splitlines() if s.strip()]
    if not statement or statement not in statements:
        failures.append(summary or "no source statement")
    if why_it_matters not in {line for options in FOLLOW_UPS.values() for line in options}:
        failures.append(why_it_matters or "no approved follow-up")
    permitted_figures = extract_figures(statement) if statement in statements else []
    for figure in figures_cited:
        if figure not in permitted_figures:
            failures.append(figure)
    return not failures, failures
