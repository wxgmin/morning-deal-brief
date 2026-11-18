from src import dedupe
from src.models import Event, RawItem


def _ev(ticker, title, url, text=""):
    item = RawItem(
        source="rss", company=ticker, ticker=ticker, role="peer",
        title=title, url=url, published="2026-11-17", summary=text,
    )
    return Event(item=item, event_type="M&A", source_text=text)


def test_dedupe_same_story_single():
    a = _ev("GGG", "Graco announces $500M acquisition of Acme", "url1", "long text")
    b = _ev("GGG", "Graco announces $500M acquisition of Acme Corp", "url2", "")
    out = dedupe.dedupe([a, b])
    assert len(out) == 1
    assert "url2" in out[0].also_reported_by


def test_dedupe_keeps_richer_source():
    a = _ev("GGG", "Graco announces acquisition", "url1", "")
    b = _ev("GGG", "Graco announces acquisition", "url2", "detailed source text")
    out = dedupe.dedupe([a, b])
    assert len(out) == 1
    assert out[0].source_text == "detailed source text"


def test_dedupe_different_companies_kept():
    a = _ev("GGG", "Graco announces acquisition", "url1")
    b = _ev("IEX", "IDEX announces acquisition", "url2")
    out = dedupe.dedupe([a, b])
    assert len(out) == 2
