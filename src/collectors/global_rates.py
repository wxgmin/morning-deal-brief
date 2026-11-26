"""Global 10-year benchmarks (official, end of day) and the crude-oil futures curve.

* Japan 10Y: Ministry of Finance JGB curve CSV (current month).
* Euro area 10Y: ECB AAA government spot curve (the standard keyless Bund proxy).
* UK 10Y: Bank of England nominal zero-coupon gilt curve (IUDMNZC).
* Oil curve: WTI front, second and twelfth contracts from Yahoo. Backwardation (front above later months)
  usually means tight near-term supply; contango means ample supply.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

from ..util import get_bytes

_UA = {"User-Agent": "Mozilla/5.0 (MorningBrief)"}
_MOF = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
_ECB = "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
_BOE = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
_MONTHS = "FGHJKMNQUVXZ"
_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _row(label, series, source, url):
    series = sorted(series)
    if len(series) < 2:
        raise ValueError(f"{label}: not enough observations")
    (d1, v1), (d0, v0) = series[-1], series[-2]
    return {"label": label, "yield": round(v1, 3), "chg_bp": round((v1 - v0) * 100, 1), "as_of": d1.isoformat(),
            "provider": source, "source_url": url}


def parse_mof(text: str) -> dict:
    series = []
    reader = csv.reader(io.StringIO(text))
    header = None
    for row in reader:
        if row and row[0] == "Date":
            header = row
            continue
        if header and row and re.match(r"\d{4}/\d{1,2}/\d{1,2}$", row[0]):
            try:
                series.append((datetime.strptime(row[0], "%Y/%m/%d").date(), float(row[header.index("10Y")])))
            except ValueError:
                continue
    return _row("Japan 10Y (JGB)", series, "Japan Ministry of Finance", "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/index.htm")


def parse_ecb(text: str) -> dict:
    series = [(date.fromisoformat(r["TIME_PERIOD"]), float(r["OBS_VALUE"])) for r in csv.DictReader(io.StringIO(text))
              if r.get("OBS_VALUE")]
    return _row("Euro area 10Y (AAA curve, Bund proxy)", series, "European Central Bank",
                "https://data.ecb.europa.eu/data/datasets/YC")


def parse_boe(text: str) -> dict:
    series = []
    for r in csv.reader(io.StringIO(text)):
        if len(r) >= 2:
            try:
                series.append((datetime.strptime(r[0].strip(), "%d %b %Y").date(), float(r[1])))
            except ValueError:
                continue
    return _row("UK 10Y (gilt, zero-coupon)", series, "Bank of England", "https://www.bankofengland.co.uk/statistics/yield-curves")


def fetch_global_yields(today: date) -> dict:
    rows, failed = [], []
    boe_q = urlencode({"csv.x": "yes", "Datefrom": (today - timedelta(days=14)).strftime("%d/%b/%Y"), "Dateto": "now",
                       "SeriesCodes": "IUDMNZC", "CSVF": "TN", "UsingCodes": "Y"})
    for label, url, parse in (("Japan", _MOF, parse_mof),
                              ("Euro area", _ECB + "?lastNObservations=3&format=csvdata", parse_ecb),
                              ("UK", _BOE + "?" + boe_q, parse_boe)):
        try:
            rows.append(parse(get_bytes(url, _UA, limit=500_000).decode("utf-8-sig", errors="replace")))
        except Exception:
            failed.append(label)
    if not rows:
        raise OSError("global yields unavailable")
    return {"state": "ok" if not failed else "partial", "rows": rows, "unavailable": failed,
            "note": "Official end-of-day benchmarks; each source publishes on its own schedule, so dates can differ."}


def _contract(symbol_root, exchange, month, year):
    return f"{symbol_root}{_MONTHS[month - 1]}{year % 100:02d}.{exchange}"


def fetch_oil_curve() -> dict:
    """WTI M1, M2, M12 from the front-contract name Yahoo reports for CL=F (e.g. 'Crude Oil Jan 27')."""
    meta = json.loads(get_bytes(_YAHOO + quote("CL=F", safe="") + "?range=1d&interval=1d", _UA))["chart"]["result"][0]["meta"]
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{2})", meta.get("shortName") or "")
    if not m:
        raise ValueError("front contract month unknown")
    month, year = _NAMES.index(m.group(1)) + 1, 2000 + int(m.group(2))
    legs = []
    for step in (0, 1, 11):
        mm, yy = (month - 1 + step) % 12 + 1, year + (month - 1 + step) // 12
        sym = _contract("CL", "NYM", mm, yy)
        price = json.loads(get_bytes(_YAHOO + quote(sym, safe="") + "?range=1d&interval=1d", _UA))["chart"]["result"][0]["meta"]["regularMarketPrice"]
        legs.append({"symbol": sym, "label": f"{_NAMES[mm - 1]} {yy}", "price": round(float(price), 2)})
    m1_m2, m1_m12 = round(legs[0]["price"] - legs[1]["price"], 2), round(legs[0]["price"] - legs[2]["price"], 2)
    shape = "backwardation" if m1_m12 > 0 else "contango" if m1_m12 < 0 else "flat"
    return {"state": "ok", "legs": legs, "m1_m2": m1_m2, "m1_m12": m1_m12, "shape": shape,
            "provider": "Yahoo Finance (NYMEX WTI futures, delayed)", "source_url": "https://finance.yahoo.com/quote/CL%3DF/futures/",
            "note": "Front minus later contracts. Positive = backwardation (prompt barrels priced above deferred ones), "
                    "which has typically reflected tight near-term supply; negative = contango (ample supply, storage pays)."}
