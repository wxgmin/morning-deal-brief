import pytest

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


@pytest.mark.parametrize(("first", "second"), [
    ("Graco announces acquisition of Acme for $500M", "Graco completes acquisition of Acme for $500M"),
    ("Graco reports acquisition growth of -5%", "Graco reports acquisition growth of 5%"),
    ("Fitch downgrades Graco to BBB", "Fitch downgrades Graco to BBB-"),
])
def test_reused_article_url_does_not_hide_material_updates(first, second):
    url = "https://news.example.org/live/coverage"
    events = [_ev("GGG", first, url), _ev("GGG", second, url + "?utm_source=wire")]
    assert dedupe.dedupe(events) == events


def test_unchanged_story_from_two_feeds_dedupes_across_midnight():
    url = "https://news.example.org/releases/acme"
    a = _ev("GGG", "Graco announces $500M acquisition of Acme", url)
    b = _ev("GGG", "Graco announces $500M acquisition of Acme", url + "?utm_medium=rss")
    b.item.published = "2026-11-18"
    assert dedupe.dedupe([a, b]) == [a]
