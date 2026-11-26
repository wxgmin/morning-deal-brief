"""US daily bars, with a prior-session baseline and explicit input provenance."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import lru_cache
from urllib.parse import quote, urlencode

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from ..models import PriceMove
from ..util import get_bytes

logger = logging.getLogger(__name__)
_WINDOW = 60
_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
_ALPACA = "https://data.alpaca.markets/v2/stocks/"


@lru_cache(maxsize=1)
def _calendar():
    return xcals.get_calendar("XNYS", start="1990-01-01", end="2050-12-31")


def expected_session(as_of_date: str, cutoff=None) -> str:
    prior = (date.fromisoformat(as_of_date) - timedelta(days=1)).isoformat()
    calendar = _calendar()
    session = calendar.date_to_session(prior, direction="previous")
    while cutoff is not None and calendar.session_close(session) > cutoff:
        session = calendar.previous_session(session)
    return session.date().isoformat()


def _session_index(start, end):
    cal = _calendar()
    first = cal.date_to_session(start, direction="next")
    last = cal.date_to_session(end, direction="previous")
    return cal.sessions_in_range(first, last) if first <= last else pd.DatetimeIndex([])


def _frame(rows, timestamps, timezone):
    index = pd.to_datetime(timestamps, utc=True).tz_convert(timezone).tz_localize(None).normalize()
    frame = pd.DataFrame(rows, index=index).apply(pd.to_numeric, errors="coerce")
    return frame[~frame.index.duplicated(keep=False)].sort_index()


def _download(ticker, start, end):
    url = _YAHOO + quote(ticker, safe="")
    params = {
        "period1": int(pd.Timestamp(start, tz="America/New_York").timestamp()),
        "period2": int(pd.Timestamp(end, tz="America/New_York").timestamp()), "interval": "1d",
    }
    result = json.loads(get_bytes(url, {"User-Agent": "Mozilla/5.0"}, params=params))["chart"]["result"][0]
    indicators = result["indicators"]
    bars = indicators["quote"][0]
    adjusted = indicators.get("adjclose") or [{}]
    frame = _frame({"Close": bars["close"], "Adjusted": adjusted[0].get("adjclose"), "Volume": bars.get("volume")},
                   pd.to_datetime(result["timestamp"], unit="s", utc=True),
                   result["meta"]["exchangeTimezoneName"])
    return frame, {
        "provider": "Yahoo Finance", "dataset_url": url + "?" + urlencode(params),
        "source_url": f"https://finance.yahoo.com/quote/{quote(ticker, safe='')}/history/",
        "currency": result["meta"]["currency"], "return_basis": "total-return adjusted close",
        "close_basis": "provider close", "volume_basis": "provider volume",
    }


def _download_alpaca(ticker, start, end, settings):
    if not (settings.alpaca_key and settings.alpaca_secret):
        raise ValueError("Alpaca credentials are missing")
    url = _ALPACA + quote(ticker, safe="") + "/bars"
    params = {"timeframe": "1Day", "start": start, "end": end, "feed": settings.alpaca_feed,
              "adjustment": "split", "limit": 10000}
    headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
    bars, seen = [], set()
    for _ in range(20):
        payload = json.loads(get_bytes(url, headers, params=params))
        bars.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
        if token in seen:
            raise ValueError("duplicate Alpaca pagination token")
        seen.add(token)
        params["page_token"] = token
    else:
        raise ValueError("Alpaca pagination limit exceeded")
    frame = _frame(
        {"Close": [b["c"] for b in bars], "Adjusted": [b["c"] for b in bars],
         "Volume": [b["v"] for b in bars]},
        [b["t"] for b in bars], "America/New_York",
    )
    query = {k: v for k, v in params.items() if k != "page_token"}
    return frame, {
        "provider": f"Alpaca {settings.alpaca_feed.upper()}",
        "dataset_url": url + "?" + urlencode(query), "source_url": url + "?" + urlencode(query),
        "currency": "USD", "return_basis": "split-adjusted close",
        "close_basis": "split-adjusted close", "volume_basis": "split-adjusted volume",
    }


def _moves(company, history, evidence, download_start, start, end):
    hist = history.reindex(_session_index(download_start, end))
    for column in ("Close", "Adjusted", "Volume"):
        hist[column] = pd.to_numeric(hist[column], errors="coerce").where(
            np.isfinite(pd.to_numeric(hist[column], errors="coerce"))
        )
    hist[["Close", "Adjusted"]] = hist[["Close", "Adjusted"]].where(hist[["Close", "Adjusted"]] > 0)
    hist["Volume"] = hist["Volume"].where(hist["Volume"] > 0)
    previous = hist["Adjusted"].shift()
    returns = hist["Adjusted"].pct_change(fill_method=None)
    returns = returns.where(np.isfinite(returns))
    baseline = returns.shift().rolling(_WINDOW)
    volume_baseline = hist["Volume"].shift().rolling(_WINDOW)
    return_mean, return_std = baseline.mean(), baseline.std(ddof=0)
    vol_mean, vol_std = volume_baseline.mean(), volume_baseline.std(ddof=0)
    window = hist.loc[start:end].dropna(subset=["Close"])
    result = {}
    for session, row in window.iterrows():
        ret, mean, std = returns[session], return_mean[session], return_std[session]
        vmean, vstd = vol_mean[session], vol_std[session]
        session_date = session.date().isoformat()
        location = hist.index.get_loc(session)
        baseline_bars = hist.iloc[max(0, location - _WINDOW - 1):location]
        inputs = {
            **evidence, "session": session_date, "baseline_sessions": _WINDOW,
            "adjusted_close": float(row["Adjusted"]) if pd.notna(row["Adjusted"]) else None,
            "previous_adjusted_close": float(previous[session]) if pd.notna(previous[session]) else None,
            "return_mean": float(mean) if pd.notna(mean) else None,
            "return_std": float(std) if pd.notna(std) else None,
            "volume_mean": float(vmean) if pd.notna(vmean) else None,
            "volume_std": float(vstd) if pd.notna(vstd) else None,
            "prior_bars": [
                [idx.date().isoformat(), float(bar.Adjusted) if pd.notna(bar.Adjusted) else None,
                 float(bar.Volume) if pd.notna(bar.Volume) else None]
                for idx, bar in baseline_bars.iterrows()
            ],
        }
        z = float((ret - mean) / std) if pd.notna(ret) and pd.notna(std) and std > 1e-12 else None
        volume = float(row["Volume"]) if pd.notna(row["Volume"]) else None
        vz = float((volume - vmean) / vstd) if volume is not None and pd.notna(vstd) and vstd > 1e-9 else None
        result[session_date] = PriceMove(
            company.ticker, company.name, company.role, session_date, float(row["Close"]),
            float(ret * 100) if pd.notna(ret) else None, volume, vz, z,
            z is not None and abs(z) >= 2.0, inputs,
        )
    return result


def collect_market_series(companies: list, start_date: str, end_date: str, lookback_days: int = 120,
                          *, settings=None) -> dict:
    """Return per-session rows. Providers are selected per ticker with visible provenance."""
    if end_date < start_date:
        return {}
    dl_start = (date.fromisoformat(start_date) - timedelta(days=lookback_days)).isoformat()
    dl_end = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    yahoo = lambda ticker: _download(ticker, dl_start, dl_end)
    alpaca = lambda ticker: _download_alpaca(ticker, dl_start, dl_end, settings)
    primary = getattr(settings, "market_provider", "yahoo")
    fallback = getattr(settings, "market_fallback", "none")
    providers = {"yahoo": yahoo, "alpaca": alpaca}
    latest_sessions = _session_index(start_date, end_date)
    if latest_sessions.empty:
        return {}

    def quality(move):
        return (move.pct_change is not None,
                int(move.z_score is not None) + int(move.volume_z is not None),
                move.volume is not None)

    def for_company(co):
        found = {}
        for name in dict.fromkeys((primary, fallback)):
            if name not in providers:
                continue
            try:
                history, evidence = providers[name](co.ticker)
                moves = _moves(co, history, evidence, dl_start, start_date, end_date)
                for day, move in moves.items():
                    if day not in found or quality(move) > quality(found[day]):
                        found[day] = move
                if len(found) == len(latest_sessions) and all(quality(move) == (True, 2, True) for move in found.values()):
                    break
            except Exception as exc:
                logger.warning("Market provider %s unavailable for %s (%s)", name, co.ticker, type(exc).__name__)
        return found

    series = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        for found in executor.map(for_company, companies):
            for session, move in found.items():
                series.setdefault(session, []).append(move)
    return series


def collect_market(companies: list, as_of_date: str = None, lookback_days: int = 120,
                   *, settings=None, cutoff=None) -> list:
    session = expected_session(as_of_date or date.today().isoformat(), cutoff)
    return collect_market_series(companies, session, session, lookback_days, settings=settings).get(session, [])


def market_stats_text(move: PriceMove) -> str:
    evidence = move.evidence
    return (
        f"{move.ticker} changed {move.pct_change:+.2f}% on {move.date} "
        f"({move.z_score:+.1f} standard deviations from the prior 60-session mean; "
        f"{evidence.get('return_basis', 'adjusted close')})."
    )
