"""Market-wide deal flow from SEC EDGAR: merger agreements, IPO filings and pricings, tender offers, activists.

* Merger agreements: EDGAR full-text search for "Agreement and Plan of Merger" in 8-Ks reporting Item 1.01.
* Everything else: EDGAR "latest filings" Atom feeds by form type (S-1/F-1, 424B4, SC TO-T, SCHEDULE 13D,
  DEFM14A). Every row links to the filing index, so nothing here is a claim beyond "this was filed".
SEC asks automated clients to declare a contact in the User-Agent (EDGAR_IDENTITY) and stay under 10 req/s.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import urlencode

import feedparser

from ..guardrails import safe_http_url
from ..util import get_bytes
from .edgar import _identity

_EFTS = "https://efts.sec.gov/LATEST/search-index"
_ATOM = "https://www.sec.gov/cgi-bin/browse-edgar"
_TITLE = re.compile(r"^(?P<form>.+?) - (?P<name>.+?) \((?P<cik>\d{10})\) \((?P<role>[^)]+)\)$")
_TICKERS = re.compile(r"\(([A-Z0-9.\-, ]+)\)\s+\(CIK")
FEEDS = {
    "ipo_filings": {"label": "IPO registrations (S-1 / F-1)", "forms": ["S-1", "F-1"], "why":
                    "New registration statements are the public start of an IPO; amendments signal a deal moving toward launch."},
    "pricings": {"label": "Priced offerings (424B4)", "forms": ["424B4"], "why":
                 "A 424B4 prospectus is filed after an offering prices: the clearest public sign a deal got done."},
    "tender": {"label": "Tender offers (SC TO-T)", "forms": ["SC TO-T"], "why":
               "A third-party tender offer is a bid made directly to shareholders, often in an acquisition."},
    "activist": {"label": "Activist and 5%+ stakes (Schedule 13D)", "forms": ["SCHEDULE 13D"], "why":
                 "A 13D means an investor crossed 5% and may seek to influence the company."},
    "proxies": {"label": "Merger votes (DEFM14A)", "forms": ["DEFM14A"], "why":
                "A definitive merger proxy sets up the shareholder vote that closes an agreed deal."},
}


def _headers():
    return {"User-Agent": _identity(), "Accept-Encoding": "gzip, deflate"}


def _index_url(cik: str, adsh: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{adsh}-index.htm"


def parse_mna(payload: dict) -> list:
    rows, seen = [], set()
    for hit in (payload.get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        adsh, ciks = src.get("adsh"), src.get("ciks") or []
        if not adsh or adsh in seen or not ciks or "1.01" not in (src.get("items") or []):
            continue
        seen.add(adsh)
        display = (src.get("display_names") or [""])[0]
        tickers = _TICKERS.search(display)
        rows.append({"company": re.sub(r"\s+\(.*$", "", display).strip(), "cik": ciks[0],
                     "tickers": [t.strip() for t in tickers.group(1).split(",")] if tickers else [],
                     "date": src.get("file_date"), "form": src.get("form"), "items": src.get("items"),
                     "url": _index_url(ciks[0], adsh), "accession": adsh})
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows


def fetch_mna(today: date, days: int = 4) -> dict:
    params = {"q": '"Agreement and Plan of Merger"', "forms": "8-K", "dateRange": "custom",
              "startdt": (today - timedelta(days=days)).isoformat(), "enddt": today.isoformat()}
    url = _EFTS + "?" + urlencode(params)
    payload = json.loads(get_bytes(url, _headers()))
    return {"items": parse_mna(payload), "total_hits": ((payload.get("hits") or {}).get("total") or {}).get("value"),
            "source_url": "https://efts.sec.gov/LATEST/search-index?" + urlencode(params)}


def parse_atom(body: bytes, kind: str) -> list:
    rows, seen = [], set()
    for entry in feedparser.parse(body).entries:
        m = _TITLE.match((entry.get("title") or "").strip())
        link = safe_http_url(entry.get("link") or "")
        acc = (entry.get("id") or "").rsplit("=", 1)[-1]
        if not m or not link or acc in seen:
            continue
        role = m.group("role")
        # For stake and tender filings the company that matters is the target ("Subject").
        if kind in {"activist", "tender"} and role != "Subject":
            continue
        if kind == "insiders" and role != "Issuer":
            continue
        seen.add(acc)
        rows.append({"company": m.group("name").strip(), "cik": m.group("cik"), "form": m.group("form"),
                     "amendment": m.group("form").endswith("/A"), "date": (entry.get("updated") or "")[:10],
                     "url": link, "accession": acc})
    return rows


def fetch_feed(kind: str) -> list:
    rows = []
    for form in FEEDS[kind]["forms"]:
        url = _ATOM + "?" + urlencode({"action": "getcurrent", "type": form, "company": "", "dateb": "",
                                       "owner": "include", "start": 0, "count": 40, "output": "atom"})
        rows += parse_atom(get_bytes(url, _headers(), limit=1_500_000), kind)
    rows.sort(key=lambda r: (r["date"], not r["amendment"]), reverse=True)  # newest first, originals before /A
    return rows


def parse_form4(xml: bytes) -> list:
    """Open-market purchases (P) and sales (S) only; grants, exercises and gifts are not market signals."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    owner = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    title = ""
    if rel is not None:
        title = (rel.findtext("officerTitle") or "").strip() or ("Director" if (rel.findtext("isDirector") or "").strip() in {"1", "true"} else
                                                                ("10% owner" if (rel.findtext("isTenPercentOwner") or "").strip() in {"1", "true"} else ""))
    rows = []
    for t in root.findall(".//nonDerivativeTransaction"):
        code = (t.findtext("transactionCoding/transactionCode") or "").strip()
        if code not in {"P", "S"}:
            continue
        try:
            shares = float(t.findtext("transactionAmounts/transactionShares/value") or 0)
            price = float(t.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        rows.append({"insider": owner, "title": title, "side": "Buy" if code == "P" else "Sell", "shares": shares,
                     "price": price, "value": round(shares * price), "date": (t.findtext("transactionDate/value") or "").strip()})
    return rows


def fetch_insiders(watch_ciks: set, watch_names: set, sample: int = 14) -> list:
    url = _ATOM + "?" + urlencode({"action": "getcurrent", "type": "4", "company": "", "dateb": "", "owner": "include",
                                   "start": 0, "count": 100, "output": "atom"})
    issuers = [r for r in parse_atom(get_bytes(url, _headers(), limit=2_000_000), "insiders")]
    def is_watch(r):
        return r["cik"].lstrip("0") in watch_ciks or re.sub(r"[^a-z0-9]", "", r["company"].lower())[:12] in watch_names
    picked = [r for r in issuers if is_watch(r)] + [r for r in issuers if not is_watch(r)][:sample]
    out = []
    for r in picked:
        base = r["url"].rsplit("/", 1)[0]
        try:
            files = json.loads(get_bytes(base + "/index.json", _headers()))["directory"]["item"]
            xml_name = next(f["name"] for f in files if f["name"].endswith(".xml"))
            trades = parse_form4(get_bytes(base + "/" + xml_name, _headers(), limit=1_000_000))
        except Exception:
            continue
        if not trades:
            continue
        buys = sum(t["value"] for t in trades if t["side"] == "Buy")
        sells = sum(t["value"] for t in trades if t["side"] == "Sell")
        out.append({**r, "insider": trades[0]["insider"], "title": trades[0]["title"],
                    "side": "Buy" if buys >= sells else "Sell", "value": max(buys, sells),
                    "shares": sum(t["shares"] for t in trades if t["side"] == ("Buy" if buys >= sells else "Sell")),
                    "trade_date": trades[0]["date"], "form": "4"})
    return rollup_insiders(out)


def rollup_insiders(rows: list) -> list:
    """One row per company and side. Three or more insiders buying is flagged as a cluster buy."""
    grouped = {}
    for r in rows:
        key = (r["cik"], r["side"])
        g = grouped.setdefault(key, {**r, "value": 0, "shares": 0, "insiders": []})
        g["value"] += r["value"]
        g["shares"] += r["shares"]
        if r["insider"] not in g["insiders"]:
            g["insiders"].append(r["insider"])
        g["date"] = max(g["date"], r["date"])
    out = []
    for g in grouped.values():
        n = len(g["insiders"])
        g["cluster"] = g["side"] == "Buy" and n >= 3
        g["insider"] = g["insider"] if n == 1 else f"{n} insiders incl. {g['insiders'][0]}"
        g["title"] = g["title"] if n == 1 else ""
        out.append(g)
    # Purchases first, clusters first: insiders sell for many reasons, but buy with their own money for one.
    out.sort(key=lambda r: (r["side"] != "Buy", not r["cluster"], -r["value"]))
    return out


def collect(today: date, watch: list) -> dict:
    """Run all deal-flow queries; each group reports its own state. `watch` is the profile watchlist."""
    names = {re.sub(r"[^a-z0-9]", "", w["name"].lower())[:12] for w in watch if w.get("name")}
    ciks = {str(w.get("cik") or "").lstrip("0") for w in watch if w.get("cik")}
    tickers = {w["ticker"] for w in watch}

    def mark(rows):
        for r in rows:
            key = re.sub(r"[^a-z0-9]", "", r["company"].lower())[:12]
            r["watch"] = (r["cik"].lstrip("0") in ciks or key in names
                          or bool(set(r.get("tickers") or []) & tickers))
        return rows

    groups = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {"mna": pool.submit(fetch_mna, today)}
        jobs.update({kind: pool.submit(fetch_feed, kind) for kind in FEEDS})
        jobs["insiders"] = pool.submit(fetch_insiders, ciks, names)
        for kind, job in jobs.items():
            try:
                value = job.result()
                rows = value["items"] if kind == "mna" else value
                groups[kind] = {"state": "ok", "items": mark(rows)[:15], "count": len(rows)}
                if kind == "mna":
                    groups[kind]["source_url"] = value["source_url"]
            except Exception as exc:
                groups[kind] = {"state": "unavailable", "items": [], "error": type(exc).__name__}
    groups["mna"].update(label="Merger agreements (8-K Item 1.01)", why=(
        "Item 1.01 reports a material definitive agreement; with merger language it is the first filing of a signed deal."))
    for kind, meta in FEEDS.items():
        groups[kind].update(label=meta["label"], why=meta["why"])
    groups["insiders"].update(label="Insider open-market trades (Form 4)", why=(
        "Executives sell for many reasons but buy with their own money for one: open-market purchases, especially "
        "clusters, are the insider signal worth noticing. Grants and option exercises are excluded."))
    states = [g["state"] for g in groups.values()]
    return {"state": "ok" if all(s == "ok" for s in states) else "unavailable" if all(s == "unavailable" for s in states)
            else "partial", "groups": groups, "provider": "SEC EDGAR",
            "source_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent",
            "watch_hits": sum(1 for g in groups.values() for r in g["items"] if r.get("watch"))}
