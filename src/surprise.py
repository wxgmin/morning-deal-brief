"""Economic data surprises: actual vs forecast, with the direction read for that kind of indicator.

No free source publishes the history of forecast errors, so a true standardised surprise (Andersen et al.)
is not possible. Instead a miss is flagged "notable" when it is at least as large as the change the forecast
itself expected from the previous reading (APP_HEURISTIC, labelled as such on the page).
"""
from __future__ import annotations

import re

_READING = re.compile(r"^\s*([+\-−]?\d+(?:\.\d+)?)\s*(%|[KMBT])?\s*$", re.IGNORECASE)
# Higher reading = weaker economy for these (so "above forecast" is a *weaker* print).
_INVERSE = re.compile(r"unemployment|jobless|claims|layoff|job cuts|continuing", re.IGNORECASE)
_INFLATION = re.compile(r"\b(cpi|pce|ppi|inflation|price index|prices|deflator|wage|earnings)\b", re.IGNORECASE)


def parse(value):
    m = _READING.match(str(value or "").replace(",", ""))
    if not m:
        return None
    return float(m.group(1).replace("−", "-")), (m.group(2) or "").upper()


def compute(item: dict) -> dict | None:
    actual, forecast = parse(item.get("actual")), parse(item.get("forecast"))
    if not actual or not forecast or actual[1] != forecast[1]:
        return None
    unit = actual[1]
    diff = round(actual[0] - forecast[0], 4)
    previous = parse(item.get("previous"))
    expected_move = abs(forecast[0] - previous[0]) if previous and previous[1] == unit else None
    if abs(diff) < 1e-9:
        direction, read = "inline", "in line with forecast"
    else:
        above = diff > 0
        direction = "above" if above else "below"
        title = item.get("actual_metric") or item.get("title") or ""
        if _INFLATION.search(title) and not _INVERSE.search(title):
            read = "hotter than forecast" if above else "cooler than forecast"
        elif _INVERSE.search(title):
            read = "weaker than forecast" if above else "stronger than forecast"
        else:
            read = "stronger than forecast" if above else "weaker than forecast"
    floor = 0.1 if unit == "%" else 1e-9
    notable = direction != "inline" and expected_move is not None and abs(diff) >= max(expected_move, floor)
    shown = f"{diff:+g}{'pp' if unit == '%' else unit}"
    return {"diff": diff, "unit": unit, "shown": shown, "direction": direction, "read": read, "notable": notable,
            "rule": "Notable = miss at least as large as the move the forecast expected from the prior reading (APP_HEURISTIC)."}


def annotate(items: list) -> list:
    """Attach `surprise` to items that have both an actual and a forecast; return those, notable first."""
    out = []
    for item in items or []:
        s = compute(item)
        if s:
            item["surprise"] = s
            out.append(item)
    out.sort(key=lambda i: (not i["surprise"]["notable"], i.get("time", "")), reverse=False)
    return out
