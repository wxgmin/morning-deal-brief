"""Press-release and news RSS collection via feedparser."""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

import feedparser
import requests

from ..models import RawItem, SOURCE_RSS

logger = logging.getLogger(__name__)

_USER_AGENT = "MorningDealBrief/1.0"


def collect_rss(feeds: list, coverage, since: str = None) -> list:
    """Collect and normalize RSS entries from the configured feeds.

    Entries are attributed to the coverage company actually named in the headline,
    else tagged as sector-level news. A ``since`` date (YYYY-MM-DD) optionally drops
    older entries; the default keeps the last 7 days.
    """
    if since is None:
        since = (date.today() - timedelta(days=7)).isoformat()

    items: list[RawItem] = []
    for feed in feeds:
        url = feed.get("url", "")
        if not url:
            continue
        try:
            resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        except Exception as exc:
            logger.warning("RSS feed failed (%s): %s", url, exc)
            continue

        for entry in parsed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue
            summary = entry.get("summary", "").strip()
            link = entry.get("link", "")
            published_iso = _to_iso(entry)
            if published_iso and published_iso < since:
                continue

            company = _attribute_company(title, url, coverage)
            items.append(
                RawItem(
                    source=SOURCE_RSS,
                    company=company.name,
                    ticker=company.ticker,
                    role=company.role,
                    title=title,
                    url=link,
                    published=published_iso,
                    summary=summary or title,
                    extra={"feed": url},
                )
            )
    return items


def _attribute_company(text: str, feed_url: str, coverage):
    """Attribute an RSS item to the coverage company actually named in the headline."""
    from ..models import Company

    t = (text or "").upper()

    # 1. Full company name as a whole phrase.
    for co in coverage.companies:
        name = (co.name or "").upper()
        if name and re.search(rf"(?<![A-Z0-9]){re.escape(name)}(?![A-Z0-9])", t):
            return co

    # 2. Ticker as a whole word (skip short/ambiguous tickers).
    for co in coverage.companies:
        ticker = (co.ticker or "").upper()
        if len(ticker) >= 3 and ticker and re.search(rf"\b{re.escape(ticker)}\b", t):
            return co

    # 3. Short name (first token) as a whole word.
    for co in coverage.companies:
        parts = (co.name or "").strip().split()
        short = parts[0].upper() if parts else ""
        if len(short) >= 3 and re.search(rf"\b{re.escape(short)}\b", t):
            return co

    return Company(name="Sector", ticker="", role="sector")


def _to_iso(entry) -> str:
    """Normalize a feed entry's date to YYYY-MM-DD (handles RFC-2822 and ISO-8601)."""
    from datetime import datetime

    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6]).date().isoformat()
            except Exception:
                pass

    published = entry.get("published", "") or entry.get("updated", "")
    if not published:
        return ""
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(published).date().isoformat()
    except Exception:
        return ""
