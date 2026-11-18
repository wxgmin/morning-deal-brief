from src import rank
from src.models import Event, RawItem


def _ev(ticker, role, event_type, confidence=1.0):
    item = RawItem(
        source="rss", company=ticker, ticker=ticker, role=role,
        title="x", url="u", published="2026-11-17",
    )
    return Event(item=item, event_type=event_type, confidence=confidence)


def test_client_ranks_above_peer(cfg):
    events = [_ev("IEX", "peer", "M&A"), _ev("GGG", "client", "M&A")]
    ranked = rank.rank_events(events, cfg)
    assert ranked[0].item.role == "client"


def test_event_weight_orders(cfg):
    events = [_ev("GGG", "client", "other"), _ev("GGG", "client", "M&A")]
    ranked = rank.rank_events(events, cfg)
    assert ranked[0].event_type == "M&A"
