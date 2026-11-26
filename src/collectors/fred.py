"""US economy and liquidity series from FRED/ALFRED (St. Louis Fed), keyless.

ALFRED's graph CSV serves one series per request without a key. It rejects browser-style User-Agents and
returns a binary file when series of different frequencies are mixed, so each series is fetched alone with
no User-Agent header. All values are official statistics; the app only computes changes, averages and
percentiles from them.
"""
from __future__ import annotations

import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import json

from ..util import get_bytes

_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}&cosd={start}"
_MAX_AGE = 6 * 3600
SERIES = {  # id: (years of history, label)
    "ICSA": (1.5, "Initial jobless claims"),
    "UNRATE": (2, "Unemployment rate"),
    "SAHMREALTIME": (1, "Sahm rule indicator (real time)"),
    "THREEFYTP10": (0.5, "10-year term premium (Kim-Wright)"),
    "WRESBAL": (1, "Reserve balances"),
    "RRPONTSYD": (0.5, "Overnight reverse repo"),
    "IORB": (0.3, "Interest on reserve balances"),
    "T10Y2Y": (2.1, "10Y minus 2Y spread"),
    "DGS2": (0.2, "2-year Treasury yield"),
    "BAMLH0A0HYM2": (5, "High-yield OAS"),
    "BAMLC0A0CM": (5, "Investment-grade OAS"),
}


def parse(text: str) -> list:
    rows = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for r in reader:
        if len(r) >= 2:
            try:
                rows.append((date.fromisoformat(r[0]), float(r[1])))
            except ValueError:
                continue  # FRED marks missing days with "."
    return rows


def _fetch(sid: str, years: float, today: date) -> list:
    start = (today - timedelta(days=int(years * 365))).isoformat()
    return parse(get_bytes(_URL.format(sid=sid, start=start), None, limit=3_000_000).decode("utf-8"))


def _pctile(values, last):
    return round(100 * sum(1 for v in values[:-1] if v < last) / (len(values) - 1)) if len(values) > 20 else None


def summarise(data: dict) -> dict:
    out = {}
    claims = data.get("ICSA") or []
    if len(claims) >= 56:
        avg4 = [sum(v for _, v in claims[i - 3:i + 1]) / 4 for i in range(3, len(claims))]
        low12 = min(avg4[-52:])
        out["claims"] = {"latest": int(claims[-1][1]), "date": claims[-1][0].isoformat(), "avg4": round(avg4[-1]),
                         "low12_avg4": round(low12), "vs_low_pct": round((avg4[-1] / low12 - 1) * 100, 1)}
    u, sahm = data.get("UNRATE") or [], data.get("SAHMREALTIME") or []
    if u:
        out["unemployment"] = {"latest": u[-1][1], "date": u[-1][0].isoformat(),
                               "chg_3m": round(u[-1][1] - u[-4][1], 2) if len(u) >= 4 else None}
    if sahm:
        out["sahm"] = {"latest": sahm[-1][1], "date": sahm[-1][0].isoformat()}
    tp = data.get("THREEFYTP10") or []
    if len(tp) > 21:
        out["term_premium"] = {"latest": round(tp[-1][1], 3), "date": tp[-1][0].isoformat(),
                               "chg20_bp": round((tp[-1][1] - tp[-21][1]) * 100, 1)}
    res, rrp, iorb = data.get("WRESBAL") or [], data.get("RRPONTSYD") or [], data.get("IORB") or []
    if res:
        out["reserves_bn"] = {"latest": round(res[-1][1] / 1000), "date": res[-1][0].isoformat(),
                              "chg_4w_bn": round((res[-1][1] - res[-5][1]) / 1000) if len(res) >= 5 else None}
    if rrp:
        out["rrp_bn"] = {"latest": round(rrp[-1][1], 1), "date": rrp[-1][0].isoformat()}
    if iorb:
        out["iorb"] = {"latest": iorb[-1][1], "date": iorb[-1][0].isoformat()}
    two = data.get("DGS2") or []
    if len(two) > 6:
        out["dgs2"] = {"latest": two[-1][1], "chg_5d_bp": round((two[-1][1] - two[-6][1]) * 100, 1), "date": two[-1][0].isoformat()}
    curve = data.get("T10Y2Y") or []
    if len(curve) > 30:
        recent = curve[-21:]
        crossed = any(a[1] <= 0 < b[1] for a, b in zip(recent[:-1], recent[1:]))
        out["curve_2s10s"] = {"latest_bp": round(curve[-1][1] * 100), "min_24m_bp": round(min(v for _, v in curve) * 100),
                              "disinverted_recently": crossed, "date": curve[-1][0].isoformat()}
    for sid, key in (("BAMLH0A0HYM2", "hy"), ("BAMLC0A0CM", "ig")):
        s = data.get(sid) or []
        if len(s) > 250:
            vals = [v for _, v in s]
            out[key] = {"level_bp": round(vals[-1] * 100), "pctile_5y": _pctile(vals, vals[-1]),
                        "date": s[-1][0].isoformat(), "years": round(len(s) / 252, 1)}
    return out


_EBP = "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"


def parse_ebp(text: str) -> dict:
    """Gilchrist-Zakrajsek spread, excess bond premium and the Fed's EBP-implied 12-month recession probability."""
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        try:
            rows.append((r["date"], float(r["ebp"]), float(r["gz_spread"]), float(r["est_prob"])))
        except (KeyError, ValueError):
            continue
    if len(rows) < 4:
        raise ValueError("EBP file too short")
    (d1, e1, gz, p1), (_, e0, _, _), (_, e3, _, _) = rows[-1], rows[-2], rows[-4]
    return {"date": d1, "ebp_bp": round(e1 * 100), "chg_1m_bp": round((e1 - e0) * 100), "chg_3m_bp": round((e1 - e3) * 100),
            "gz_bp": round(gz * 100), "recession_prob_pct": round(p1 * 100, 1),
            "source_url": "https://www.federalreserve.gov/econres/notes/feds-notes/updating-the-recession-risk-and-the-excess-bond-premium-20161006.html"}


def fetch_macro(today: date, cache_dir: Path | None = None) -> dict:
    cache = Path(cache_dir) / "fred_macro.json" if cache_dir else None
    if cache and cache.is_file() and time.time() - cache.stat().st_mtime < _MAX_AGE:
        return json.loads(cache.read_text(encoding="utf-8"))
    data, failed = {}, []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {sid: pool.submit(_fetch, sid, years, today) for sid, (years, _) in SERIES.items()}
        for sid, job in jobs.items():
            try:
                data[sid] = job.result()
            except Exception:
                failed.append(SERIES[sid][1])
    if not data:
        raise OSError("FRED series unavailable")
    extra = {}
    try:
        extra["ebp"] = parse_ebp(get_bytes(_EBP, {"User-Agent": "Mozilla/5.0"}, limit=1_000_000).decode("utf-8-sig"))
    except Exception:
        failed.append("Excess bond premium")
    result = {**summarise(data), **extra, "state": "ok" if not failed else "partial", "unavailable": failed,
              "provider": "Federal Reserve Bank of St. Louis (FRED/ALFRED)", "source_url": "https://fred.stlouisfed.org/",
              "note": "Official series; claims are weekly, unemployment monthly, reserves weekly, the rest daily. "
                      "Each figure shows its own date."}
    if cache and not failed:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result), encoding="utf-8")
    return result
