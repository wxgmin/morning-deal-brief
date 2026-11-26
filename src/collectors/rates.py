"""Funding rates (NY Fed) and real yields / breakevens (U.S. Treasury). Both official and keyless.

* NY Fed Markets API: EFFR, SOFR, OBFR, TGCR with the FOMC target range.
* Treasury par real yield curve (TIPS) + the nominal par curve -> 5Y/10Y real yields and breakevens,
  with 1-day and 20-session changes computed from the same official files.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from urllib.parse import urlencode

from ..util import get_bytes

_NYFED = "https://markets.newyorkfed.org/api/rates/all/latest.json"
_TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
             "daily-treasury-rates.csv/{year}/all")
_HEADERS = {"User-Agent": "Mozilla/5.0 (MorningBrief)", "Accept": "application/json"}
_WINDOW = 20


def parse_funding(payload: dict) -> dict:
    rows = {}
    for row in payload.get("refRates") or []:
        kind = row.get("type")
        if kind in {"EFFR", "SOFR", "OBFR", "TGCR"} and isinstance(row.get("percentRate"), (int, float)):
            rows[kind] = {"rate": float(row["percentRate"]), "date": row.get("effectiveDate"),
                          "p1": row.get("percentPercentile1"), "p99": row.get("percentPercentile99"),
                          "volume_bn": row.get("volumeInBillions")}
            if row.get("targetRateFrom") is not None:
                rows["target"] = {"low": float(row["targetRateFrom"]), "high": float(row["targetRateTo"])}
    if "EFFR" not in rows and "SOFR" not in rows:
        raise ValueError("NY Fed reference rates missing")
    target = rows.get("target")
    out = {"rates": {k: v for k, v in rows.items() if k != "target"}, "target": target, "state": "ok",
           "provider": "Federal Reserve Bank of New York", "source_url": "https://www.newyorkfed.org/markets/reference-rates"}
    if target and "SOFR" in rows:
        out["sofr_minus_top_bp"] = round((rows["SOFR"]["rate"] - target["high"]) * 100, 1)
    if target and "EFFR" in rows:
        out["effr_minus_bottom_bp"] = round((rows["EFFR"]["rate"] - target["low"]) * 100, 1)
    return out


def fetch_funding() -> dict:
    return parse_funding(json.loads(get_bytes(_NYFED, _HEADERS)))


def _rows(text: str, columns: dict) -> dict:
    out = {}
    for record in csv.DictReader(io.StringIO(text)):
        try:
            day = datetime.strptime((record.get("Date") or "").strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        values = {}
        for header, key in columns.items():
            try:
                values[key] = float((record.get(header) or "").strip())
            except ValueError:
                continue
        if values:
            out[day] = values
    return out


def build_real_yields(nominal_text: str, real_text: str, source_url: str = "") -> dict:
    nominal = _rows(nominal_text, {"5 Yr": "n5", "10 Yr": "n10"})
    real = _rows(real_text, {"5 YR": "r5", "10 YR": "r10"})
    days = sorted(d for d in real if d in nominal and "r10" in real[d] and "n10" in nominal[d])
    if len(days) < 2:
        raise ValueError("not enough real-yield history")
    series = [{"date": d, "r10": real[d]["r10"], "be10": nominal[d]["n10"] - real[d]["r10"],
               "r5": real[d].get("r5"), "be5": (nominal[d]["n5"] - real[d]["r5"])
               if "n5" in nominal[d] and real[d].get("r5") is not None else None} for d in days]
    last, prev = series[-1], series[-2]
    back = series[-1 - _WINDOW] if len(series) > _WINDOW else None

    def bp(a, b):
        return None if a is None or b is None else round((a - b) * 100, 1)

    return {
        "as_of": last["date"].isoformat(), "state": "ok",
        "real10": round(last["r10"], 3), "real10_chg_bp": bp(last["r10"], prev["r10"]),
        "real10_chg20_bp": bp(last["r10"], back["r10"]) if back else None,
        "be10": round(last["be10"], 3), "be10_chg_bp": bp(last["be10"], prev["be10"]),
        "be10_chg20_bp": bp(last["be10"], back["be10"]) if back else None,
        "real5": None if last["r5"] is None else round(last["r5"], 3),
        "be5": None if last["be5"] is None else round(last["be5"], 3),
        "provider": "U.S. Department of the Treasury (par real and nominal yield curves)",
        "source_url": source_url, "method": "Breakeven = nominal par yield minus real (TIPS) par yield, same day and tenor.",
    }


def fetch_real_yields(today: date | None = None) -> dict:
    today = today or date.today()
    last_error = None
    for year in (today.year, today.year - 1):
        def url(kind):
            return _TREASURY.format(year=year) + "?" + urlencode(
                {"type": kind, "field_tdr_date_value": year, "_format": "csv"})
        try:
            nominal = get_bytes(url("daily_treasury_yield_curve"), _HEADERS, limit=2_000_000).decode("utf-8-sig")
            real_url = url("daily_treasury_real_yield_curve")
            real = get_bytes(real_url, _HEADERS, limit=2_000_000).decode("utf-8-sig")
            return build_real_yields(nominal, real, real_url)
        except Exception as exc:
            last_error = exc
    raise OSError(f"real yields unavailable ({type(last_error).__name__})")
