"""Conservative story identity with same-issuer and date guards."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .guardrails import safe_http_url

_SUFFIX = re.compile(r"[, ]+\b(?:inc|incorporated|corp|corporation|ltd|limited|llc|plc)\.?$")
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid"}


def _url_key(value):
    if not safe_http_url(value):
        return ""
    url = urlsplit(value)
    query = urlencode([(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                       if k.lower() not in _TRACKING])
    return urlunsplit(("", url.netloc.lower(), url.path or "/", query, ""))


def _headline(value):
    return _SUFFIX.sub("", " ".join(value.casefold().split()).rstrip("."))


def _same_story(a, b):
    x, y = a.item, b.item
    if (x.ticker or x.company).casefold() != (y.ticker or y.company).casefold():
        return False
    if x.source == "edgar" and y.source == "edgar":
        return bool(x.accession and x.accession == y.accession and a.event_type == b.event_type
                    and x.extra.get("exhibit") == y.extra.get("exhibit"))
    if x.source == "market" or y.source == "market":
        return x.source == y.source and x.published == y.published
    if x.source != "rss" or y.source != "rss" or a.event_type != b.event_type:
        return False
    # A story URL can carry revised terms or a new headline as a deal progresses.
    if _headline(x.title) != _headline(y.title):
        return False
    key = _url_key(x.url)
    return x.published == y.published or bool(key and key == _url_key(y.url))


def _quality(event):
    return (event.item.source == "edgar", event.item.extra.get("publisher_type") == "press",
            len(event.source_text or ""), len(event.item.summary or ""))


def dedupe(events: list, threshold: int = 82) -> list:
    """Keep the best evidence for each high-confidence match and retain linked sources."""
    kept = []
    for event in sorted(events, key=_quality, reverse=True):
        match = next((k for k in kept if _same_story(event, k)), None)
        if match is None:
            kept.append(event)
        else:
            links = [event.item.url, *event.also_reported_by]
            match.also_reported_by.extend(
                dict.fromkeys(url for url in links if url and url != match.item.url and url not in match.also_reported_by)
            )
    return kept
