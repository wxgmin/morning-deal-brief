"""Stock movers: biggest gainers, losers and most active US stocks (Yahoo predefined screeners, unofficial).

Filtered to companies of at least $2B market value so the list is about stocks a desk cares about, not penny
names. Before the open these lists still describe the last session; any pre-market move Yahoo reports is shown
alongside so the reader sees both.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from ..util import get_bytes

_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds={sid}&count=60"
LISTS = {"gainers": "day_gainers", "losers": "day_losers", "active": "most_actives"}
MIN_CAP = 2e9


def parse(payload: dict, limit: int = 8) -> list:
    result = ((payload.get("finance") or {}).get("result") or [{}])[0]
    rows = []
    for q in result.get("quotes") or []:
        cap = q.get("marketCap")
        if not isinstance(cap, (int, float)) or cap < MIN_CAP or not q.get("symbol"):
            continue
        rows.append({"symbol": q["symbol"], "name": q.get("shortName") or q.get("longName") or q["symbol"],
                     "price": q.get("regularMarketPrice"), "pct": q.get("regularMarketChangePercent"),
                     "volume": q.get("regularMarketVolume"), "avg_volume": q.get("averageDailyVolume3Month"),
                     "market_cap": cap, "pre_pct": q.get("preMarketChangePercent"),
                     "post_pct": q.get("postMarketChangePercent"), "sector": q.get("sector"),
                     "url": f"https://finance.yahoo.com/quote/{quote(q['symbol'], safe='')}/"})
        if len(rows) == limit:
            break
    for r in rows:
        if r["volume"] and r["avg_volume"]:
            r["rel_volume"] = round(r["volume"] / r["avg_volume"], 1)
    return rows


def collect(watch: set) -> dict:
    out = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {k: pool.submit(get_bytes, _URL.format(sid=sid), {"User-Agent": "Mozilla/5.0"}) for k, sid in LISTS.items()}
        for key, job in jobs.items():
            try:
                rows = parse(json.loads(job.result()))
                for r in rows:
                    r["watch"] = r["symbol"] in watch
                out[key] = rows
            except Exception:
                out[key] = None
    if all(v is None for v in out.values()):
        raise OSError("movers unavailable")
    return {"state": "ok" if all(v is not None for v in out.values()) else "partial", **out,
            "provider": "Yahoo Finance screeners (unofficial)", "source_url": "https://finance.yahoo.com/markets/stocks/gainers/",
            "note": f"US stocks with market value of at least ${MIN_CAP / 1e9:.0f}B. Relative volume = today's volume "
                    "divided by the 3-month average; above 2 usually means news-driven trading."}
