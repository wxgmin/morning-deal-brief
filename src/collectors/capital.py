"""Public end-of-day credit benchmarks and the reported U.S. IPO calendar."""
from __future__ import annotations

import csv
import io
import json
import math
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from ..util import get_bytes

_SPREADS = {
    "BAMLC0A0CM": "US investment grade",
    "BAMLH0A0HYM2": "US high yield",
}
_IPO_SOURCE = "https://www.nasdaq.com/market-activity/ipos"
_HEADERS = {"User-Agent": "Mozilla/5.0 (MorningBrief)", "Accept": "application/json"}
_IPO_DATES = {"upcoming": ("expectedPriceDate", "expectedPricingDate", "pricedDate"),
              "priced": ("pricedDate",), "filed": ("filedDate",), "withdrawn": ("withdrawDate",)}


def parse_spreads(body: str, today: date) -> dict:
    reader = csv.DictReader(io.StringIO(body))
    columns = {column.split("_")[0]: column for column in reader.fieldnames or []}
    observations = {sid: {} for sid in _SPREADS}
    for row in reader:
        try:
            day = date.fromisoformat(row.get("observation_date") or "")
        except ValueError:
            continue
        if day > today:
            continue
        for sid in observations:
            try:
                value = float(row.get(columns.get(sid)) or "")
            except ValueError:
                continue
            if math.isfinite(value):
                observations[sid][day] = round(value * 100, 2)

    rows = []
    for sid, values in observations.items():
        dates = sorted(values, reverse=True)
        if len(dates) < 2:
            continue
        latest, prior = dates[:2]
        back = dates[20] if len(dates) > 20 else None  # 20 observations earlier = ~one trading month
        rows.append({"series": sid, "change_20d_bp": round(values[latest] - values[back], 2) if back else None,"label": _SPREADS[sid], "as_of": latest.isoformat(), "previous_date": prior.isoformat(),
                     "level_bp": values[latest], "prior_bp": values[prior],
                     "change_bp": round(values[latest] - values[prior], 2),
                     "stale": (today - latest).days > 5, "source_url": f"https://fred.stlouisfed.org/series/{sid}"})
    if not rows:
        raise ValueError("no credit spread history")
    return {"state": "ok" if len(rows) == len(_SPREADS) and not any(r["stale"] for r in rows) else "partial",
            "rows": rows, "provider": "ICE BofA via FRED"}


def fetch_spreads(today: date) -> dict:
    query = urlencode({"id": ",".join(_SPREADS), "cosd": str(today - timedelta(days=45))})
    url = f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?{query}"
    result = parse_spreads(get_bytes(url, limit=500_000).decode("utf-8-sig"), today)
    result["dataset_url"] = url
    return result


def parse_ipos(payload: dict, today: date, until: date) -> list:
    data = payload.get("data")
    if not isinstance(data, dict) or not any(key in data for key in _IPO_DATES):
        raise ValueError("IPO calendar response was incomplete")
    out = []
    for status, keys in _IPO_DATES.items():
        for entry in (data.get(status) or {}).get("rows") or []:
            name = entry.get("companyName")
            if not name:
                continue
            raw_date = next((entry[key] for key in keys if entry.get(key)), "")
            try:
                day = datetime.strptime(raw_date, "%m/%d/%Y").date()
            except ValueError:
                continue
            if not (today <= day <= until if status == "upcoming" else today - timedelta(days=7) <= day <= today):
                continue
            out.append({"date": day.isoformat(), "status": status, "name": str(name)[:100],
                        "symbol": str(entry.get("proposedTickerSymbol") or "")[:18],
                        "exchange": str(entry.get("proposedExchange") or "")[:50],
                        "amount": str(entry.get("dollarValueOfSharesOffered") or "")[:30],
                        "id": str(entry.get("dealID") or "")})
    return out


def fetch_ipos(today: date) -> dict:
    until = today + timedelta(days=7)
    months = dict.fromkeys(day.strftime("%Y-%m") for day in (today - timedelta(days=7), today, until))
    ipo_rows, failed = {}, []
    for month in months:
        url = "https://api.nasdaq.com/api/ipo/calendar?" + urlencode({"date": month})
        try:
            for row in parse_ipos(json.loads(get_bytes(url, _HEADERS, limit=1_000_000)), today, until):
                ipo_rows[(row["status"], row["id"] or row["name"], row["date"])] = row
        except (OSError, ValueError, AttributeError, TypeError):
            failed.append(month)
    if len(failed) == len(months):
        raise OSError("IPO calendar unavailable")
    rows = list(ipo_rows.values())
    upcoming = sorted((row for row in rows if row["status"] == "upcoming"), key=lambda row: row["date"])
    latest = sorted((row for row in rows if row["status"] != "upcoming"), key=lambda row: row["date"], reverse=True)
    return {"state": "partial" if failed else "ok", "rows": upcoming[:6] + latest[:8],
            "source_url": _IPO_SOURCE, "provider": "Nasdaq IPO calendar"}
