"""Grounding guardrail: enforce that every figure in a summary traces to its source.

Two checks:
  1. Strict: every self-declared ``figures_cited`` entry must appear verbatim
     (after normalization) in the source text.
  2. Lenient: every number independently extracted from the summary/why-it-matters
     must match (within a relative epsilon) some number present in the source text.
     This catches figures the model used without declaring them, while tolerating
     harmless formatting differences (trailing zeros, commas, %, sign).
"""
from __future__ import annotations

import re

_MULTIPLIERS = {
    "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
    "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
}

# Matches amounts, percentages, and bare numbers (including $-prefixed and K/M/B words).
_NUM_TOKEN_RE = re.compile(
    r"-?\$?\s?\d[\d,]*(?:\.\d+)?\s?(?:million|billion|trillion|thousand|[kmbt])?\s?%?",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    s = (text or "").lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace(",", "").replace("$", "")
    return s


def _token_value(token: str):
    t = token.strip().lower().replace("$", "").replace(",", "")
    is_pct = "%" in t
    t = t.replace("%", "").strip()
    mult = 1.0
    for word, val in _MULTIPLIERS.items():
        if t.endswith(word):
            mult = val
            t = t[: -len(word)].strip()
            break
    try:
        v = float(t)
    except ValueError:
        return None
    return v * mult


def extract_figures(text: str) -> list:
    """Return the raw figure tokens (currency/percent) present in text."""
    out = []
    for m in _NUM_TOKEN_RE.findall(text or ""):
        token = m.strip()
        if token and ("$" in token or "%" in token):
            out.append(token)
    return out


def _extract_figure_values(text: str) -> list:
    """Numeric values of currency/percentage figures only (not bare numbers)."""
    values = []
    for m in _NUM_TOKEN_RE.findall(text or ""):
        token = m.strip()
        if "$" not in token and "%" not in token:
            continue
        v = _token_value(token)
        if v is not None:
            values.append(v)
    return values


def _matches(value: float, corpus_values: list) -> bool:
    av = abs(value)
    for cv in corpus_values:
        acv = abs(cv)
        if abs(av - acv) <= max(1e-6, acv * 1e-3):
            return True
    return False


def verify_grounding(summary: str, why_it_matters: str, figures_cited: list, source_text: str):
    """Return (grounded: bool, ungrounded_figures: list)."""
    corpus_norm = _normalize(source_text)
    corpus_figures = _extract_figure_values(source_text)
    ungrounded: list[str] = []

    # 1. Strict verbatim check on self-declared figures.
    for f in figures_cited:
        if _normalize(str(f)) not in corpus_norm:
            ungrounded.append(str(f))

    # 2. Independent backstop on currency/percentage figures the model may not
    #    have declared (magnitude match tolerates trailing-zero/format differences).
    for token in extract_figures(f"{summary} {why_it_matters}"):
        v = _token_value(token)
        if v is not None and not _matches(v, corpus_figures):
            ungrounded.append(token)

    return (not ungrounded, sorted(set(ungrounded)))
