from src import brief, dedupe, llm, rank
from src.events import classify, detect
from src.models import BriefItem, PriceMove, RawItem


def test_pipeline_produces_grounded_brief(cfg):
    edgar_items = [
        RawItem(
            source="edgar", company="Graco Inc.", ticker="GGG", role="client",
            title="Graco filed 10-K", url="https://www.sec.gov/ggg", published="2026-11-17", form="10-K",
        ),
        RawItem(
            source="edgar", company="IDEX", ticker="IEX", role="peer",
            title="IDEX filed S-4", url="https://www.sec.gov/iex", published="2026-11-17", form="S-4",
        ),
    ]
    moves = [
        PriceMove(
            ticker="GGG", company="Graco", role="client", date="2026-11-17",
            close=100, pct_change=-3.2, volume=1e6, volume_z=2.1, z_score=-2.4, large_move=True,
        ),
    ]

    events = detect.detect_events(edgar_items, [], moves)
    events = classify.classify_events(events, cfg)  # no LLM key -> no-op
    events = dedupe.dedupe(events)
    ranked = rank.rank_events(events, cfg)

    brief_items = []
    for ev in ranked[:5]:
        j = llm.judgment_for_event(cfg, ev, ev.source_text, cfg.coverage)
        assert j.grounded
        brief_items.append(
            BriefItem(
                rank=len(brief_items) + 1,
                ticker=ev.item.ticker,
                company=ev.item.company,
                event_type=ev.event_type,
                summary=j.summary,
                why_it_matters=j.why_it_matters,
                source_url=ev.item.url,
                figures=j.figures_cited,
                confidence=j.confidence,
            )
        )

    md = brief.render_markdown(brief_items, moves, cfg.coverage, "2026-11-17")
    html = brief.render_html(brief_items, moves, cfg.coverage, "2026-11-17")

    assert "Graco" in md
    assert "<html" in html
    assert all(b.source_url.startswith("http") for b in brief_items)
