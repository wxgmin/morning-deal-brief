"""Source-aware event detection and evidence selection."""
from __future__ import annotations

import re
from dataclasses import replace

from ..collectors import edgar
from ..collectors.market import market_stats_text
from ..guardrails import MAX_EXCERPT, extract_figures
from ..models import Event, PriceMove, RawItem

_SIGNALS = {
    "M&A": r"\b(?:merger agreement|merger|acquisitions?|acquires?|acquired|buyout|divestitures?|business combination|spin[- ]off|carve[- ]out)\b",
    "financing": (
        r"\b(?:credit agreement|note purchase agreement|senior (?:unsecured |secured )?notes|notes due|"
        r"refinancing|refinance|buyback|share repurchase|stock repurchase|revolving credit|debt offering|"
        r"dividend|notice of (?:default|acceleration)|breach of (?:a |the )?financial covenant|"
        r"fail(?:ed|ure) to make (?:an? |the )?(?:interest|principal) payment)\b"
    ),
    "guidance_change": r"\b(?:raises?|raised|lowers?|lowered|cuts?|cut|withdraws?|reaffirms?|updates?)\b.{0,65}\b(?:guidance|outlook|forecast)\b",
    "earnings": r"\b(?:results of operations|financial results|quarter(?:ly)? results|earnings release|earnings per share|reports? (?:first|second|third|fourth)[ -]quarter)\b",
    "management_change": (
        r"\b(?:appoints?|appointed|resigns?|resigned|retires?|retired|departure|succession)\b"
        r".{0,90}\b(?:directors?|chief|CEO|CFO|chair|officer)\b"
        r"|\b(?:CEO|CFO|chief executive officer)\b.{0,50}\b(?:resigns?|resigned|retires?|retired|steps down|departure)\b"
        r"|\b(?:provided notification|gave notice|notified)\b.{0,90}\b(?:retirement|resignation)\b"
    ),
    "rating_change": (
        r"\b(?:ratings? (?:downgrad\w+|upgrad\w+)|(?:downgrad\w+|upgrad\w+)\b.{0,55}\b(?:credit |issuer )?ratings?)\b"
        r"|\b(?:Fitch|Moody.s|S&P(?: Global Ratings)?)\s+(?:downgrad\w+|upgrad\w+|"
        r"(?:raises?|lowers?|cuts?)\b.{0,65}\b(?:ratings?|to ['\"]?(?-i:[A-D][A-Za-z]{0,2}[1-3]?)[+-]?(?![\w+-])))"
        r"|\b(?:revises?|changes?)\b.{0,70}\bratings? outlook\b"
        r"|\b(?:Fitch|Moody.s|S&P(?: Global Ratings)?)\b.{0,85}\boutlook to (?:positive|negative|stable)\b"
    ),
    "bankruptcy": r"\b(?:file[ds]?|filed for)\b.{0,85}\b(?:Chapter (?:7|11)|bankruptcy protection)\b",
    "impairment": r"\b(?:(?:material|goodwill) impairment|impairment charges?|charge for impairment)\b",
    "financial_reporting": (
        r"\b(?:financial statements|financial results)\b.{0,90}\b(?:should|can) no longer be relied upon\b"
        r"|\b(?:will restate|non-reliance on)\b.{0,70}\bfinancial statements\b"
    ),
    "activist": r"\b(?:activist investor|activist stake|proxy fight|proxy contest|nominat\w+ (?:a |new )?(?:director|board slate))\b",
}
_SIGNALS = {event: re.compile(pattern, re.I) for event, pattern in _SIGNALS.items()}
_ITEM = re.compile(r"(?im)^\s*item\s*\n?\s*(\d\.\d\d)\.?\s*")
_ITEM_TYPES = {"1.03": "bankruptcy", "2.01": "M&A", "2.02": "earnings", "2.03": "financing",
               "2.04": "financing", "2.06": "impairment", "3.02": "financing",
               "4.02": "financial_reporting", "5.02": "management_change"}
_PERSONNEL_HEADING = re.compile(r"^departure of directors or certain officers\b", re.I)
_CONTEXT_HEADINGS = {
    "1.03": r"bankruptcy or receivership",
    "2.04": r"triggering events that accelerate or increase a direct financial obligation or an obligation "
            r"under an off[ -]balance sheet arrangement",
    "2.06": r"material impairments",
    "4.02": r"non[- ]reliance on previously issued financial statements or a related audit report "
            r"or completed interim review",
}
_SECTION_END = re.compile(
    r"(?im)^\s*(?:signatures?|(?:cautionary (?:note|statement) (?:on|regarding) )?"
    r"forward[- ]looking statements)\.?\s*$"
)
_EARNINGS_EXHIBIT = re.compile(r"\bexhibit\s+99\.1\b", re.I)
_EARNINGS_METRIC = re.compile(
    r"\b(?:revenue|net sales|net income|operating income|earnings per share|ebitda|free cash flow)\b", re.I,
)
_BUYBACK_AUTHORIZATION = re.compile(
    r"\b(?:authoriz(?:es?|ed)|approv(?:es?|ed)|increas(?:es?|ed))\b"
    r".{0,120}\b(?:share repurchase|stock repurchase|buyback)\b", re.I,
)
_FORWARD_LOOKING = re.compile(
    r"^(?:forward[- ]looking statements\b|cautionary (?:note|statement)\b|"
    r"this (?:news|press) release contains .{0,40}\bforward[- ]looking statements\b)", re.I,
)
_GUIDANCE_BOILERPLATE = re.compile(r"\b(?:no (?:obligation|duty)|disclaims? any obligation)\b", re.I)


def keyword_classify(text: str, default: str = "other") -> str:
    return next((kind for kind, regex in _SIGNALS.items() if regex.search(text or "")), default)


def _has_financial_figure(line):
    return any(f.startswith(("$", "€", "£", "¥", "USD ", "EUR ", "GBP ", "JPY ", "CAD ", "AUD "))
               or f.endswith("%") for f in extract_figures(line))


def _statements(text, kind):
    signal = _SIGNALS.get(kind)
    if not signal:
        return []
    # Keep intact body paragraphs with the detected action and its surrounding words.
    return [line for line in text.splitlines() if 50 <= len(line) <= MAX_EXCERPT
            and not _ITEM.match(line) and not _PERSONNEL_HEADING.match(line)
            and signal.search(line)]


def _context_statement(body, heading):
    text = " ".join(_SECTION_END.split(body, maxsplit=1)[0].split())
    text = re.sub(rf"^(?:{heading})(?=\s|\.|$)\.?\s*", "", text, flags=re.I)
    # A late qualification can govern the whole disclosure. Use the full section
    # or fall back to filing metadata when it exceeds the publication budget.
    return [text] if 8 <= len(text) <= MAX_EXCERPT else []


def _earnings_exhibit_events(item, document):
    text = edgar.fetch_text(item.extra["cik"], item.accession or "", item.published, document=document)
    if not text:
        item.extra["document_unavailable"] = True
        return []
    lines = []
    for line in text.splitlines():
        if _FORWARD_LOOKING.match(line):
            break
        if 8 <= len(line) <= MAX_EXCERPT:
            lines.append(line)
    buybacks = [line for line in lines if _BUYBACK_AUTHORIZATION.search(line) and _has_financial_figure(line)]
    guidance = [line for line in lines if _SIGNALS["guidance_change"].search(line)
                and not _GUIDANCE_BOILERPLATE.search(line)]
    earnings = [line for line in lines if _EARNINGS_METRIC.search(line) and _has_financial_figure(line)
                and line not in guidance]
    # One results or guidance story plus a separate capital-allocation decision.
    kind, results = ("guidance_change", guidance) if guidance else ("earnings", earnings)
    exhibit_item = replace(item, url=edgar._doc_url(item.extra["cik"], item.accession, document),
                           extra={**item.extra, "exhibit": "99.1"})
    return [Event(exhibit_item, label, 0.9, source_text="\n".join(paragraphs[:6]))
            for label, paragraphs in (("financing", buybacks), (kind, results)) if paragraphs]


def _filing_event(item, kind, paragraphs):
    statement = item.title
    # A filing-date label can differ from the SEC acceptance timestamp.
    if item.extra.get("accepted_at"):
        statement += f" SEC filing date: {item.published}."
    return Event(item, kind, 0.9 if kind != "other" else 0.55,
                 source_text="\n".join([*list(dict.fromkeys(paragraphs))[:6], statement]))


def _current_report_events(item, text, exhibits):
    matches = list(_ITEM.finditer(text))
    sections = [(match.group(1), text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)])
                for i, match in enumerate(matches)]
    seen = {number for number, _ in sections}
    sections.extend((number, "") for number in re.findall(r"\d\.\d\d", item.extra.get("items") or "")
                    if number not in seen)
    topics, contextual = {}, {}
    for number, body in sections:
        if number != "9.01":
            label = _ITEM_TYPES.get(number) or keyword_classify(body)
            if label != "other":
                paragraphs = topics.setdefault(label, [])
                if number in _CONTEXT_HEADINGS:
                    contextual.setdefault(label, []).extend(_context_statement(body, _CONTEXT_HEADINGS[number]))
                else:
                    paragraphs.extend(_statements(body, label))

    exhibit_events = []
    if any(number == "2.02" and _EARNINGS_EXHIBIT.search(body) for number, body in sections):
        if "99.1" in exhibits:
            exhibit_events = _earnings_exhibit_events(item, exhibits["99.1"])
        elif item.extra.get("document"):
            item.extra["document_unavailable"] = True
    for event in exhibit_events:
        if event.event_type in {"earnings", "guidance_change"}:
            topics.pop("earnings", None)
        elif event.event_type == "financing" and "financing" not in contextual and all(
            _BUYBACK_AUTHORIZATION.search(line) for line in topics.get("financing", [])
        ):
            topics.pop("financing", None)

    events = []
    for label, paragraphs in topics.items():
        if label in {"M&A", "financing"}:
            paragraphs.sort(key=_has_financial_figure, reverse=True)
        events.append(_filing_event(item, label, contextual.get(label, []) + paragraphs))
    return events + exhibit_events or [_filing_event(item, "other", [])]


def _events_for_filing(item: RawItem) -> list[Event]:
    form = (item.form or "").upper().removesuffix("/A")
    kind = edgar.FORM_EVENT_MAP.get(form, "other")
    paragraphs = []
    if form in {"8-K", "6-K", "425", "S-4", "F-4"}:
        exhibits = {}
        text = item.extra.get("text", "")
        if not text and item.extra.get("cik"):
            kwargs = {"document": item.extra["document"]} if item.extra.get("document") else {}
            if form == "8-K" and kwargs:
                kwargs["exhibits"] = exhibits
            text = edgar.fetch_text(item.extra["cik"], item.accession or "", item.published, **kwargs)
            if not text:
                item.extra["document_unavailable"] = True
        if form == "8-K":
            return _current_report_events(item, text, exhibits)
        elif form == "6-K":
            # The cover page of a foreign issuer report often only lists administrative text.
            kind = keyword_classify("\n".join(text.splitlines()[20:]))
            paragraphs = _statements(text, kind)
        else:
            paragraphs = _statements(text, kind)
    return [_filing_event(item, kind, paragraphs)]


def _detect_edgar(item: RawItem) -> Event:
    return _events_for_filing(item)[0]


def _detect_rss(item: RawItem) -> Event:
    kind = keyword_classify(item.title)
    publisher = item.extra.get("publisher_type")
    return Event(item, kind, 0.83 if publisher == "press" else 0.62, source_text=item.title)


def _detect_market(move: PriceMove):
    if not move.large_move or move.pct_change is None or move.z_score is None:
        return None
    text = market_stats_text(move)
    url = move.evidence.get("source_url") or f"https://finance.yahoo.com/quote/{move.ticker}/history/"
    item = RawItem("market", move.company, move.ticker, move.role, text, url, move.date,
                   summary=text, extra={"market": {key: value for key, value in move.evidence.items()
                                                       if key != "prior_bars"},
                                        "publisher": move.evidence.get("provider")})
    return Event(item, "large_price_move", 0.8, abs(move.z_score), source_text=text)


def detect_events(edgar_items: list, rss_items: list, price_moves: list) -> list:
    return ([event for item in edgar_items for event in _events_for_filing(item)]
            + [_detect_rss(item) for item in rss_items]
            + [event for move in price_moves if (event := _detect_market(move)) is not None])
