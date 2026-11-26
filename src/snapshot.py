"""Assemble the market snapshot: the cross-asset board, calendar and headlines for today's brief.

Only what the profile's enabled sections need is collected. Every source runs independently and reports
its own state, so a dead feed degrades one panel instead of the whole brief.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import alerts as alerts_mod, archive, catalog, chatgpt, desk_read, macro_read, morning, narrative, surprise, signals as signals_mod, profile as profile_mod
from .collectors import calendar as cal
from .collectors import breadth, capital, dealflow, fred, global_rates, movers as stock_movers, positioning, rates, rates_path
from .collectors import headlines as heads
from .collectors import macro

logger = logging.getLogger(__name__)
SCHEMA = 1
_GROUPS = {g["id"]: g for g in catalog.GROUPS}


def clean(value):
    """JSON-safe: non-finite floats become null (the archive writer rejects NaN)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def _needs(profile, *ids):
    return any(profile_mod.section_enabled(profile, i) for i in ids)


def _source(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), {"state": "ok"}
    except Exception as exc:
        logger.warning("%s unavailable (%s)", getattr(fn, "__name__", "source"), type(exc).__name__)
        return None, {"state": "unavailable", "error": type(exc).__name__}


def _quote_status(quotes):
    bad = [q["symbol"] for q in quotes if q.get("state") == "unavailable"]
    stale = [q["symbol"] for q in quotes if q.get("state") == "stale"]
    state = "unavailable" if quotes and len(bad) == len(quotes) else "partial" if bad or stale else "ok"
    return {"state": state, "total": len(quotes), "ok": len(quotes) - len(bad) - len(stale),
            "unavailable": bad, "stale": stale}


def _prior_snapshot(settings, before: str):
    """The most recent archived edition before this brief date that carried a market snapshot."""
    root = getattr(settings, "archive_dir", None)
    if not root:
        return None
    try:
        for day in sorted(archive.published_dates(root), reverse=True):
            if day < before:
                payload = archive.read_payload(root, day) or {}
                if payload.get("snapshot"):
                    return payload["snapshot"]
    except (OSError, ValueError):
        logger.info("No prior snapshot available for comparison")
    return None


QUICK_SKIP = {"calendar", "ratepath", "positioning", "capital", "curve", "sentiment", "economy"}


def collect(settings, profile: dict | None = None, now: datetime | None = None, *, quick: bool = False) -> dict:
    """quick=True is for the 15-minute alert loop: it skips once-a-day sources and every model call."""
    profile = profile or profile_mod.load_profile(settings)
    if quick:
        profile = {**profile, "sections": [{**s, "enabled": s["enabled"] and s["id"] not in QUICK_SKIP}
                                           for s in profile["sections"]]}
    now = now or datetime.now(timezone.utc)
    tz = settings.timezone
    day = cal.brief_date(now, tz)
    watch = {row["ticker"] for row in profile["watchlist"]}
    instruments = profile_mod.active_instruments(profile)
    want_quotes = _needs(profile, "heartbeats", "tone", "reads", "movers", "boards", "signals")
    want_curve = _needs(profile, "curve", "reads", "signals")
    calendar_cfg = profile["calendar"]

    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        if want_quotes and instruments:
            jobs["quotes"] = pool.submit(_source, macro.fetch_quotes, instruments)
        if _needs(profile, "watchlist") and profile["watchlist"]:
            rows = [{"symbol": macro.yahoo_symbol(r["ticker"]), "label": r["ticker"], "name": r["name"],
                     "role": r["role"], "kind": "equity", "dp": 2, "group": "watchlist", "ticker": r["ticker"]}
                    for r in profile["watchlist"]]
            jobs["watchlist"] = pool.submit(_source, macro.fetch_quotes, rows, with_extended=True)
        if want_curve:
            jobs["curve"] = pool.submit(_source, macro.fetch_treasury_curve, day)
            jobs["funding"] = pool.submit(_source, rates.fetch_funding)
            jobs["real_yields"] = pool.submit(_source, rates.fetch_real_yields, day)
        if _needs(profile, "ratepath", "summary"):
            jobs["rate_path"] = pool.submit(_source, rates_path.fetch_rate_path,
                                            profile_mod.profile_path(settings).parent / "cache")
        if _needs(profile, "calendar"):
            jobs["economic"] = pool.submit(_source, cal.fetch_release_tracker, day, calendar_cfg["horizon_days"], tz,
                                           calendar_cfg["countries"], calendar_cfg["min_impact"],
                                           cache_dir=profile_mod.profile_path(settings).parent / "calendar-cache", now=now)
            if calendar_cfg["earnings"]:
                jobs["earnings"] = pool.submit(_source, cal.fetch_earnings,
                                               [cal.prev_weekday(day), day, cal.next_weekday(day)], watch)
            if calendar_cfg["auctions"]:
                jobs["auctions"] = pool.submit(_source, cal.fetch_auctions, day)
        if _needs(profile, "headlines", "capital") and any(f["enabled"] for f in profile["feeds"]):
            jobs["headlines"] = pool.submit(_source, heads.collect_headlines, profile, now, include_candidates=True)
        market_date = now.astimezone(ZoneInfo("America/New_York")).date()
        if _needs(profile, "capital", "signals"):
            jobs["spreads"] = pool.submit(_source, capital.fetch_spreads, market_date)
        if _needs(profile, "capital"):
            jobs["ipos"] = pool.submit(_source, capital.fetch_ipos, market_date)
        if _needs(profile, "dealflow"):
            jobs["dealflow"] = pool.submit(_source, dealflow.collect, market_date, profile["watchlist"])
        if _needs(profile, "stockmovers"):
            jobs["stock_movers"] = pool.submit(_source, stock_movers.collect, watch)
        if _needs(profile, "positioning", "signals"):
            jobs["oil_curve"] = pool.submit(_source, global_rates.fetch_oil_curve)
        if _needs(profile, "positioning"):
            jobs["positioning"] = pool.submit(_source, positioning.collect,
                                              profile_mod.profile_path(settings).parent / "cache")
            jobs["global_yields"] = pool.submit(_source, global_rates.fetch_global_yields, market_date)
        if _needs(profile, "sentiment", "signals") and not quick:
            jobs["breadth"] = pool.submit(_source, breadth.fetch_breadth, profile_mod.profile_path(settings).parent / "cache")
            jobs["sentiment"] = pool.submit(_source, breadth.fetch_sentiment)
        if _needs(profile, "economy", "signals") and not quick:
            jobs["economy"] = pool.submit(_source, fred.fetch_macro, market_date,
                                          profile_mod.profile_path(settings).parent / "cache")
        done = {name: job.result() for name, job in jobs.items()}

    sources, data = {}, {}
    for name, (value, status) in done.items():
        data[name] = value
        sources[name] = status

    quotes = data.get("quotes") or []
    if "quotes" in done and done["quotes"][1]["state"] == "ok":
        sources["quotes"] = _quote_status(quotes)
    watchlist = data.get("watchlist") or []
    if "watchlist" in done and done["watchlist"][1]["state"] == "ok":
        sources["watchlist"] = _quote_status(watchlist)
    for name in ("economic", "earnings", "auctions", "headlines", "spreads", "ipos"):
        if data.get(name) and data[name].get("state") in {"partial", "disabled", "unavailable"}:
            sources[name] = {"state": data[name]["state"]}
    curve = data.get("curve")

    boards = []
    for group in catalog.GROUPS:
        rows = [q for q in quotes if q.get("group") == group["id"]]
        if rows:
            boards.append({"group": {"id": group["id"], "label": group["label"], "why": group["why"]}, "quotes": rows})

    snapshot = {
        "schema": SCHEMA, "generated_at": now.isoformat(), "timezone": tz, "brief_date": day.isoformat(),
        "markets": profile["markets"], "sector": profile["sector"],
        "sections": [{"id": s["id"], "label": s["label"], "enabled": s["enabled"]} for s in profile["sections"]],
        "heartbeats": [q for q in quotes if q.get("pinned")],
        "boards": boards, "watchlist": watchlist,
        "tone": macro_read.tone(quotes) if _needs(profile, "tone") else None,
        "reads": macro_read.reads(quotes, curve) if _needs(profile, "reads") else [],
        "movers": macro_read.movers(quotes + watchlist) if _needs(profile, "movers") else [],
        "curve": curve if _needs(profile, "curve") else None,
        "calendar": {"economic": data.get("economic"), "earnings": data.get("earnings"),
                     "auctions": data.get("auctions"), "countries": calendar_cfg["countries"],
                     "min_impact": calendar_cfg["min_impact"]},
        "headlines": data.get("headlines"),
        "capital": {"spreads": data.get("spreads"), "ipos": data.get("ipos")},
        "funding": data.get("funding") if want_curve else None,
        "real_yields": data.get("real_yields") if want_curve else None,
        "rate_path": data.get("rate_path"),
        "dealflow": data.get("dealflow"),
        "stock_movers": data.get("stock_movers"),
        "positioning": data.get("positioning"),
        "global_yields": data.get("global_yields"),
        "oil_curve": data.get("oil_curve"),
        "breadth": data.get("breadth"),
        "economy": data.get("economy"),
        "sentiment": data.get("sentiment"),
        "thresholds": macro_read.THRESHOLDS,
        "sources": sources,
    }
    # Signals read the full curve even when the curve panel itself is hidden.
    snapshot["signals"] = signals_mod.evaluate({**snapshot, "curve": curve}) if _needs(profile, "signals") else []
    snapshot["surprises"] = surprise.annotate((data.get("economic") or {}).get("items"))
    snapshot["since"] = morning.since_last(snapshot, _prior_snapshot(settings, day.isoformat()))
    results = [r for d in (snapshot["calendar"].get("earnings") or {}).get("days", []) for r in d["rows"]
               if r.get("result")]
    snapshot["earnings_results"] = sorted(results, key=lambda r: (not r["watch"], -(r.get("market_cap") or 0)))[:12]
    snapshot["summary"] = morning.top_of_morning(snapshot) if _needs(profile, "summary") else []
    snapshot["alerts"] = alerts_mod.evaluate(snapshot, profile.get("alerts") or {})
    if quick:  # no model calls in the alert loop
        snapshot["alerts"] = alerts_mod.evaluate(snapshot, profile.get("alerts") or {})
        return clean(snapshot)
    connection = chatgpt.Connection(settings)
    try:
        generate = connection.generator()
        ai_status = "off"
    except Exception:
        # A machine may not have a supported OS credential store.
        generate, ai_status = None, "off"
    # Sequential on purpose: parallel calls raced on the ChatGPT token refresh and lost the morning note.
    desk_read.build(snapshot, generate, status=ai_status)
    if _needs(profile, "summary"):
        snapshot["morning_note"] = narrative.write(snapshot, generate or _api_generator(settings))
    return clean(snapshot)


def ai_generator(settings):
    """The connected ChatGPT plan, else a configured API provider, else None."""
    try:
        generate = chatgpt.Connection(settings).generator()
    except Exception:
        generate = None
    return generate or _api_generator(settings)


def _api_generator(settings):
    """Fall back to a configured API provider (OpenAI, Anthropic, Gemini, OpenRouter, local) when no ChatGPT plan."""
    if not getattr(settings, "llm_enabled", False):
        return None
    from types import SimpleNamespace

    from .llm import providers

    provider = providers.get_provider(SimpleNamespace(settings=settings))
    if isinstance(provider, providers.NoneProvider):
        return None
    model = settings.llm_model or providers.default_model(getattr(provider, "name", "") or settings.llm_provider)
    return lambda instructions, evidence: provider.chat_json(instructions, evidence, model)
