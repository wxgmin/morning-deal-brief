"""Speculative positioning from the CFTC Commitments of Traders (weekly, Tuesday data, released Friday).

Financial futures use the Traders in Financial Futures report (leveraged funds); commodities use the
Disaggregated report (managed money). For each market: net position, weekly change, and where the net sits in
its own 3-year history (percentile). Research support for "fade the extreme" is weak, so extremes are shown as
context ("crowded"), never as a signal to trade against.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

from ..util import get_bytes

_BASE = "https://publicreporting.cftc.gov/resource/{ds}.json"
_WEEKS = 156
MARKETS = [
    ("S&P 500 (E-mini)", "gpe5-46if", "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "lev_money"),
    ("Nasdaq-100 (mini)", "gpe5-46if", "NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE", "lev_money"),
    ("US 10-year note", "gpe5-46if", "UST 10Y NOTE - CHICAGO BOARD OF TRADE", "lev_money"),
    ("US 2-year note", "gpe5-46if", "UST 2Y NOTE - CHICAGO BOARD OF TRADE", "lev_money"),
    ("US Dollar Index", "gpe5-46if", "USD INDEX - ICE FUTURES U.S.", "lev_money"),
    ("Japanese yen", "gpe5-46if", "JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE", "lev_money"),
    ("Euro", "gpe5-46if", "EURO FX - CHICAGO MERCANTILE EXCHANGE", "lev_money"),
    ("VIX futures", "gpe5-46if", "VIX FUTURES - CBOE FUTURES EXCHANGE", "lev_money"),
    ("WTI crude", "72hh-3qpy", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE", "m_money"),
    ("Gold", "72hh-3qpy", "GOLD - COMMODITY EXCHANGE INC.", "m_money"),
    ("Copper", "72hh-3qpy", "COPPER- #1 - COMMODITY EXCHANGE INC.", "m_money"),
]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarise(label: str, rows: list, trader: str) -> dict:
    long_key, short_key = (f"{trader}_positions_long", f"{trader}_positions_short") if trader == "lev_money" else \
        (f"{trader}_positions_long_all", f"{trader}_positions_short_all")
    series = []
    for r in rows:
        lo, sh, oi = _num(r.get(long_key)), _num(r.get(short_key)), _num(r.get("open_interest_all"))
        if lo is not None and sh is not None:
            series.append({"date": r["report_date_as_yyyy_mm_dd"][:10], "net": lo - sh, "oi": oi})
    series.sort(key=lambda x: x["date"])
    if len(series) < 2:
        raise ValueError("not enough COT history")
    last, prev = series[-1], series[-2]
    history = [x["net"] for x in series]
    pct = round(100 * sum(1 for v in history[:-1] if v < last["net"]) / (len(history) - 1)) if len(history) > 20 else None
    # Describe the position against its own history, not by sign: a net short at the 94th percentile is the
    # *least short* in 3 years, which "crowded long" would misstate.
    state = None
    if pct is not None and pct >= 90:
        state = "longest in 3 years" if last["net"] > 0 else "least short in 3 years"
    elif pct is not None and pct <= 10:
        state = "shortest in 3 years" if last["net"] < 0 else "least long in 3 years"
    return {"label": label, "trader": "Leveraged funds" if trader == "lev_money" else "Managed money",
            "date": last["date"], "net": int(last["net"]), "change": int(last["net"] - prev["net"]),
            "net_pct_oi": round(100 * last["net"] / last["oi"], 1) if last["oi"] else None,
            "pctile_3y": pct, "extreme": state, "weeks": len(series)}


def fetch_market(label, ds, name, trader):
    params = {"$where": f"market_and_exchange_names='{name}'", "$order": "report_date_as_yyyy_mm_dd DESC",
              "$limit": _WEEKS}
    return summarise(label, json.loads(get_bytes(_BASE.format(ds=ds) + "?" + urlencode(params),
                                                 {"User-Agent": "MorningBrief/3.0"})), trader)


def collect(cache_dir=None) -> dict:
    """CFTC data changes once a week, so a result younger than 12 hours is reused instead of 11 downloads."""
    import time
    from pathlib import Path
    cache = Path(cache_dir) / "positioning.json" if cache_dir else None
    if cache and cache.is_file() and time.time() - cache.stat().st_mtime < 12 * 3600:
        return json.loads(cache.read_text(encoding="utf-8"))
    result = _collect()
    if cache and result["state"] == "ok":
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def _collect() -> dict:
    rows, failed = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for spec, job in [(m, pool.submit(fetch_market, *m)) for m in MARKETS]:
            try:
                rows.append(job.result())
            except Exception:
                failed.append(spec[0])
    if not rows:
        raise OSError("CFTC positioning unavailable")
    return {"state": "ok" if not failed else "partial", "rows": rows, "unavailable": failed,
            "as_of": max(r["date"] for r in rows), "provider": "CFTC Commitments of Traders",
            "source_url": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
            "note": "Net = long minus short contracts held by leveraged funds (financial futures) or managed money "
                    "(commodities), as of Tuesday. Percentile compares this week's net with the past 3 years. "
                    "Crowded positioning shows where a reversal could be sharp; research does not support trading "
                    "against it on its own."}
