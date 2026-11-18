"""Press-release and news RSS collection via feedparser."""
from __future__ import annotations

import re

import feedparser

from ..models import RawItem, SOURCE_RSS


def collect_rss(feeds: list, coverage) -> list:
    """Collect and normalize RSS entries from the configured feeds.

    Each feed is expected to be a dict with at least a "url". Entries are
    attributed to a coverage company by matching ticker/name in the headline or
    feed URL, else tagged as sector-level news.
    """
    items: list[RawItem] = []
    for feed in feeds:
        url = feed.get("url", "")
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
        except Exception:
            continue

        for entry in parsed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue
            summary = entry.get("summary", "").strip()
            link = entry.get("link", "")
            published = entry.get("published", "") or entry.get("updated", "")
            published_iso = _to_iso(published)

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
    """Attribute an RSS item to the coverage company actually named in the headline.

    Falls back to the feed's own ticker only when the headline names it; otherwise
    the item is sector-level news. This avoids mis-attributing a peer/third-party
    headline to the client just because it arrived via the client's feed.
    """
    from ..models import Company

    t = (text or "").upper()
    # 1. Ticker as a whole word.
    for co in coverage.companies:
        ticker = co.ticker.upper()
        if ticker and re.search(rf"\b{re.escape(ticker)}\b", t):
            return co
    # 2. Full company name.
    for co in coverage.companies:
        if co.name.upper() in t:
            return co
    # 3. Short name (first token of the company name) as a whole word.
    for co in coverage.companies:
        short = co.name.strip().split()[0].upper()
        if short and re.search(rf"\b{re.escape(short)}\b", t):
            return co
    return Company(name="Sector", ticker="", role="sector")


def _to_iso(published: str) -> str:
    """Best-effort normalize a feed date to YYYY-MM-DD."""
    if not published:
        return ""
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(published).date().isoformat()
    except Exception:
        return published[:10]
