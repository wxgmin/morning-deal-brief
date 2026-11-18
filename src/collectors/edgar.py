"""SEC EDGAR filing collection via edgartools."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import requests

from ..models import RawItem, SOURCE_EDGAR

ALLOWED_FORMS = {"8-K", "10-Q", "10-K", "S-4", "SC 13D", "SC 13G", "DEF 14A", "425", "DEFM14A", "DEFA14A"}
FORM_EVENT_MAP = {
    "SC 13D": "activist",
    "SC 13G": "activist",
    "S-4": "M&A",
    "425": "M&A",
    "10-Q": "earnings",
    "10-K": "earnings",
    "DEF 14A": "management_change",
    "DEFM14A": "M&A",
}

_SEC_URL = "https://www.sec.gov/files/company_tickers.json"
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)
_TICKERS_CACHE = _CACHE_DIR / "company_tickers.json"

_HEADERS = {"User-Agent": "MorningDealBrief/1.0 (coverage research; contact: local@example.com)"}


def _ensure_identity() -> None:
    """Set the SEC-required User-Agent identity for edgartools (once per process)."""
    try:
        from edgar import set_identity

        set_identity(os.getenv("EDGAR_IDENTITY", "MorningDealBrief research local@example.com"))
    except Exception:
        pass


def _load_ticker_map() -> dict:
    if _TICKERS_CACHE.exists():
        try:
            return json.loads(_TICKERS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass

    resp = requests.get(_SEC_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _TICKERS_CACHE.write_text(json.dumps(data), encoding="utf-8")
    return data


def resolve_cik(ticker: str) -> str:
    """Return a zero-padded 10-digit CIK for a ticker, using the SEC map."""
    ticker = ticker.upper().strip()
    try:
        data = _load_ticker_map()
    except Exception:
        return ""
    for record in data.values():
        if record.get("ticker", "").upper() == ticker:
            return str(record.get("cik_str", "")).zfill(10)
    return ""


def _filing_attr(filing, name: str, default=""):
    for attr in (name, f"_{name}"):
        v = getattr(filing, attr, None)
        if v is not None and v != "":
            return v
    return default


def _filing_url(filing, cik: str, accession: str) -> str:
    url = _filing_attr(filing, "homepage_url")
    if url:
        return url
    url = _filing_attr(filing, "url")
    if url:
        return url
    if accession:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/index.json"
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40"


def collect_filings(companies: list, date_start: str, date_end: Optional[str] = None) -> list:
    """Collect allowed-form filings for each company within [date_start, date_end].

    date_start and date_end are YYYY-MM-DD. If date_end is None, the window is
    date_start only. Returns RawItem list (metadata only; text is fetched lazily).
    """
    items: list[RawItem] = []
    date_spec = date_start if date_end is None else f"{date_start}:{date_end}"

    try:
        from edgar import Company as EdgarCompany
    except Exception as exc:  # pragma: no cover - exercised only if dep missing
        raise RuntimeError("edgartools is not installed; run `pip install -r requirements.txt`") from exc

    _ensure_identity()

    for co in companies:
        cik = co.cik.strip() if co.cik else resolve_cik(co.ticker)
        if not cik:
            continue
        try:
            company = EdgarCompany(cik)
            filings = company.get_filings(date=date_spec)
        except Exception:
            continue

        for f in filings:
            form = str(_filing_attr(f, "form", "") or "").upper()
            if form not in ALLOWED_FORMS:
                continue
            accession = str(_filing_attr(f, "accession_no", "") or "")
            filing_date = str(_filing_attr(f, "filing_date", "") or date_start)
            url = _filing_url(f, cik, accession)
            items.append(
                RawItem(
                    source=SOURCE_EDGAR,
                    company=co.name,
                    ticker=co.ticker,
                    role=co.role,
                    title=f"{co.name} ({co.ticker}) filed {form}",
                    url=url,
                    published=filing_date[:10],
                    form=form,
                    accession=accession,
                    extra={"cik": cik},
                )
            )
    return items


def fetch_text(cik: str, accession: str, date: str, max_chars: int = 3000) -> str:
    """Fetch a bounded text excerpt for a specific filing (for grounding/judgment)."""
    if not cik or not accession:
        return ""
    try:
        from edgar import Company as EdgarCompany

        _ensure_identity()
        company = EdgarCompany(cik)
        filings = company.get_filings(date=date[:10])
        for f in filings:
            if str(_filing_attr(f, "accession_no", "") or "") == accession:
                text = f.text() or ""
                return (text or "")[:max_chars]
    except Exception:
        return ""
    return ""
