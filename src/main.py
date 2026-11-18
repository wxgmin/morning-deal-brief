"""Orchestrator and CLI for the Morning Deal Brief pipeline."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, timedelta

from . import archive, brief, config, dedupe, deliver, llm, rank
from .collectors import edgar, market, rss
from .events import classify, detect
from .models import BriefItem


def today_iso() -> str:
    return date.today().isoformat()


def recent_weekdays(end_date: str, days: int) -> list:
    """Return the last `days` weekdays (Mon-Fri) ending on or before end_date."""
    out: list[str] = []
    d = date.fromisoformat(end_date)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


def _serialize(brief_items: list, price_moves: list, date_str: str) -> dict:
    return {
        "date": date_str,
        "items": [asdict(b) for b in brief_items],
        "price_moves": [asdict(m) for m in price_moves],
    }


def run_pipeline(
    cfg,
    as_of_date: str = None,
    include_news: bool = True,
    send: bool = False,
    max_items: int = 5,
    edgar_items: list = None,
    price_moves: list = None,
    rss_items: list = None,
):
    """Run one day's pipeline: collect -> detect -> classify -> dedupe -> rank
    -> judge -> guardrail -> render -> archive -> (optionally) deliver."""
    date_str = as_of_date or today_iso()

    if edgar_items is None:
        edgar_items = edgar.collect_filings(cfg.coverage.companies, date_str, date_str)
    if price_moves is None:
        price_moves = market.collect_market(cfg.coverage.companies, as_of_date=as_of_date)
    if rss_items is None:
        rss_items = rss.collect_rss(cfg.coverage.rss_feeds, cfg.coverage) if include_news else []

    events = detect.detect_events(edgar_items, rss_items, price_moves)
    events = classify.classify_events(events, cfg)
    events = dedupe.dedupe(events)
    ranked = rank.rank_events(events, cfg)

    brief_items: list[BriefItem] = []
    for ev in ranked[:max_items]:
        source_text = ev.source_text or ""
        if ev.item.source == "edgar" and not source_text:
            source_text = edgar.fetch_text(
                ev.item.extra.get("cik", ""), ev.item.accession or "", ev.item.published, max_chars=3000
            )

        judgment = llm.judgment_for_event(cfg, ev, source_text, cfg.coverage)
        if not judgment.grounded:
            # Dropped by the guardrail: never ship an unsourced figure.
            continue

        brief_items.append(
            BriefItem(
                rank=len(brief_items) + 1,
                ticker=ev.item.ticker,
                company=ev.item.company,
                event_type=ev.event_type,
                summary=judgment.summary,
                why_it_matters=judgment.why_it_matters,
                source_url=ev.item.url,
                figures=judgment.figures_cited,
                confidence=judgment.confidence,
            )
        )

    brief_md = brief.render_markdown(brief_items, price_moves, cfg.coverage, date_str)
    brief_html = brief.render_html(brief_items, price_moves, cfg.coverage, date_str)
    payload = _serialize(brief_items, price_moves, date_str)

    archive.save_brief(cfg, date_str, brief_md, brief_html, payload)

    if send:
        deliver.deliver(cfg, date_str, brief_md, brief_html, brief_items, price_moves)

    return brief_items, price_moves


def _run_backfill(cfg, days: int, end_date: str) -> None:
    dates = recent_weekdays(end_date, days)
    start, end = dates[0], dates[-1]

    # Collect the full window once, then bucket per day.
    edgar_all = edgar.collect_filings(cfg.coverage.companies, start, end)
    market_series = market.collect_market_series(cfg.coverage.companies, start, end)

    for d in dates:
        day_edgar = [it for it in edgar_all if it.published == d]
        day_moves = market_series.get(d, [])
        run_pipeline(
            cfg,
            as_of_date=d,
            include_news=False,
            send=False,
            edgar_items=day_edgar,
            price_moves=day_moves,
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Morning Deal Brief")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run", action="store_true", help="Run live and deliver (per DELIVERY)")
    group.add_argument("--dry-run", action="store_true", help="Run live, archive only, no delivery")
    group.add_argument("--backfill", type=int, metavar="DAYS", help="Backfill N weekdays (archive only)")
    parser.add_argument("--end-date", default=None, help="Backfill end date (YYYY-MM-DD), default today")
    args = parser.parse_args(argv)

    cfg = config.load_config()

    if args.backfill:
        _run_backfill(cfg, args.backfill, args.end_date or today_iso())
        return 0

    if args.run:
        run_pipeline(cfg, include_news=True, send=True)
    else:
        # default and --dry-run
        run_pipeline(cfg, include_news=True, send=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
