"""Rule-based event detection: turn collected items into tagged Events."""
from __future__ import annotations

from ..collectors import edgar as edgar_c
from ..collectors.market import market_stats_text
from ..models import Event, RawItem, PriceMove

KEYWORD_EVENTS = {
    "M&A": [
        "merger", "acquisition", "acquire", "acquires", "acquired", "divestiture",
        "divest", "sale of", "sell its", "business combination", "tender offer",
        "definitive agreement", "combination", "spin-off", "spinoff", "carve-out",
    ],
    "earnings": [
        "results of operations", "financial results", "earnings release", "quarterly results",
        "fourth quarter", "q4", "annual results", "reports first quarter", "reports second quarter",
        "reports third quarter", "net sales", "revenue", "eps", "earnings per share",
    ],
    "guidance_change": [
        "guidance", "outlook", "forecast", "reaffirm", "raises", "lowers", "updates full-year",
        "updates fiscal", "sees",
    ],
    "financing": [
        "offering", "underwriting", "notes due", "senior notes", "credit agreement", "borrowing",
        "revolving credit", "shelf registration", "issuance of", "share repurchase", "buyback",
        "dividend", "tender offer", "debt", "refinancing",
    ],
    "management_change": [
        "departure of directors", "resignation", "resigns", "appointment of", "appoints",
        "chief executive officer", "chief financial officer", "ceo", "cfo", "board of directors",
        "executive", "succession", "named new",
    ],
    "rating_change": [
        "credit rating", "moody", "s&p", "standard & poor", "fitch", "rating", "downgrade", "upgrade",
    ],
    "activist": [
        "13d", "13g", "activist", "beneficial ownership", "stake", "nominate", "board seat",
    ],
}


def keyword_classify(text: str, default: str = "other") -> str:
    t = (text or "").lower()
    best = default
    best_score = 0
    for event, keywords in KEYWORD_EVENTS.items():
        score = sum(1 for k in keywords if k in t)
        if score > best_score:
            best = event
            best_score = score
    return best


def _detect_edgar(item: RawItem) -> Event:
    form = (item.form or "").upper()
    event_type = edgar_c.FORM_EVENT_MAP.get(form)
    text = ""
    if form == "8-K":
        # Peek at the 8-K item headers to classify the underlying event.
        text = edgar_c.fetch_text(
            item.extra.get("cik", ""), item.accession or "", item.published, max_chars=1500
        )
        item.extra["text"] = text
        event_type = keyword_classify(text, default="other")
    elif event_type is None:
        event_type = "other"

    confidence = 0.8 if event_type != "other" else 0.5
    return Event(item=item, event_type=event_type, confidence=confidence, source_text=text)


def _detect_rss(item: RawItem) -> Event:
    text = f"{item.title} {item.summary}"
    event_type = keyword_classify(text, default="other")
    return Event(item=item, event_type=event_type, confidence=0.6, source_text=text)


def _detect_market(move: PriceMove):
    if not move.large_move:
        return None
    text = market_stats_text(move)
    item = RawItem(
        source="market",
        company=move.company,
        ticker=move.ticker,
        role=move.role,
        title=f"{move.ticker} moved {move.pct_change:+.1f}% ({move.z_score:+.1f} sigma)",
        url=f"https://finance.yahoo.com/quote/{move.ticker}",
        published=move.date,
        summary=text,
        extra={"pct_change": move.pct_change, "z_score": move.z_score},
    )
    return Event(
        item=item,
        event_type="large_price_move",
        confidence=1.0,
        magnitude=abs(move.z_score),
        source_text=text,
    )


def detect_events(edgar_items: list, rss_items: list, price_moves: list) -> list:
    events: list[Event] = []
    for item in edgar_items:
        events.append(_detect_edgar(item))
    for item in rss_items:
        events.append(_detect_rss(item))
    for move in price_moves:
        ev = _detect_market(move)
        if ev is not None:
            events.append(ev)
    return events
