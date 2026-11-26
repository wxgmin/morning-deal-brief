"""Market-implied Fed path from the Atlanta Fed Market Probability Tracker (free stand-in for CME FedWatch).

The tracker estimates probability distributions for the average policy rate over 3-month windows from CME
3-month SOFR options. Published daily (T-1) as a ~7 MB xlsx, so it is cached and refreshed at most every
12 hours. Figures are shown exactly as published; the app only converts basis points to percent.
"""
from __future__ import annotations

import io
import json
import re
import time
from datetime import date, datetime
from pathlib import Path

from ..util import get_bytes

URL = ("https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/"
       "market-probability-tracker/mpt_histdata.xlsx")
PAGE = "https://www.atlantafed.org/research-and-data/data/market-probability-tracker"
_MAX_AGE = 12 * 3600
_WINDOWS = 6


def _bp(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_rows(rows) -> dict:
    """rows: iterable of (date, reference_start, target_range, field, value), header excluded."""
    keep = {}  # only the two newest observation dates are retained (the file holds years of history)
    for obs, start, target, field, value in rows:
        if not obs or not field:
            continue
        obs = str(obs)[:10]
        if obs not in keep:
            if len(keep) == 2 and obs < min(keep):
                continue
            keep[obs] = {}
            if len(keep) > 2:
                del keep[min(keep)]
        start = start.date().isoformat() if isinstance(start, datetime) else str(start)[:10]
        window = keep[obs].setdefault(start, {"target": target, "buckets": []})
        val = _bp(value)
        if field.startswith("Rate: "):
            window[field[6:].replace(" percentile", "").replace("th", "")] = val
        elif field == "Prob: cut":
            window["p_cut"] = val
        elif field == "Prob: hike":
            window["p_hike"] = val
        elif field.startswith("Prob: "):
            nums = re.findall(r"\d+", field)
            if len(nums) == 2:
                window["buckets"].append({"low": int(nums[0]) / 100, "high": int(nums[1]) / 100, "p": val})
    if not keep:
        raise ValueError("tracker has no observations")
    latest = max(keep)
    prior_date = min(keep) if len(keep) == 2 else None
    by_date = keep
    target = None
    out = []
    for start, w in sorted(by_date[latest].items())[:_WINDOWS]:
        target = target or w["target"]
        prev = (by_date.get(prior_date) or {}).get(start, {})
        mode = max(w["buckets"], key=lambda b: b["p"] or 0) if w["buckets"] else None
        mean = w.get("mean")
        out.append({
            "window_start": start, "mean_pct": None if mean is None else round(mean / 100, 3),
            "mean_chg_bp": None if mean is None or prev.get("mean") is None else round(mean - prev["mean"], 1),
            "p25_pct": None if w.get("25") is None else round(w["25"] / 100, 3),
            "p75_pct": None if w.get("75") is None else round(w["75"] / 100, 3),
            "p_cut": w.get("p_cut"), "p_hike": w.get("p_hike"),
            "p_hold": None if w.get("p_cut") is None or w.get("p_hike") is None else round(100 - w["p_cut"] - w["p_hike"], 2),
            "most_likely": None if not mode else {"low": mode["low"], "high": mode["high"], "p": mode["p"]},
        })
    nums = re.findall(r"\d+", target or "")
    return {"state": "ok", "as_of": latest, "prior": prior_date, "windows": out,
            "target": {"low": int(nums[0]) / 100, "high": int(nums[1]) / 100} if len(nums) == 2 else None,
            "provider": "Federal Reserve Bank of Atlanta, Market Probability Tracker (from CME 3-month SOFR options)",
            "source_url": PAGE,
            "note": "Each row is the market-implied average policy rate over the 3-month window starting on that date, "
                    "not a single FOMC decision."}


def _read_xlsx(blob: bytes) -> dict:
    import openpyxl  # imported lazily: only this source needs it

    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    ws = wb["DATA"]
    ws.reset_dimensions()  # the file's stored dimensions are wrong; without this only the header is read
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    if tuple(header[:5]) != ("date", "reference_start", "target_range", "field", "value"):
        raise ValueError("unexpected tracker layout")
    return parse_rows(r[:5] for r in rows)


def fetch_rate_path(cache_dir: Path | None = None, today: date | None = None) -> dict:
    cache = Path(cache_dir) / "rate_path.json" if cache_dir else None
    if cache and cache.is_file() and time.time() - cache.stat().st_mtime < _MAX_AGE:
        return json.loads(cache.read_text(encoding="utf-8"))
    result = _read_xlsx(get_bytes(URL, {"User-Agent": "Mozilla/5.0 (MorningBrief)"}, limit=20_000_000, attempts=2))
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
