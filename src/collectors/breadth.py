"""Market breadth and sentiment.

* Breadth is computed, not scraped: S&P 500 constituents (Wikipedia list) and one year of daily closes per stock
  (Yahoo spark endpoint, 20 symbols per call). Gives % above the 50- and 200-day averages, advancers vs
  decliners, 52-week highs/lows and a 10-day EMA of the advancing share for the breadth-thrust rule.
  Cached 6 hours: it is ~26 requests and only changes once a day.
* Sentiment: CNN Fear & Greed composite and its components, including the put/call ratio (unofficial endpoint).
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..util import get_bytes

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}
_LIST = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_SPARK = "https://query1.finance.yahoo.com/v7/finance/spark?symbols={s}&range=1y&interval=1d"
_FNG = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_MAX_AGE = 6 * 3600


def constituents() -> list:
    table = BeautifulSoup(get_bytes(_LIST, _UA).decode("utf-8"), "html.parser").find("table", {"id": "constituents"})
    symbols = [tr.find("td").get_text(strip=True).replace(".", "-") for tr in table.find_all("tr")[1:] if tr.find("td")]
    if len(symbols) < 450:
        raise ValueError("S&P 500 list looks incomplete")
    return symbols


def _closes(chunk: list) -> dict:
    body = json.loads(get_bytes(_SPARK.format(s=quote(",".join(chunk), safe=",")), _UA))
    out = {}
    for r in (body.get("spark") or {}).get("result") or []:
        try:
            closes = [c for c in r["response"][0]["indicators"]["quote"][0]["close"] if c is not None]
        except (KeyError, IndexError, TypeError):
            continue
        if len(closes) > 30:
            out[r["symbol"]] = closes
    return out


def compute(series: dict) -> dict:
    """Pure breadth maths over {symbol: [closes]}, so it can be tested without the network."""
    n = len(series)
    if n < 100:
        raise ValueError("too few stocks for breadth")
    above50 = above200 = adv = dec = highs = lows = counted200 = 0
    for closes in series.values():
        last = closes[-1]
        if len(closes) >= 50 and last > sum(closes[-50:]) / 50:
            above50 += 1
        if len(closes) >= 200:
            counted200 += 1
            above200 += last > sum(closes[-200:]) / 200
        if len(closes) >= 2:
            adv += closes[-1] > closes[-2]
            dec += closes[-1] < closes[-2]
        highs += last >= max(closes[-252:])
        lows += last <= min(closes[-252:])
    # Advancing share per day over the last 30 sessions, then a 10-day EMA (Zweig breadth-thrust input).
    shares = []
    for back in range(30, 0, -1):
        a = d = 0
        for c in series.values():
            if len(c) > back:
                a += c[-back] > c[-back - 1]
                d += c[-back] < c[-back - 1]
        if a + d:
            shares.append(a / (a + d))
    ema, alpha = [], 2 / 11
    for s in shares:
        ema.append(s if not ema else alpha * s + (1 - alpha) * ema[-1])
    thrust = any(ema[i] < 0.40 and max(ema[i:i + 11]) > 0.615 for i in range(max(0, len(ema) - 20), len(ema))) if ema else False
    return {"stocks": n, "pct_above_50": round(100 * above50 / n, 1),
            "pct_above_200": round(100 * above200 / counted200, 1) if counted200 else None,
            "advancers": adv, "decliners": dec, "new_highs": highs, "new_lows": lows,
            "ema10_adv_share": round(ema[-1], 3) if ema else None, "thrust": thrust}


def fetch_breadth(cache_dir: Path | None = None) -> dict:
    cache = Path(cache_dir) / "breadth.json" if cache_dir else None
    if cache and cache.is_file() and time.time() - cache.stat().st_mtime < _MAX_AGE:
        return json.loads(cache.read_text(encoding="utf-8"))
    symbols = constituents()
    series = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for part in pool.map(lambda i: _closes(symbols[i:i + 20]), range(0, len(symbols), 20)):
            series.update(part)
    result = {**compute(series), "state": "ok" if len(series) >= 0.9 * len(symbols) else "partial",
              "coverage": f"{len(series)} of {len(symbols)} S&P 500 stocks",
              "provider": "Computed from Yahoo Finance daily closes; constituents from Wikipedia",
              "source_url": _LIST,
              "note": "Computed by this app from each constituent's daily closes. Above-average share and "
                      "advancers/decliners describe the last completed session."}
    if cache and result["state"] == "ok":
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def fetch_sentiment() -> dict:
    body = json.loads(get_bytes(_FNG, {**_UA, "Referer": "https://www.cnn.com/", "Origin": "https://www.cnn.com",
                                        "Accept": "application/json"}))
    fg = body.get("fear_and_greed") or {}
    if fg.get("score") is None:
        raise ValueError("Fear & Greed missing")
    names = {"market_momentum_sp500": "S&P 500 momentum", "stock_price_strength": "52-week highs vs lows",
             "stock_price_breadth": "Volume breadth", "put_call_options": "Put/call options",
             "market_volatility_vix": "VIX vs average", "junk_bond_demand": "Junk bond demand",
             "safe_haven_demand": "Safe-haven demand"}
    parts = []
    for key, label in names.items():
        c = body.get(key) or {}
        if c.get("score") is not None:
            last = (c.get("data") or [{}])[-1]
            parts.append({"label": label, "score": round(c["score"], 1), "rating": c.get("rating"),
                          "value": round(last["y"], 3) if isinstance(last.get("y"), (int, float)) else None})
    def r(v):
        return None if v is None else round(v, 1)
    return {"state": "ok", "score": r(fg["score"]), "rating": fg.get("rating"), "previous_close": r(fg.get("previous_close")),
            "week_ago": r(fg.get("previous_1_week")), "month_ago": r(fg.get("previous_1_month")),
            "year_ago": r(fg.get("previous_1_year")), "components": parts, "as_of": fg.get("timestamp"),
            "provider": "CNN Fear & Greed (unofficial endpoint)", "source_url": "https://www.cnn.com/markets/fear-and-greed",
            "note": "A composite of seven indicators scored 0 (extreme fear) to 100 (extreme greed). It has no established "
                    "academic track record as a timing tool; read it as a summary of mood."}
