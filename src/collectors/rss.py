"""Bounded RSS retrieval with explicit timestamps and issuer attribution."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import feedparser

from ..guardrails import safe_http_url
from ..models import RawItem, SOURCE_RSS
from ..util import get_bytes

logger = logging.getLogger(__name__)


def _clean(value):
    return " ".join(BeautifulSoup(value, "html.parser").stripped_strings)


def _published(entry):
    value = entry.get("published_parsed") or entry.get("created_parsed")
    if value:
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None


def _attribute_company(text, coverage):
    selected = []
    for co in coverage.companies:
        name = re.sub(r"(?i)\s+(?:incorporated|inc|corporation|corp|limited|ltd|plc|holdings)\.?$", "", co.name)
        ticker_pattern = rf"(?:\${re.escape(co.ticker)}\b|\b(?:NYSE|NASDAQ):\s*{re.escape(co.ticker)}\b)"
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) or re.search(ticker_pattern, text):
            selected.append(co)
    return selected


def collect_rss(feeds: list, coverage, since: str = None, *, status=None) -> list:
    since = since or (date.today() - timedelta(days=7)).isoformat()

    def get_feed(feed):
        url = feed.get("url", "")
        try:
            if not safe_http_url(url) or not url.startswith("https://"):
                raise ValueError("feed URL must be HTTPS")
            parsed = feedparser.parse(get_bytes(url, {"User-Agent": "MorningDealBrief/2.0"}, limit=2_000_000))
            if not parsed.version or (parsed.bozo and not parsed.entries):
                raise ValueError("invalid feed")
            result = []
            state = "ok"
            for entry in parsed.entries[:150]:
                published = _published(entry)
                if not published:
                    state = "partial"
                    continue
                if published.date().isoformat() < since:
                    continue
                link = safe_http_url(urljoin(url, entry.get("link") or "")) if entry.get("link") else ""
                title = _clean(entry.get("title") or "")
                if not title or not link:
                    state = "partial"
                    continue
                publisher = _clean(entry.get("source", {}).get("title") or parsed.feed.get("title") or "")
                if feed.get("type") == "press" and feed.get("ticker"):
                    co = coverage.company_for_ticker(feed["ticker"])
                    companies = [co] if co else []
                else:
                    companies = _attribute_company(title, coverage)
                if not companies and feed.get("type") == "sector":
                    from ..models import Company
                    companies = [Company(coverage.sector, "", role="sector")]
                if not companies:
                    continue
                summary = _clean(entry.get("summary") or "")[:1200]
                result.extend(
                    RawItem(SOURCE_RSS, co.name, co.ticker, co.role, title, link,
                            published.date().isoformat(), summary,
                            extra={"published_at": published.isoformat(), "feed": url, "publisher": publisher,
                                   "publisher_type": feed.get("type", "news")})
                    for co in companies
                )
            return result, "partial" if len(parsed.entries) > 150 else state
        except Exception as exc:
            logger.warning("RSS feed unavailable (%s)", type(exc).__name__)
            return [], "unavailable"

    output = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        for feed, (items, state) in zip(feeds, executor.map(get_feed, feeds)):
            output.extend(items)
            if status is not None:
                status[feed["url"]] = state
    return output
