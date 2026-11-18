"""Daily price/volume collection and >2-sigma move detection.

Data comes from Yahoo Finance's chart API (the same source yfinance wraps). We
fetch it with ``requests`` directly because yfinance's curl backend timed out in
this environment; the data itself is identical.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import requests

from ..models import PriceMove
from ..util import retry

logger = logging.getLogger(__name__)

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _download(co_ticker: str, start: str, end: str) -> pd.DataFrame:
    """Return daily Close/Volume as a DataFrame with a midnight-normalized DatetimeIndex."""
    p1 = int(pd.Timestamp(start).timestamp())
    p2 = int(pd.Timestamp(end).timestamp())

    def _fetch():
        resp = requests.get(
            _CHART_URL.format(ticker=co_ticker),
            params={"period1": p1, "period2": p2, "interval": "1d"},
            headers=_REQUEST_HEADERS,
            timeout=30,
        )
        if resp.status_code in (400, 404):
            return None  # invalid/delisted ticker — skip, not a transient error
        resp.raise_for_status()
        return resp.json()

    data = retry(_fetch, attempts=3, logger=logger)
    if not data:
        return pd.DataFrame()

    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return pd.DataFrame()

    r = result[0]
    timestamps = r.get("timestamp") or []
    quote = (r.get("indicators") or {}).get("quote") or []
    q = quote[0] if quote else {}
    closes = q.get("close") or []
    volumes = q.get("volume") or []

    n = min(len(timestamps), len(closes), len(volumes))
    if n == 0:
        return pd.DataFrame()

    idx = pd.to_datetime(timestamps[:n], unit="s").normalize()
    df = pd.DataFrame({"Close": closes[:n], "Volume": volumes[:n]}, index=idx)
    df = df.dropna(subset=["Close", "Volume"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def _make_move(co, row, ret: float, std: float, vol_mean: float, vol_std: float) -> PriceMove:
    z = (ret / std) if std else 0.0
    last_vol = float(row["Volume"]) if pd.notna(row["Volume"]) else 0.0
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
    """Return {date_str: [PriceMove, ...]} for trading days in [start_date, end_date]."""
    series: dict = {}
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    dl_start = (start - pd.Timedelta(days=lookback_days + 1)).strftime("%Y-%m-%d")
    dl_end = (end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    for co in companies:
        try:
            hist = _download(co.ticker, dl_start, dl_end)
            if hist.empty:
                continue

            returns = hist["Close"].pct_change()
            rolling_std = returns.rolling(60).std(ddof=0)
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
        except Exception as exc:
            logger.warning("market data failed for %s: %s", co.ticker, exc)

    return series


def collect_market(companies: list, as_of_date: Optional[str] = None, lookback_days: int = 90) -> list:
    """Return the latest completed trading day's PriceMove list (default: today)."""
    end = pd.Timestamp(as_of_date) if as_of_date else pd.Timestamp.today()
    # Widen so Mondays and post-holiday mornings still find the prior session.
    start = (end - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
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
