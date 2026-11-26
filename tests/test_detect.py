from src.events import detect
from src.models import PriceMove, RawItem


def test_keyword_classify_ma():
    assert detect.keyword_classify("Acme announced a merger and definitive agreement") == "M&A"


def test_keyword_classify_earnings():
    assert detect.keyword_classify("The company reported fourth quarter results of operations") == "earnings"


def test_keyword_classify_defaults_other():
    assert detect.keyword_classify("Routine announcement with no signal") == "other"


def test_detect_edgar_form_map():
    item = RawItem(
        source="edgar", company="Graco", ticker="GGG", role="client",
        title="x", url="u", published="2026-11-17", form="10-Q",
    )
    ev = detect._detect_edgar(item)
    assert ev.event_type == "earnings"


def test_detect_edgar_8k_keyword(monkeypatch):
    monkeypatch.setattr(detect.edgar, "fetch_text", lambda *a, **k: "Item 2.02 results of operations")
    item = RawItem(
        source="edgar", company="Graco", ticker="GGG", role="client",
        title="x", url="u", published="2026-11-17", form="8-K",
        accession="a", extra={"cik": "123"},
    )
    ev = detect._detect_edgar(item)
    assert ev.event_type == "earnings"


def test_detect_market_large_move():
    move = PriceMove(
        ticker="GGG", company="Graco", role="client", date="2026-11-17",
        close=100, pct_change=-3.2, volume=1e6, volume_z=2.1, z_score=-2.4, large_move=True,
    )
    ev = detect._detect_market(move)
    assert ev is not None
    assert ev.event_type == "large_price_move"


def test_detect_market_small_move_none():
    move = PriceMove(
        ticker="GGG", company="Graco", role="client", date="2026-11-17",
        close=100, pct_change=-0.5, volume=1e6, volume_z=0.1, z_score=-0.4, large_move=False,
    )
    assert detect._detect_market(move) is None
