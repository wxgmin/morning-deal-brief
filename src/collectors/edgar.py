"""SEC submissions and primary filing documents."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, timedelta
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..archive import _atomic_write
from ..models import RawItem, SOURCE_EDGAR
from ..paths import CACHE_DIR as _CACHE_DIR
from ..util import get_bytes

logger = logging.getLogger(__name__)

_BASE_FORMS = {"8-K", "10-Q", "10-K", "S-4", "F-4", "6-K", "20-F", "40-F", "SC 13D",
               "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G", "DEF 14A", "425", "DEFM14A", "DEFA14A"}
ALLOWED_FORMS = _BASE_FORMS | {f"{form}/A" for form in _BASE_FORMS}
FORM_EVENT_MAP = {
    "SC 13D": "ownership", "SC 13G": "ownership",
    "SCHEDULE 13D": "ownership", "SCHEDULE 13G": "ownership",
    "S-4": "M&A", "F-4": "M&A", "425": "M&A", "DEFM14A": "M&A",
    "10-Q": "earnings", "10-K": "earnings", "20-F": "earnings", "40-F": "earnings",
}

_SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/"
_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/"
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_DOCUMENT = re.compile(r"[\w.-]+(?:/[\w.-]+)*")
_last_request = 0.0


def _identity() -> str:
    return os.getenv("EDGAR_IDENTITY") or "MorningDealBrief research local@example.com"


def _sec_bytes(url, limit=8_000_000):
    def pace():
        global _last_request
        time.sleep(max(0, _last_request + 0.22 - time.monotonic()))
        _last_request = time.monotonic()
    return get_bytes(url, {"User-Agent": _identity()}, limit=limit, attempts=2, before_request=pace)


def _load_ticker_map() -> dict:
    path = _CACHE_DIR / "company_tickers.json"
    previous = {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous and time.time() - path.stat().st_mtime < 86400:
            return previous
    except (OSError, ValueError):
        pass
    try:
        data = json.loads(_sec_bytes(_SEC_TICKERS))
    except Exception:
        if previous:
            logger.warning("Using saved SEC ticker map")
            return previous
        raise
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(data))
    except OSError:
        logger.warning("Could not cache SEC ticker map")
    return data


def resolve_cik(ticker: str, ticker_map=None) -> str:
    ticker = ticker.upper().strip()
    try:
        data = _load_ticker_map() if ticker_map is None else ticker_map
        return next((str(row["cik_str"]).zfill(10) for row in data.values()
                     if row.get("ticker", "").upper() == ticker), "")
    except Exception as exc:
        logger.warning("SEC ticker map unavailable (%s)", type(exc).__name__)
        return ""


def _doc_url(cik, accession, document=""):
    folder = f"{_ARCHIVES}{int(cik)}/{accession.replace('-', '')}/"
    if _DOCUMENT.fullmatch(document) and not any(p in {".", ".."} for p in document.split("/")):
        return folder + quote(document, safe="/")
    return folder + accession + "-index.html"


def _filings(co, cik, rows, start, end):
    forms = rows["form"]
    if not all(isinstance(values, list) for values in (forms, rows["filingDate"], rows["accessionNumber"])):
        raise ValueError("invalid SEC submissions columns")

    def optional(key, index):
        values = rows.get(key)
        if isinstance(values, list) and index < len(values) and isinstance(values[index], str):
            return values[index]
        return ""

    result, malformed = [], 0
    for i, form in enumerate(forms):
        if not isinstance(form, str):
            malformed += 1
            continue
        if form not in ALLOWED_FORMS:
            continue
        try:
            filed = date.fromisoformat(rows["filingDate"][i]).isoformat()
            if not start <= filed <= end:
                continue
            acc = rows["accessionNumber"][i]
            if not isinstance(acc, str) or not _ACCESSION.fullmatch(acc):
                raise ValueError("invalid accession")
        except (IndexError, TypeError, ValueError):
            malformed += 1
            continue
        document = optional("primaryDocument", i)
        result.append(RawItem(
            SOURCE_EDGAR, co.name, co.ticker, co.role,
            f"{co.name} filed Form {form}.", _doc_url(cik, acc, document), filed,
            form=form, accession=acc,
            extra={"cik": cik, "accepted_at": optional("acceptanceDateTime", i), "document": document,
                   "items": optional("items", i), "index_url": _doc_url(cik, acc)},
        ))
    if malformed:
        logger.warning("SEC submissions for %s skipped %d malformed record(s)", co.ticker, malformed)
    return result, malformed


def collect_filings(companies: list, date_start: str, date_end: str = None, *, status=None) -> list:
    """Collect original SEC filing metadata, retaining acceptance timestamps."""
    end = date_end or date_start
    # EDGAR can give an accepted filing the following business day's filing date.
    search_end = (date.fromisoformat(end) + timedelta(days=7)).isoformat()
    ticker_map = {}
    if any(not company.cik for company in companies):
        try:
            ticker_map = _load_ticker_map()
        except Exception as exc:
            logger.warning("SEC ticker map unavailable (%s)", type(exc).__name__)
    output = []
    for company in companies:
        rows = []
        state = "unavailable"
        try:
            cik = company.cik or resolve_cik(company.ticker, ticker_map)
            if not cik.isdigit() or not 0 < int(cik) <= 9_999_999_999:
                raise ValueError("CIK unavailable")
            data = json.loads(_sec_bytes(f"{_SUBMISSIONS}CIK{cik.zfill(10)}.json"))
            filings = data["filings"]
            rows, malformed = _filings(company, cik, filings["recent"], date_start, search_end)
            state = "partial" if malformed else "ok"
            for file in filings.get("files", []):
                try:
                    if file["filingTo"] < date_start or file["filingFrom"] > search_end:
                        continue
                    name = file["name"]
                    if not re.fullmatch(rf"CIK{cik.zfill(10)}-submissions-\d+\.json", name):
                        raise ValueError("invalid SEC history name")
                    data = json.loads(_sec_bytes(_SUBMISSIONS + name))
                    historical, malformed = _filings(company, cik, data, date_start, search_end)
                    rows.extend(historical)
                    if malformed:
                        state = "partial"
                except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                    state = "partial"
                    logger.warning("SEC filing history incomplete for %s (%s)", company.ticker, type(exc).__name__)
        except Exception as exc:
            state = "partial" if rows else "unavailable"
            logger.warning("SEC submissions unavailable for %s (%s)", company.ticker, type(exc).__name__)
        unique_rows = {}
        for row in rows:
            unique_rows.setdefault(row.accession, row)
        output.extend(unique_rows.values())
        if status is not None:
            status[company.ticker] = state
    if status is None and date_end is None:
        return [item for item in output if item.published == date_start]
    if status is None:
        return [item for item in output if date_start <= item.published <= end]
    return output


def fetch_text(cik: str, accession: str, date: str, max_chars: int = 45_000, *, document="",
               exhibits=None) -> str:
    """Fetch and normalize the primary filing in source order, preserving paragraphs."""
    if not cik.isdigit() or not _ACCESSION.fullmatch(accession) or not document:
        return ""
    try:
        raw = _sec_bytes(_doc_url(cik, accession, document), limit=15_000_000)
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup.find_all(["script", "style", "head", "ix:header", "ix:hidden"]):
            tag.decompose()
        if exhibits is not None:
            for link in soup.find_all("a", href=True):
                label = link.get_text(" ", strip=True)
                if not re.fullmatch(r"(?:Exhibit\s+)?99\.1", label, re.I):
                    continue
                filename = link["href"]
                if re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", filename, re.I):
                    parent = document.rpartition("/")[0]
                    exhibits.setdefault("99.1", f"{parent}/{filename}" if parent else filename)
        blocks = soup.find_all(["p", "div", "h1", "h2", "h3", "td"])
        lines = [" ".join(tag.stripped_strings) for tag in blocks
                 if not tag.find(["p", "div", "h1", "h2", "h3", "td"])]
        if not lines:
            lines = list(soup.stripped_strings)
        result, size = [], 0
        for line in lines:
            line = " ".join(line.split())
            size += len(line) + 1
            if size > max_chars:
                break
            if line:
                result.append(line)
        return "\n".join(result)
    except Exception as exc:
        logger.warning("Filing document unavailable for accession %s (%s)", accession, type(exc).__name__)
        return ""
