"""Morning brief orchestration with one cutoff for fresh and replayed reports."""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from filelock import Timeout

from . import archive, brief, config, dedupe, deliver, llm, profile, rank
from . import snapshot as snapshot_mod
from .collectors import edgar, market, rss
from .events import detect
from .guardrails import safe_http_url
from .models import BriefItem, PriceMove

logger = logging.getLogger(__name__)
_CATCHUP_WEEKDAYS = 5


def today_iso(tz: str = None) -> str:
    return datetime.now(ZoneInfo(tz) if tz else None).date().isoformat()


def recent_weekdays(end_date: str, days: int) -> list[str]:
    result = []
    day = date.fromisoformat(end_date)
    while len(result) < days:
        if day.weekday() < 5:
            result.append(day.isoformat())
        day -= timedelta(days=1)
    return result[::-1]


def briefing_window(cfg, date_str, now=None):
    day = date.fromisoformat(date_str)
    tz = ZoneInfo(cfg.settings.timezone)
    previous = date.fromisoformat(recent_weekdays((day - timedelta(days=1)).isoformat(), 1)[0])
    start = datetime.combine(previous, cfg.settings.briefing_time, tzinfo=tz)
    end = datetime.combine(day, cfg.settings.briefing_time, tzinfo=tz)
    return start.astimezone(timezone.utc), min(end.astimezone(timezone.utc), now) if now else end.astimezone(timezone.utc)


def _live_window(cfg, date_str, now):
    start, cutoff = briefing_window(cfg, date_str, now=now)
    day = date.fromisoformat(date_str)
    if day.weekday() >= 5:
        return (start, cutoff), None

    root = cfg.settings.archive_dir
    oldest = recent_weekdays((day - timedelta(days=1)).isoformat(), _CATCHUP_WEEKDAYS + 1)[0]
    for prior in sorted(archive.published_dates(root), reverse=True):
        if prior >= date_str or date.fromisoformat(prior).weekday() >= 5:
            continue
        try:
            edition = archive.read_payload(root, prior)
            if (not edition or edition.get("mode") != "live"
                    or edition.get("outcome") not in {"partial", "complete"} or edition.get("is_preview")):
                continue
            _, final_cutoff = briefing_window(cfg, prior)
            if datetime.fromisoformat(edition["cutoff"]) != final_cutoff:
                continue
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning("Could not use %s as a recovery reference", prior)
            continue
        if final_cutoff >= start:
            break
        recovery_start = briefing_window(cfg, oldest)[1] if prior < oldest else final_cutoff
        return (recovery_start, cutoff), {
            "last_finalized_date": prior, "omitted_older_windows": prior < oldest,
        }
    return (start, cutoff), None


def _items_in_window(items, start, cutoff, key):
    kept, unknown = [], 0
    for item in items:
        raw = item.extra.get(key) or ""
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if when.tzinfo is None:
                raise ValueError("timezone required")
        except (ValueError, TypeError, AttributeError):
            unknown += 1
            continue
        if start < when <= cutoff:
            item.extra["available_at"] = when.astimezone(timezone.utc).isoformat()
            kept.append(item)
    return kept, unknown


def _source_status(states, enabled=True):
    if not enabled:
        return "disabled"
    if not states or all(value == "unavailable" for value in states.values()):
        return "unavailable"
    return "ok" if all(value == "ok" for value in states.values()) else "partial"


def _collect(cfg, date_str, window, include_news, filings, moves, news):
    start, cutoff = window
    edgar_status, news_status = {}, {}
    teams = cfg.coverage.companies
    with ThreadPoolExecutor(max_workers=3) as pool:
        if filings is None:
            sec_start = start.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            job_edgar = pool.submit(edgar.collect_filings, teams, sec_start,
                                    date_str, status=edgar_status)
        if news is None and include_news:
            job_news = pool.submit(rss.collect_rss, cfg.coverage.rss_feeds, cfg.coverage,
                                   start.date().isoformat(), status=news_status)
        if moves is None:
            job_market = pool.submit(market.collect_market, teams, date_str, settings=cfg.settings, cutoff=cutoff)
        if filings is None:
            filings = job_edgar.result()
        if news is None:
            news = job_news.result() if include_news else []
        if moves is None:
            moves = job_market.result()
    return filings, moves, news, edgar_status, news_status


def _snapshot_state(snapshot):
    states = [v.get("state") for v in snapshot.get("sources", {}).values() if v.get("state") != "disabled"]
    if not states or all(v == "unavailable" for v in states):
        return "unavailable"
    return "ok" if all(v == "ok" for v in states) else "partial"


def _status(cfg, moves, edgar_status, news_status, events, unresolved, expected, news_enabled, snapshot=None):
    by_ticker = {move.ticker: move for move in moves}
    market_status = {
        co.ticker: ("unavailable" if co.ticker not in by_ticker else
                    "partial" if any(getattr(by_ticker[co.ticker], key) is None
                                     for key in ("pct_change", "z_score", "volume_z"))
                    else "ok")
        for co in cfg.coverage.companies
    }
    sec = {**edgar_status}
    for event in events:
        if event.item.extra.get("document_unavailable"):
            sec[event.item.ticker] = "partial"
    has_companies = bool(cfg.coverage.companies)
    sec_state = _source_status(sec, has_companies)
    if unresolved and sec_state == "ok":
        sec_state = "partial"
    sources = {
        "filings": {"state": sec_state, "issuers": sec, "unknown_acceptance": unresolved},
        "market": {"state": _source_status(market_status, has_companies), "issuers": market_status, "session": expected},
        "news": {"state": _source_status(news_status, news_enabled and has_companies), "feeds": news_status},
    }
    if snapshot is not None:
        sources["snapshot"] = {"state": _snapshot_state(snapshot),
                               "issuers": {name: v.get("state", "unavailable")
                                           for name, v in snapshot.get("sources", {}).items()}}
    # A usable market snapshot still makes an edition readable during a total issuer outage.
    states = [detail["state"] for key, detail in sources.items()
              if detail["state"] != "disabled" and key != "snapshot"]
    if not states and snapshot is not None:
        states = [sources["snapshot"]["state"]]
    outcome = "unavailable" if all(s == "unavailable" for s in states) else (
        "complete" if all(s == "ok" for s in states) else "partial")
    if outcome == "unavailable" and sources.get("snapshot", {}).get("state") in {"ok", "partial"}:
        outcome = "partial"
    return sources, outcome


def _publication(cfg, date_str, window, filings, moves, news, edgar_status, news_status, mode, max_items,
                 *, include_news=False, recovery=None, snapshot=None):
    start, cutoff = window
    _, scheduled_cutoff = briefing_window(cfg, date_str)
    expected = market.expected_session(date_str, cutoff)
    moves = [move for move in moves if move.date == expected]
    filings, unresolved = _items_in_window(filings, start, cutoff, "accepted_at")
    news, _ = _items_in_window(news, start, cutoff, "published_at")
    prior_date = start.astimezone(ZoneInfo(cfg.settings.timezone)).date().isoformat()
    suppress_repeat_moves = market.expected_session(prior_date, start) == expected
    events = detect.detect_events(filings, news, [] if suppress_repeat_moves else moves)
    sources, outcome = _status(cfg, moves, edgar_status, news_status, events,
                               unresolved, expected, include_news and bool(cfg.coverage.rss_feeds), snapshot)
    selected = []
    model_state = {}
    ranked = rank.rank_events(dedupe.dedupe(events), cfg)
    for event in ranked:
        if len(selected) == max_items or event.score <= 0:
            break
        item = event.item
        if event.event_type == "other" and item.source == "rss":
            continue
        judgment = llm.judgment_for_event(cfg if mode == "live" else None, event, event.source_text,
                                          cfg.coverage, state=model_state)
        if not judgment.grounded:
            continue
        evidence = {
            "statement": judgment.summary, "accession": item.accession, "form": item.form,
            "index_url": item.extra.get("index_url"), "feed": item.extra.get("feed"),
            "market": item.extra.get("market"), "method": judgment.method,
            "editorial_score": round(event.score, 4),
        }
        if item.extra.get("exhibit"):
            evidence["exhibit"] = item.extra["exhibit"]
        selected.append(BriefItem(
            len(selected) + 1, item.ticker, item.company, event.event_type, judgment.summary,
            judgment.why_it_matters, item.url, judgment.figures_cited, judgment.confidence,
            source=item.source, publisher=item.extra.get("publisher") or {"edgar": "SEC EDGAR", "rss": "News feed"}.get(item.source, "Market"),
            published_at=item.extra.get("available_at") or item.published, role=item.role,
            evidence=evidence, related_sources=[url for url in event.also_reported_by if safe_http_url(url)],
        ))
    payload = {
        "schema_version": 2, "date": date_str, "mode": mode, "outcome": outcome,
        "generated_at": datetime.now(timezone.utc).isoformat(), "timezone": cfg.settings.timezone,
        "window_start": start.isoformat(), "cutoff": cutoff.isoformat(),
        "scheduled_cutoff": scheduled_cutoff.isoformat(), "is_preview": cutoff < scheduled_cutoff, "sources": sources,
        "coverage": [asdict(co) for co in cfg.coverage.companies], "sector": cfg.coverage.sector,
        "model_fallback": bool(model_state.get("unavailable")),
        "items": [asdict(item) for item in selected], "price_moves": [asdict(move) for move in moves],
    }
    if recovery:
        payload["recovery"] = recovery
    if snapshot is not None:
        payload["snapshot"] = snapshot
    if cfg.desk_profile is not None:
        payload["profile_revision"] = profile.revision(cfg.desk_profile)
    md = brief.render_markdown(selected, moves, cfg.coverage, date_str, payload)
    html = brief.render_html(selected, moves, cfg.coverage, date_str, payload)
    archive.save_brief(cfg, date_str, md, html, payload)
    logger.info("%s %s: %s, %d events, %d market rows (%s)", mode, date_str, outcome,
                len(selected), len(moves), expected)
    return selected, moves, archive.read_payload(cfg.settings.archive_dir, date_str)


def _existing_delivery(cfg, date_str, send, live_window):
    payload = archive.read_payload(cfg.settings.archive_dir, date_str)
    if not payload or not deliver.read_receipt(cfg, date_str):
        return None
    items = [BriefItem(**item) for item in payload["items"]]
    moves = [PriceMove(**row) for row in payload["price_moves"]]
    if send:
        _deliver_saved(cfg, date_str, payload, items, moves, live_window)
    return items, moves


def _deliver_saved(cfg, date_str, payload, items, moves, live_window):
    if cfg.settings.delivery == "none":
        return
    if payload.get("mode") != "live":
        raise RuntimeError("Only a live edition can be delivered")
    if payload["outcome"] == "unavailable" or (cfg.settings.require_complete and payload["outcome"] != "complete"):
        raise RuntimeError("Delivery withheld because source coverage is incomplete")
    if datetime.fromisoformat(payload["cutoff"]) < live_window[1]:
        raise RuntimeError("Delivery withheld because the archive was generated before the configured cutoff")
    target = archive.artifacts(cfg.settings.archive_dir, date_str, payload)
    deliver.deliver(cfg, date_str, (target / "brief.md").read_text(encoding="utf-8"),
                    (target / "brief.html").read_text(encoding="utf-8"), items, moves, payload)


def _safe_snapshot(cfg):
    """The market snapshot must never be able to stop the deal-flow brief (or the other way round)."""
    try:
        return snapshot_mod.collect(cfg.settings, cfg.desk_profile)
    except Exception as exc:
        logger.error("Market snapshot failed (%s)", type(exc).__name__)
        preferences = cfg.desk_profile or profile.default_profile()
        return {"sections": preferences["sections"], "timezone": cfg.settings.timezone,
                "brief_date": today_iso(cfg.settings.timezone), "markets": preferences["markets"],
                "sources": {"snapshot": {"state": "unavailable"}}}


def run_pipeline(cfg, as_of_date=None, include_news=True, send=False, max_items=5,
                 edgar_items=None, price_moves=None, rss_items=None, scheduled=False,
                 snapshot=None, include_snapshot=None):
    date_str = as_of_date or today_iso(cfg.settings.timezone)
    if not 1 <= max_items <= 10:
        raise ValueError("max_items must be between 1 and 10")
    now = datetime.now(timezone.utc)
    scheduled_window = briefing_window(cfg, date_str)
    if send and cfg.settings.delivery != "none" and now < scheduled_window[1]:
        raise RuntimeError("Delivery starts at the configured briefing time")
    with archive.job_lock(cfg):
        if cfg.profile_managed:
            preferences = profile.load_profile(cfg.settings)
            cfg = replace(cfg, coverage=config.coverage_from_profile(cfg.settings, preferences), desk_profile=preferences)
        existing = _existing_delivery(cfg, date_str, send, scheduled_window)
        if existing is not None:
            return existing
        if scheduled and cfg.settings.delivery == "none":
            previous = archive.read_payload(cfg.settings.archive_dir, date_str) or {}
            if (previous.get("mode") == "live" and previous.get("outcome") == "complete"
                    and datetime.fromisoformat(previous["cutoff"]) >= scheduled_window[1]):
                return [BriefItem(**row) for row in previous["items"]], [PriceMove(**row) for row in previous["price_moves"]]
        window, recovery = _live_window(cfg, date_str, now)
        if include_snapshot is None:  # injected test data means "do not touch the network"
            include_snapshot = (date_str == now.astimezone(ZoneInfo(cfg.settings.timezone)).date().isoformat()
                                and edgar_items is None and price_moves is None and rss_items is None)
        with ThreadPoolExecutor(max_workers=1) as side:
            snapshot_job = side.submit(_safe_snapshot, cfg) if include_snapshot and snapshot is None else None
            filings, moves, news, sec, feeds = _collect(cfg, date_str, window, include_news,
                                                        edgar_items, price_moves, rss_items)
            if snapshot_job is not None:
                snapshot = snapshot_job.result()
        if edgar_items is not None:
            sec = {company.ticker: "ok" for company in cfg.coverage.companies}
        items, moves, payload = _publication(cfg, date_str, window, filings, moves, news, sec, feeds,
                                             "live", max_items, include_news=include_news, recovery=recovery,
                                             snapshot=snapshot)
        if payload["outcome"] == "unavailable":
            raise RuntimeError("No source was available. See the archived coverage status.")
        if send:
            _deliver_saved(cfg, date_str, payload, items, moves, scheduled_window)
        return items, moves


def _run_backfill(cfg, days: int, end_date: str) -> None:
    now = datetime.now(timezone.utc)
    if now < briefing_window(cfg, end_date)[1]:
        end_date = (date.fromisoformat(today_iso(cfg.settings.timezone)) - timedelta(days=1)).isoformat()
    dates = recent_weekdays(end_date, days)
    first, last = dates[0], dates[-1]
    sessions = {day: market.expected_session(day, briefing_window(cfg, day)[1]) for day in dates}
    sec = {}
    with archive.job_lock(cfg):
        with ThreadPoolExecutor(max_workers=2) as pool:
            sec_start = briefing_window(cfg, first)[0].astimezone(ZoneInfo("America/New_York")).date().isoformat()
            filing_job = pool.submit(edgar.collect_filings, cfg.coverage.companies,
                                     sec_start, last, status=sec)
            market_job = pool.submit(market.collect_market_series, cfg.coverage.companies,
                                     sessions[first], sessions[last], settings=cfg.settings)
            filings, series = filing_job.result(), market_job.result()
        failures = 0
        for day in dates:
            existing = archive.read_payload(cfg.settings.archive_dir, day)
            if existing and (existing.get("mode") != "backfill" or deliver.read_receipt(cfg, day)):
                logger.info("Keeping existing brief for %s", day)
                continue
            _, _, payload = _publication(cfg, day, briefing_window(cfg, day), filings,
                                          series.get(sessions[day], []), [], sec, {},
                                          "backfill", 5)
            failures += payload["outcome"] == "unavailable"
        if failures:
            raise RuntimeError(f"All enabled sources were unavailable for {failures} backfill date(s)")


def _run_scheduled(cfg, now=None):
    tz = ZoneInfo(cfg.settings.timezone)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    if local.weekday() >= 5 or not cfg.settings.briefing_time <= local.time() < time(8):
        return
    run_pipeline(cfg, as_of_date=local.date().isoformat(), send=True, scheduled=True)


def _retry_delivery(cfg, day):
    if cfg.settings.delivery == "none":
        raise RuntimeError("No delivery channel is configured")
    with archive.job_lock(cfg):
        payload = archive.read_payload(cfg.settings.archive_dir, day)
        if not payload or payload.get("mode") != "live":
            raise RuntimeError("No live edition exists for this date")
        if datetime.now(timezone.utc) < briefing_window(cfg, day)[1]:
            raise RuntimeError("The configured cutoff has not passed")
        _deliver_saved(cfg, day, payload, [BriefItem(**item) for item in payload["items"]],
                       [PriceMove(**row) for row in payload["price_moves"]], briefing_window(cfg, day))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Morning Deal Brief")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--run", action="store_true", help="Archive and deliver (per DELIVERY)")
    modes.add_argument("--dry-run", action="store_true", help="Collect and archive without delivering")
    modes.add_argument("--backfill", type=int, metavar="DAYS", help="Replay N weekdays without delivering")
    modes.add_argument("--scheduled", action="store_true", help="Run during the configured weekday morning window")
    modes.add_argument("--retry-delivery", metavar="DATE", help="Retry safe deliveries for an archived live edition")
    modes.add_argument("--alerts", action="store_true",
                       help="Collect the market snapshot and push new alerts (schedule every 15 minutes)")
    parser.add_argument("--end-date", help="Backfill end date (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    if args.backfill is not None and not 1 <= args.backfill <= 260:
        parser.error("--backfill must be between 1 and 260 weekdays")
    if args.end_date and not args.backfill:
        parser.error("--end-date requires --backfill")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        cfg = config.load_config()
        if args.backfill:
            end = archive._validated_date(args.end_date) if args.end_date else today_iso(cfg.settings.timezone)
            if end > today_iso(cfg.settings.timezone):
                raise ValueError("Backfill end date is in the future")
            _run_backfill(cfg, args.backfill, end)
        elif args.scheduled:
            _run_scheduled(cfg)
        elif args.retry_delivery:
            _retry_delivery(cfg, archive._validated_date(args.retry_delivery))
        elif args.alerts:
            from . import alerts as alerts_mod
            snap = snapshot_mod.collect(cfg.settings, quick=True)
            sent = alerts_mod.push_new(snap.get("alerts") or [], cfg.settings)
            logger.info("Alerts: %d triggered, %d newly pushed", len(snap.get("alerts") or []), len(sent))
        else:
            run_pipeline(cfg, send=args.run)
        return 0
    except Timeout:
        logger.error("Another brief job is active")
        return 2
    except (ValueError, OSError, RuntimeError) as exc:
        logger.error("Pipeline failed: %s", exc)
        return 1
    except Exception as exc:
        logger.error("Pipeline failed (%s). Check source and configuration diagnostics.", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
