"""Orchestrator and CLI for the Morning Deal Brief pipeline."""
from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import archive, brief, config, dedupe, deliver, llm, rank
from .collectors import edgar, market, rss
from .events import classify, detect
from .models import BriefItem

logger = logging.getLogger(__name__)


def today_iso(tz: str = None) -> str:
    """Today's date in the configured timezone (falls back to local date)."""
    if tz:
        try:
            return datetime.now(ZoneInfo(tz)).date().isoformat()
        except Exception:
            pass
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
    date_str = as_of_date or today_iso(cfg.settings.timezone)

    if edgar_items is None:
        # A morning run must see filings filed since the previous run, including the
        # prior business day (and spanning a weekend/holiday).
        edgar_start = recent_weekdays(date_str, 5)[0]
        edgar_items = edgar.collect_filings(cfg.coverage.companies, edgar_start, date_str)
        if not edgar_items:
            logger.warning("No EDGAR filings collected in window [%s, %s]", edgar_start, date_str)

    if price_moves is None:
        price_moves = market.collect_market(cfg.coverage.companies, as_of_date=date_str)
        if not price_moves:
            raise RuntimeError("Market data unavailable; aborting (network/coverage?)")

    if rss_items is None:
        rss_items = rss.collect_rss(cfg.coverage.rss_feeds, cfg.coverage) if include_news else []
        if include_news and not rss_items:
            logger.warning("No RSS items collected")

    events = detect.detect_events(edgar_items, rss_items, price_moves)
    events = classify.classify_events(events, cfg)
    events = dedupe.dedupe(events)
    ranked = rank.rank_events(events, cfg)

    brief_items: list[BriefItem] = []
    # Collect up to max_items GROUNDED items from the full ranked list (a guardrail
    # fallback is deterministic and always grounded, so this only stops at the cap).
    for ev in ranked:
        if len(brief_items) >= max_items:
            break
        source_text = ev.source_text or ""
        if ev.item.source == "edgar" and not source_text:
            source_text = edgar.fetch_text(
                ev.item.extra.get("cik", ""), ev.item.accession or "", ev.item.published, max_chars=3000
            )

        judgment = llm.judgment_for_event(cfg, ev, source_text, cfg.coverage)
        if not judgment.grounded:
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

    # Mirror the live 5-business-day catch-up window so backfilled briefs include
    # filings from the prior business day / weekend gap.
    edgar_start = recent_weekdays(start, 5)[0]
    edgar_all = edgar.collect_filings(cfg.coverage.companies, edgar_start, end)
    # Extend the market window so the first brief date still has a prior session.
    market_start = (date.fromisoformat(start) - timedelta(days=7)).isoformat()
    market_series = market.collect_market_series(cfg.coverage.companies, market_start, end)
    trading_dates = sorted(market_series.keys())

    for d in dates:
        window = set(recent_weekdays(d, 5))
        day_edgar = [it for it in edgar_all if it.published in window]
        # A morning brief shows the latest COMPLETED trading day strictly before d.
        candidates = [t for t in trading_dates if t < d]
        day_moves = market_series[candidates[-1]] if candidates else []
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

    if args.backfill is not None and args.backfill < 1:
        parser.error("--backfill requires a positive integer")
    if args.end_date and not args.backfill:
        parser.error("--end-date requires --backfill")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        cfg = config.load_config()
        if args.backfill:
            _run_backfill(cfg, args.backfill, args.end_date or today_iso(cfg.settings.timezone))
        elif args.run:
            if cfg.settings.delivery == "none":
                logger.warning("--run requested but DELIVERY=none; brief will be archived only")
            run_pipeline(cfg, include_news=True, send=True)
        else:
            run_pipeline(cfg, include_news=True, send=False)
        return 0
    except Exception as exc:
        logger.error("pipeline failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
