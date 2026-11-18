"""Grounding guardrail: enforce that every figure in a summary traces to its source.

Two checks:
  1. Strict: every self-declared ``figures_cited`` entry must appear (normalized)
     in the source text.
  2. Backstop: every currency/percentage figure independently extracted from the
     summary/why-it-matters must match a like-unit figure present in the source
     (unit-aware; sign-agnostic to tolerate "fell 3.2%" vs a source "-3.2%").
"""
from __future__ import annotations

import re

_MULTIPLIERS = {
    "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
    "thousands": 1e3, "millions": 1e6, "billions": 1e9, "trillions": 1e12,
    "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
    "mn": 1e6, "mm": 1e6, "bn": 1e9,
}
_MULT_ORDER = sorted(_MULTIPLIERS.items(), key=lambda kv: -len(kv[0]))

_MULT_WORD = re.compile(r"(?:million|billions?|trillions?|thousands?|bn|mn|mm|[kmbt])\b", re.IGNORECASE)

_NUM_TOKEN_RE = re.compile(
    r"-?\$?\s?(?:\d[\d,]*(?:\.\d+)?|\.\d+)\s?(?:million|billions?|trillions?|thousands?|bn|mn|mm|[kmbt])?\s?%?",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    s = (text or "").lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace(",", "").replace("$", "")
    s = re.sub(r"\s?%", "%", s)
    for full, short in (("billion", "b"), ("million", "m"), ("trillion", "t"), ("thousand", "k")):
        s = re.sub(rf"\b{full}s?\b", short, s)
    return s


def _token_value(token: str):
    """Return (magnitude, unit) where unit is 'currency' or 'percent'."""
    t = token.strip().lower().replace("$", "").replace(",", "").strip()
    is_pct = "%" in t
    t = t.replace("%", "").replace("-", "").strip()
    mult = 1.0
    for word, val in _MULT_ORDER:
        if t.endswith(word):
            mult = val
            t = t[: -len(word)].strip()
            break
    try:
        v = float(t)
    except ValueError:
        return None
    return (v * mult, "percent" if is_pct else "currency")


def extract_figures(text: str) -> list:
    """Return raw figure tokens: currency, percentages, and multiplier-word amounts."""
    out = []
    for m in _NUM_TOKEN_RE.findall(text or ""):
        token = m.strip()
        if token and ("$" in token or "%" in token or _MULT_WORD.search(token)):
            out.append(token)
    return out


def _extract_figure_values(text: str) -> list:
    values = []
    for token in extract_figures(text):
        v = _token_value(token)
        if v is not None:
            values.append(v)
    return values


def _matches(fig, corpus) -> bool:
    v, unit = fig
    av = abs(v)
    for cv, cunit in corpus:
        if unit != cunit:
            continue
        acv = abs(cv)
        if abs(av - acv) <= max(1e-9, acv * 1e-6):
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
    #    have declared (unit-aware magnitude match).
    for token in extract_figures(f"{summary} {why_it_matters}"):
        v = _token_value(token)
        if v is not None and not _matches(v, corpus_figures):
            ungrounded.append(token)

    return (not ungrounded, sorted(set(ungrounded)))
