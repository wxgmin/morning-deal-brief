"""Daily price/volume collection and >2-sigma move detection.

Data comes from Yahoo Finance's chart API (the same source yfinance wraps). We
fetch it with ``requests`` directly because yfinance's curl backend timed out in
this environment; the data itself is identical.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
import requests

from ..models import PriceMove

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _download(co_ticker: str, start: str, end: str) -> pd.DataFrame:
    """Return daily Close/Volume as a DataFrame with a DatetimeIndex."""
    p1 = int(pd.Timestamp(start).timestamp())
    p2 = int(pd.Timestamp(end).timestamp())
    resp = requests.get(
        _CHART_URL.format(ticker=co_ticker),
        params={"period1": p1, "period2": p2, "interval": "1d"},
        headers=_REQUEST_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    idx = pd.to_datetime(timestamps, unit="s")
    df = pd.DataFrame({"Close": quote.get("close"), "Volume": quote.get("volume")}, index=idx)
    return df.dropna(subset=["Close"])


def _make_move(co, row, ret: float, std: float, vol_mean: float, vol_std: float) -> PriceMove:
    z = (ret / std) if std else 0.0
    last_vol = float(row["Volume"])
    vol_z = ((last_vol - vol_mean) / vol_std) if vol_std else 0.0
    idx = row.name
    date_str = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
    return PriceMove(
        ticker=co.ticker,
        company=co.name,
        role=co.role,
        date=date_str,
        close=float(row["Close"]),
        pct_change=ret * 100.0,
        volume=last_vol,
        volume_z=vol_z,
        z_score=z,
        large_move=abs(z) >= 2.0,
    )


def collect_market_series(companies: list, start_date: str, end_date: str, lookback_days: int = 90) -> dict:
    """Return {date_str: [PriceMove, ...]} for trading days in [start_date, end_date].

    One download per company covers the window plus the lookback used to compute a
    trailing 60-day std, so backfilling many days is cheap.
    """
    series: dict = {}
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    dl_start = (start - pd.Timedelta(days=lookback_days + 1)).strftime("%Y-%m-%d")
    dl_end = (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    for co in companies:
        try:
            hist = _download(co.ticker, dl_start, dl_end)
            if hist is None or hist.empty:
                continue

            returns = hist["Close"].pct_change()
            rolling_std = returns.rolling(60).std()
            vol_mean = hist["Volume"].rolling(60).mean()
            vol_std = hist["Volume"].rolling(60).std(ddof=0)

            window = hist[(hist.index >= start) & (hist.index <= end)]
            for idx, row in window.iterrows():
                ret = returns.loc[idx]
                std = rolling_std.loc[idx]
                if pd.isna(ret) or pd.isna(std):
                    continue
                move = _make_move(
                    co, row, float(ret), float(std), float(vol_mean.loc[idx]), float(vol_std.loc[idx])
                )
                series.setdefault(move.date, []).append(move)
        except Exception:
            continue

    return series


def collect_market(companies: list, as_of_date: Optional[str] = None, lookback_days: int = 90) -> list:
    """Return the latest available day's PriceMove list (default: today)."""
    end = pd.Timestamp(as_of_date) if as_of_date else pd.Timestamp.today()
    start = (end - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    series = collect_market_series(companies, start, end.strftime("%Y-%m-%d"), lookback_days)
    if not series:
        return []
    latest = max(series.keys())
    return series[latest]


def market_stats_text(move: PriceMove) -> str:
    """Groundable text summarizing a company's computed market move."""
    direction = "up" if move.pct_change >= 0 else "down"
    return (
        f"{move.company} ({move.ticker}) closed at ${move.close:.2f} on {move.date}, "
        f"{direction} {move.pct_change:+.2f}% ({move.z_score:+.1f} standard deviations vs "
        f"the trailing 60-day distribution). Volume z-score {move.volume_z:+.1f}."
    )
