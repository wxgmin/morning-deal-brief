from src import dedupe, guardrails
from src.events import detect
from src.models import Event, RawItem


def test_guardrail_rejects_currency_vs_percent():
    source = "The company reported $50 million in revenue."
    grounded, _ = guardrails.verify_grounding(
        "Revenue rose 50%.", "Strong growth.", figures_cited=[], source_text=source,
    )
    assert not grounded


def test_guardrail_accepts_billion_abbreviation():
    source = "The company announced a $1.2B deal."
    grounded, _ = guardrails.verify_grounding(
        "Announced a $1.2 billion deal.", "Significant.", figures_cited=[], source_text=source,
    )
    assert grounded


def test_detect_operating_not_rating():
    assert detect.keyword_classify("operating margin improved") == "other"


def test_detect_eps_word_boundary():
    assert detect.keyword_classify("the company took steps to improve") == "other"


def test_dedupe_keeps_distinct_edgar_accessions():
    a = Event(
        item=RawItem(source="edgar", company="Graco", ticker="GGG", role="client",
                     title="Graco filed 8-K", url="u1", published="2026-11-17",
                     form="8-K", accession="a1"),
        event_type="M&A",
    )
    b = Event(
        item=RawItem(source="edgar", company="Graco", ticker="GGG", role="client",
                     title="Graco filed 8-K", url="u2", published="2026-11-17",
                     form="8-K", accession="a2"),
        event_type="management_change",
    )
    out = dedupe.dedupe([a, b])
    assert len(out) == 2
