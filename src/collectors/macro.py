"""Cross-asset quotes and the official Treasury curve, each carrying its source and as-of time.

Quote rule: *last* vs the previous completed daily close from the same provider. Yields change in basis
points, everything else in percent. A move is "abnormal" when it is at least 2 standard deviations of the
instrument's own recent daily changes (so a 1% day means something different for gold and for the VIX).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from statistics import mean, pstdev
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from ..util import get_bytes

logger = logging.getLogger(__name__)

_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
             "daily-treasury-rates.csv/{year}/all")
_HEADERS = {"User-Agent": "Mozilla/5.0 (MorningBrief)"}
_BASELINE = 40
_MIN_BASELINE = 15
STALE_AFTER_DAYS = 5
ABNORMAL_Z = 2.0


def _num(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def yahoo_symbol(ticker: str) -> str:
    """Watchlist tickers are exchange tickers; Yahoo writes US share classes with a dash (BRK.B -> BRK-B)."""
    return re.sub(r"^([A-Z]{1,5})\.([AB])$", r"\1-\2", ticker.upper())


def search_companies(query: str) -> list:
    params = {"q": query, "quotesCount": 12, "newsCount": 0}
    data = json.loads(get_bytes(_SEARCH, _HEADERS, params=params, limit=300_000, attempts=1))
    rows = []
    for quote_row in data.get("quotes", []):
        if quote_row.get("quoteType") not in {"EQUITY", "ETF"}:
            continue
        symbol = quote_row.get("symbol", "").upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", symbol):
            continue
        rows.append({"ticker": symbol, "name": (quote_row.get("longname") or quote_row.get("shortname")
                                               or symbol)[:100],
                     "exchange": quote_row.get("exchDisp") or quote_row.get("exchange") or ""})
    return rows


def _chart(symbol, **params):
    url = _YAHOO + quote(symbol, safe="")
    body = json.loads(get_bytes(url, _HEADERS, params=params))
    chart = body.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        raise ValueError("symbol not found")
    return chart["result"][0], url + "?" + urlencode(params)


def _session_date(ts, tz):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(tz)).date()


def _year_context(history):
    """Where today's level sits in its own trailing year: 0 = 1-year low, 100 = 1-year high."""
    year = history[-252:]
    if len(year) < 120:
        return {"pctile_1y": None, "low_1y": None, "high_1y": None}
    below = sum(1 for v in year[:-1] if v < year[-1])
    return {"pctile_1y": round(100 * below / (len(year) - 1)), "low_1y": round(min(year), 6),
            "high_1y": round(max(year), 6)}


CHART_RANGES = {"1mo": "1d", "6mo": "1d", "1y": "1d", "5y": "1wk"}
_CHART_CACHE = {}


def history(symbol: str, span: str) -> dict:
    """Closes for the interactive chart. Symbol and range are validated by the caller; cached 10 minutes."""
    import re
    import time as _time
    if span not in CHART_RANGES or not re.fullmatch(r"[A-Za-z0-9^=.\-]{1,24}", symbol or ""):
        raise ValueError("invalid chart request")
    key = (symbol, span)
    hit = _CHART_CACHE.get(key)
    if hit and _time.monotonic() - hit[0] < 600:
        return hit[1]
    result, url = _chart(symbol, range=span, interval=CHART_RANGES[span])
    points = [(int(t), round(float(c), 6)) for t, c in zip(result.get("timestamp") or [],
              ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []) if _num(c)]
    if len(points) < 2:
        raise ValueError("not enough history")
    closes = [c for _, c in points]
    out = {"symbol": symbol, "range": span, "points": points, "low": min(closes), "high": max(closes),
           "first": closes[0], "last": closes[-1], "chg_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
           "provider": "Yahoo Finance", "dataset_url": url}
    if len(_CHART_CACHE) > 500:
        _CHART_CACHE.clear()
    _CHART_CACHE[key] = (_time.monotonic(), out)
    return out


def build_quote(inst: dict, result: dict, dataset_url: str, now: datetime | None = None) -> dict:
    """Pure function over a Yahoo chart payload, so it can be tested without the network."""
    now = now or datetime.now(timezone.utc)
    meta = result.get("meta") or {}
    tz = meta.get("exchangeTimezoneName") or "UTC"
    stamps = result.get("timestamp") or []
    closes_raw = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    history_by_date = {}
    for stamp, close in zip(stamps, closes_raw):
        stamp, close = _num(stamp), _num(close)
        if stamp is not None and close is not None and close > 0 and stamp <= now.timestamp():
            history_by_date[_session_date(stamp, tz)] = (stamp, close)
    bars = sorted(history_by_date.items())
    if len(bars) < 2:
        raise ValueError("not enough history")
    kind = inst.get("kind", "price")
    scale = 1.0
    if kind == "yield" and inst["symbol"] in {"^IRX", "^FVX", "^TNX", "^TYX"} and bars[-1][1][1] > 25:
        scale = 0.1
    series = [c * scale for _, (_, c) in bars]

    live = _num(meta.get("regularMarketPrice"))
    live = live * scale if live and live > 0 else None
    live_time = _num(meta.get("regularMarketTime"))
    last_date, (last_stamp, _) = bars[-1]
    use_print = live is not None and live_time is not None and last_stamp <= live_time <= now.timestamp()
    newer_than_bar = use_print and _session_date(live_time, tz) > last_date
    last = live if use_print else series[-1]
    history = series + [last] if newer_than_bar else series[:-1] + [last]
    prev = history[-2]

    is_yield = kind == "yield"
    changes = []
    for a, b in zip(history[:-1], history[1:]):
        changes.append((b - a) * 100 if is_yield else (b / a - 1) * 100)
    chg = changes[-1]
    baseline = changes[-1 - _BASELINE:-1]
    z = None
    avg, sd = None, None
    if len(baseline) >= _MIN_BASELINE:
        avg, sd = mean(baseline), pstdev(baseline)
        if sd > 1e-9:
            z = (chg - avg) / sd

    def lookback(n):
        if len(history) <= n:
            return None
        base = history[-1 - n]
        return (history[-1] - base) * 100 if is_yield else (history[-1] / base - 1) * 100

    as_of = datetime.fromtimestamp(live_time if use_print else last_stamp, timezone.utc)
    stale_after = timedelta(hours=12) if kind == "crypto" else timedelta(days=STALE_AFTER_DAYS)
    quote_row = {
        "symbol": inst["symbol"], "label": inst.get("label") or inst["symbol"], "group": inst.get("group"),
        "kind": kind, "dp": inst.get("dp", 2), "pinned": bool(inst.get("pinned")), "role": inst.get("role"),
        "name": inst.get("name"), "region": inst.get("region"),
        "last": round(last, 6), "prev": round(prev, 6),
        "chg": round(chg, 4), "chg_unit": "bp" if is_yield else "%",
        "chg_5d": None if lookback(5) is None else round(lookback(5), 4),
        "chg_1m": None if lookback(21) is None else round(lookback(21), 4),
        "z": None if z is None else round(z, 2),
        "abnormal": z is not None and abs(z) >= ABNORMAL_Z,
        "baseline": {"samples": len(baseline), "mean": avg, "standard_deviation": sd},
        "spark": [round(v, 5) for v in history[-31:]],
        **_year_context(history),
        # Weekly-sampled year for the click-to-expand chart (about 52 points keeps the archive small).
        "hist_1y": [round(v, 5) for v in (history[-252:][::5] + ([history[-1]] if len(history[-252:]) % 5 != 1 else []))],
        "as_of": as_of.isoformat(),
        "session": (_session_date(live_time, tz) if newer_than_bar else last_date).isoformat(),
        "currency": meta.get("currency"), "state": "stale" if now - as_of > stale_after else "ok",
        "provider": "Yahoo Finance", "source_url": f"https://finance.yahoo.com/quote/{quote(inst['symbol'], safe='')}/",
        "dataset_url": dataset_url,
    }
    return quote_row


def extended_hours(result: dict) -> dict | None:
    """Pre-market / after-hours print versus the last regular close, from 5-minute bars."""
    meta = result.get("meta") or {}
    regular_price, regular_time = _num(meta.get("regularMarketPrice")), _num(meta.get("regularMarketTime"))
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = _num(period.get("start")), _num(period.get("end"))
    if not (regular_price and regular_time and start and end):
        return None
    stamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    latest = None
    for t, c in zip(stamps, closes):
        c = _num(c)
        if c and t > regular_time and (t < start or t >= end):
            latest = (t, c)
    if not latest:
        return None
    label = "Pre-market" if latest[0] < start else "After-hours"
    if abs(latest[1] / regular_price - 1) < 0.00005:  # an unchanged print carries no information
        return None
    return {"label": label, "price": round(latest[1], 4),
            "pct": round((latest[1] / regular_price - 1) * 100, 3),
            "at": datetime.fromtimestamp(latest[0], timezone.utc).isoformat()}


def fetch_quote(inst: dict, *, with_extended: bool = False, now: datetime | None = None) -> dict:
    symbol = inst["symbol"]
    try:
        result, url = _chart(symbol, range="1y", interval="1d")
        row = build_quote(inst, result, url, now)
        if with_extended:
            try:
                ext_result, _ = _chart(symbol, range="1d", interval="5m", includePrePost="true")
                row["extended"] = extended_hours(ext_result)
            except Exception as exc:  # extended hours are a bonus; never fail the quote for them
                logger.info("Extended-hours data unavailable for %s (%s)", symbol, type(exc).__name__)
                row["extended"] = None
        return row
    except Exception as exc:
        logger.warning("Quote unavailable for %s (%s)", symbol, type(exc).__name__)
        return {"symbol": symbol, "label": inst.get("label") or symbol, "group": inst.get("group"),
                "kind": inst.get("kind", "price"), "dp": inst.get("dp", 2), "pinned": bool(inst.get("pinned")),
                "role": inst.get("role"), "name": inst.get("name"), "region": inst.get("region"),
                "state": "unavailable",
                "error": type(exc).__name__, "provider": "Yahoo Finance",
                "source_url": f"https://finance.yahoo.com/quote/{quote(symbol, safe='')}/"}


def fetch_quotes(instruments: list, *, with_extended: bool = False, max_workers: int = 8) -> list:
    """Order-preserving, bounded-parallel quote fetch. One failure never affects the others."""
    if not instruments:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(lambda inst: fetch_quote(inst, with_extended=with_extended), instruments))


# ───────────────────────────── official Treasury curve ─────────────────────────────

_TENORS = [("1 Mo", "1M", 1 / 12), ("2 Mo", "2M", 2 / 12), ("3 Mo", "3M", 0.25), ("4 Mo", "4M", 4 / 12),
           ("6 Mo", "6M", 0.5), ("1 Yr", "1Y", 1), ("2 Yr", "2Y", 2), ("3 Yr", "3Y", 3), ("5 Yr", "5Y", 5),
           ("7 Yr", "7Y", 7), ("10 Yr", "10Y", 10), ("20 Yr", "20Y", 20), ("30 Yr", "30Y", 30)]


def parse_treasury_curve(text: str, source_url: str = "") -> dict:
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        try:
            when = datetime.strptime((record.get("Date") or "").strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        values = {}
        for header, short, years in _TENORS:
            v = _num((record.get(header) or "").strip())
            if v is not None:
                values[short] = v
        if values:
            rows.append((when, values))
    rows.sort(reverse=True)
    if len(rows) < 2:
        raise ValueError("Treasury curve has too few rows")
    (today, now_v), (prior, prev_v) = rows[0], rows[1]
    points = []
    for header, short, years in _TENORS:
        if short in now_v:
            prev = prev_v.get(short)
            points.append({"tenor": short, "years": round(years, 4), "yield": now_v[short], "prev": prev,
                           "chg_bp": None if prev is None else round((now_v[short] - prev) * 100, 1)})

    def spread(name, long_, short_):
        if long_ in now_v and short_ in now_v:
            value = round((now_v[long_] - now_v[short_]) * 100, 1)
            before = None
            if long_ in prev_v and short_ in prev_v:
                before = round((prev_v[long_] - prev_v[short_]) * 100, 1)
            return {"name": name, "bp": value, "prev_bp": before,
                    "chg_bp": None if before is None else round(value - before, 1),
                    "inverted": value < 0}
        return None

    spreads = [s for s in (spread("2s10s", "10Y", "2Y"), spread("3m10y", "10Y", "3M"),
                           spread("5s30s", "30Y", "5Y")) if s]
    return {"as_of": today.isoformat(), "prior": prior.isoformat(), "points": points, "spreads": spreads,
            "provider": "U.S. Department of the Treasury (par yield curve)", "source_url": source_url,
            "state": "ok"}


def fetch_treasury_curve(today: date | None = None) -> dict:
    today = today or date.today()
    last_error = None
    for year in (today.year, today.year - 1):
        url = _TREASURY.format(year=year) + "?" + urlencode(
            {"type": "daily_treasury_yield_curve", "field_tdr_date_value": year, "_format": "csv"})
        try:
            text = get_bytes(url, _HEADERS, limit=2_000_000).decode("utf-8-sig")
            return parse_treasury_curve(text, url)
        except Exception as exc:
            last_error = exc
    raise OSError(f"Treasury curve unavailable ({type(last_error).__name__})")
