"""What is scheduled: economic releases, earnings, Treasury auctions. Each source reports its own state.

Sources (all keyless):
  * Economic data  : Forex Factory weekly calendar JSON and published results (unofficial)
  * Earnings       : Nasdaq earnings calendar API (unofficial; consensus EPS shown as published)
  * Auctions       : U.S. Treasury FiscalData upcoming_auctions (official)
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ..util import get_bytes
from . import official_calendar

logger = logging.getLogger(__name__)

_ECON_URLS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
              "https://nfs.faireconomy.media/ff_calendar_nextweek.json"]
_NASDAQ = "https://api.nasdaq.com/api/calendar/earnings"
_FISCAL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/upcoming_auctions"
_HEADERS = {"User-Agent": "Mozilla/5.0 (MorningBrief)", "Accept": "application/json"}
_IMPACT_RANK = {"Low": 1, "Medium": 2, "High": 3, "Holiday": 1}
_VENDOR_CALENDAR = "https://www.forexfactory.com/calendar"
_RESULTS_LOOKBACK = 7
_VENDOR_DAYS = re.compile(r"window\.calendarComponentStates\[\d+\]\s*=\s*\{\s*days:\s*")
_VENDOR_EVENT = re.compile(r"/calendar\?day=[a-z]{3}\d{1,2}\.\d{4}#detail=(\d+)")
_READING = re.compile(r"[+\-−]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)(?:%|[KMBT])?", re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def brief_date(now: datetime, tz: str) -> date:
    """The trading day this brief is *for*: today on a weekday, the next Monday on a weekend."""
    local = now.astimezone(ZoneInfo(tz)).date()
    while local.weekday() >= 5:
        local += timedelta(days=1)
    return local


def next_weekday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def parse_economic(rows: list, *, start: date, days: int, tz: str, countries: list, min_impact: str) -> list:
    zone, floor, end = ZoneInfo(tz), _IMPACT_RANK[min_impact], start + timedelta(days=days)
    events = []
    for row in rows:
        try:
            when = datetime.fromisoformat(row["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if when.tzinfo is None:
            continue
        all_day = when.hour == when.minute == 0
        when = when if all_day else when.astimezone(zone)
        impact = row.get("impact") if row.get("impact") in _IMPACT_RANK else "Low"
        # Holidays are always shown for selected countries: a closed market matters at any impact level.
        if row.get("country") not in countries or not start <= when.date() < end:
            continue
        if impact != "Holiday" and _IMPACT_RANK[impact] < floor:
            continue
        events.append({"time": when.isoformat(), "all_day": all_day, "local_date": when.date().isoformat(),
                       "country": row.get("country"), "title": str(row.get("title") or "")[:140],
                       "impact": impact, "forecast": str(row.get("forecast") or ""),
                       "previous": str(row.get("previous") or "")})
    events.sort(key=lambda e: (e["time"], -_IMPACT_RANK[e["impact"]]))
    return events


def fetch_economic(start: date, days: int, tz: str, countries: list, min_impact: str) -> dict:
    source = {"provider": "Forex Factory calendar (unofficial)", "source_url": "https://www.forexfactory.com/calendar"}
    if not countries:
        return {"state": "disabled", "items": [], "note": "No regions selected for economic data.", **source}
    rows, failures = [], 0
    for url in _ECON_URLS:
        try:
            batch = json.loads(get_bytes(url, _HEADERS, limit=2_000_000))
            if not isinstance(batch, list):
                raise ValueError("not a weekly calendar")
            rows.extend(batch)
        except Exception as exc:  # next week's file only exists late in the week; that is not an error
            failures += 1
            logger.info("Economic calendar file unavailable (%s)", type(exc).__name__)
    if failures == len(_ECON_URLS):
        raise OSError("economic calendar unavailable")
    items = parse_economic(rows, start=start, days=days, tz=tz, countries=countries, min_impact=min_impact)
    covered = []
    for row in rows:
        try:
            stamp = datetime.fromisoformat(row["date"])
        except (KeyError, ValueError, TypeError):
            continue
        if stamp.tzinfo is not None:
            covered.append(stamp.astimezone(ZoneInfo(tz)).date())
    through = max(covered) if covered else None
    incomplete = through is None or start + timedelta(days=days - 1) > through
    note = "The vendor calendar does not cover the full date range. Check the official schedules below." if incomplete else None
    return {"state": "partial" if incomplete else "ok", "items": items,
            "covered_through": str(through) if through else None, "note": note, **source}


def economic_items(economic):
    return economic.get("items", []) if economic else []


def _reading(value):
    value = value.strip() if isinstance(value, str) else ""
    return value if len(value) <= 24 and _READING.fullmatch(value) else ""


def _vendor_date(day):
    return f"{_MONTHS[day.month - 1]}{day.day}.{day.year}"


def _published_week(week, start, now, tz, countries):
    url = f"{_VENDOR_CALENDAR}?week={_vendor_date(week)}"
    page = get_bytes(url, {**_HEADERS, "Accept": "text/html"}, limit=2_000_000, attempts=1).decode("utf-8")
    marker = _VENDOR_DAYS.search(page)
    if not marker:
        raise ValueError("missing calendar data")
    days, _ = json.JSONDecoder().raw_decode(page, marker.end())
    if not isinstance(days, list) or not days:
        raise ValueError("invalid calendar data")
    zone = ZoneInfo(tz)
    items = []
    for day in days:
        if not isinstance(day, dict) or not isinstance(day.get("events"), list):
            raise ValueError("invalid calendar day")
        for row in day["events"]:
            if not isinstance(row, dict):
                continue
            actual, event_id = _reading(row.get("actual")), row.get("id")
            if not actual or row.get("currency") not in countries or row.get("leaked") or row.get("timeMasked"):
                continue
            if type(event_id) is not int or event_id <= 0:
                continue
            try:
                moment = datetime.fromtimestamp(row["dateline"], zone)
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                continue
            if moment > now or moment.date() < start:
                continue
            title = str(row.get("name") or "").strip()[:140]
            if not title:
                continue
            event_path = row.get("url")
            match = _VENDOR_EVENT.fullmatch(event_path) if isinstance(event_path, str) else None
            source_url = (f"https://www.forexfactory.com{event_path}" if match and match[1] == str(event_id)
                          else f"{url}#detail={event_id}")
            items.append({"time": moment.isoformat(), "all_day": False, "local_date": moment.date().isoformat(),
                          "country": row["currency"], "title": title,
                          "impact": str(row.get("impactName") or "").capitalize(),
                          "actual": actual, "forecast": _reading(row.get("forecast")),
                          "previous": _reading(row.get("revision")) or _reading(row.get("previous")),
                          "provider": "Forex Factory", "source_url": source_url, "actual_source_url": source_url})
    return items


def _published_results(start, now, tz, countries):
    # The site can label dates in a different timezone. Use timestamps to assign local dates.
    first = start - timedelta(days=1)
    first -= timedelta(days=(first.weekday() + 1) % 7)
    last = now.astimezone(ZoneInfo(tz)).date() + timedelta(days=1)
    weeks = [first + timedelta(days=offset) for offset in range(0, (last - first).days + 1, 7)]
    items, failures = [], 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(_published_week, week, start, now, tz, countries) for week in weeks]
        for job in jobs:
            try:
                items.extend(job.result())
            except (OSError, ValueError, KeyError, TypeError) as exc:
                failures += 1
                logger.info("Economic calendar results unavailable (%s)", type(exc).__name__)
    state = "ok" if not failures else "unavailable" if failures == len(weeks) else "partial"
    return items, state


def _merge_key(event):
    title = " ".join(re.findall(r"[a-z0-9]+", event["title"].lower()))
    # Consolidate known equivalent releases at the same time. Similar but distinct
    # releases (such as core CPI and payrolls versus the unemployment rate) stay visible.
    aliases = {
        "payrolls": r"^employment situation|^non farm employment change|^nonfarm payroll",
        "cpi": r"^consumer price index(?: monthly)?$|^cpi m m$",
        "ppi": r"^producer price index(?: monthly)?$|^ppi m m$",
        "retail": r"^advance monthly sales for retail|^retail sales m m$",
        "trade": r"^u s international trade in goods and services|^trade balance$",
        "weekly-claims": r"^unemployment insurance weekly claims|^unemployment claims|^initial jobless claims",
        "job-openings": r"^job openings and labor turnover|^jolts job openings",
    }
    if event["country"] == "USD":
        title = next((key for key, pattern in aliases.items() if re.search(pattern, title)), title)
    moment = event["local_date"] if event["all_day"] else datetime.fromisoformat(event["time"])
    return event["country"], moment, title


def fetch_release_tracker(start, days, tz, countries, min_impact, *, cache_dir=None, now=None):
    if not countries:
        return {"state": "disabled", "items": [], "note": "No regions selected for economic data.",
                "provider": "Economic release tracker", "coverage": []}
    end = start + timedelta(days=days - 1)
    now = now or datetime.now(timezone.utc)
    recent_start = start - timedelta(days=_RESULTS_LOOKBACK)
    with ThreadPoolExecutor(max_workers=3) as pool:
        official = pool.submit(official_calendar.fetch, start - timedelta(days=1), end + timedelta(days=1),
                               countries, cache_dir=cache_dir)
        vendor = pool.submit(fetch_economic, start, days, tz, countries, min_impact)
        # Skip results collection for a schedule wholly outside the live brief window.
        published = (pool.submit(_published_results, recent_start, now, tz, countries)
                     if recent_start <= now.astimezone(ZoneInfo(tz)).date() <= end else None)
        rows, coverage = official.result()
        try:
            extra = vendor.result()
        except (OSError, ValueError):
            extra = {"state": "unavailable", "items": [], "provider": "Forex Factory",
                     "source_url": "https://www.forexfactory.com/calendar"}
        results, results_state = published.result() if published else ([], "disabled")

    zone, merged = ZoneInfo(tz), {}
    for item in sorted(rows, key=lambda e: (bool(e.get("cached_at")), not e["primary"])):
        if not item["all_day"]:
            moment = datetime.fromisoformat(item["time"]).astimezone(zone)
            item["time"], item["local_date"] = moment.isoformat(), moment.date().isoformat()
        if start.isoformat() <= item["local_date"] <= end.isoformat():
            if _IMPACT_RANK[item["impact"]] >= _IMPACT_RANK[min_impact]:
                merged.setdefault(_merge_key(item), item)
    for row in economic_items(extra):
        key = _merge_key(row)
        if key not in merged:
            merged[key] = {**row, "provider": extra["provider"], "source_url": extra["source_url"]}
        elif row.get("forecast") or row.get("previous"):
            existing = merged[key]
            if not existing.get("estimate_source_url") and existing["title"].casefold() == row["title"].casefold():
                existing.update(estimate_source_url=extra["source_url"], forecast=row["forecast"],
                                previous=row["previous"])

    recent = {}
    for result in results:
        key = _merge_key(result)
        if key in merged:
            existing = merged[key]
            existing.update(actual=result["actual"], actual_source_url=result["actual_source_url"],
                            forecast=result["forecast"], previous=result["previous"],
                            estimate_source_url=result["actual_source_url"])
            if existing["title"].casefold() != result["title"].casefold():
                existing["actual_metric"] = result["title"]
        elif _IMPACT_RANK.get(result["impact"], 0) >= _IMPACT_RANK[min_impact]:
            result["estimate_source_url"] = result["actual_source_url"]
            if result["local_date"] < start.isoformat():
                recent.setdefault(key, result)
            elif result["local_date"] <= end.isoformat():
                merged[key] = result

    items = sorted(merged.values(), key=lambda e: (
        e["local_date"], not e["all_day"], datetime.fromisoformat(e["time"]),
        -_IMPACT_RANK[e["impact"]], e["title"],
    ))
    vendor_only = sorted(set(countries) - {s["country"] for s in coverage})
    coverage.append({**{k: extra[k] for k in ("provider", "source_url", "state")},
                     "country": ", ".join(countries), "covered_through": extra.get("covered_through")})
    state = "partial" if vendor_only or any(s["state"] != "ok" for s in coverage) else "ok"
    if not items and all(s["state"] == "unavailable" for s in coverage):
        state = "partial" if recent else "unavailable"
    return {"state": state, "items": items, "coverage": coverage, "provider": "Economic release tracker",
            "range_end": end.isoformat(), "vendor_only": vendor_only,
            "recent_results": sorted(recent.values(), key=lambda e: datetime.fromisoformat(e["time"]), reverse=True),
            "results_state": results_state,
            "note": ("Some schedules are incomplete or unavailable. Expand calendar coverage for details."
                     if state != "ok" else None)}


def _money(text):
    digits = re.sub(r"[^\d.]", "", text or "")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _eps(text):
    """'$1.16' -> 1.16, '($0.12)' -> -0.12, 'N/A' -> None."""
    if not text or "N/A" in str(text):
        return None
    raw = str(text).strip()
    negative = raw.startswith("(") or raw.startswith("-")
    value = _money(raw)
    return None if value is None else (-value if negative else value)


def prev_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def parse_earnings(payload: dict, day: date, watch: set, limit: int = 10) -> dict:
    rows = ((payload.get("data") or {}).get("rows")) or []
    parsed = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        parsed.append({
            "symbol": symbol, "name": str(row.get("name") or "")[:80],
            "when": {"time-pre-market": "Before open", "time-after-hours": "After close"}.get(
                row.get("time"), "Time not stated"),
            "eps_forecast": row.get("epsForecast") if row.get("epsForecast") not in (None, "", "N/A") else None,
            "estimates": row.get("noOfEsts"), "market_cap": _money(row.get("marketCap")),
            "fiscal_quarter": row.get("fiscalQuarterEnding"), "watch": symbol in watch,
        })
        actual, estimate = _eps(row.get("eps")), _eps(row.get("epsForecast"))
        if actual is not None:
            parsed[-1]["eps_actual"] = row.get("eps")
            if estimate:
                surprise = round((actual - estimate) / abs(estimate) * 100, 1)
                parsed[-1]["eps_surprise_pct"] = surprise
                parsed[-1]["result"] = "beat" if surprise > 0 else "miss" if surprise < 0 else "in line"
    watched = [r for r in parsed if r["watch"]]
    others = sorted((r for r in parsed if not r["watch"]), key=lambda r: -(r["market_cap"] or 0))
    return {"date": day.isoformat(), "total": len(parsed), "rows": watched + others[:limit]}


def fetch_earnings(days: list, watch: set) -> dict:
    out, failures = [], 0
    for day in days:
        try:
            payload = json.loads(get_bytes(_NASDAQ + "?" + urlencode({"date": day.isoformat()}), _HEADERS))
            out.append(parse_earnings(payload, day, watch))
        except Exception as exc:
            failures += 1
            logger.warning("Earnings calendar unavailable for %s (%s)", day, type(exc).__name__)
    if failures == len(days):
        raise OSError("earnings calendar unavailable")
    return {"state": "ok" if not failures else "partial", "days": out, "provider": "Nasdaq earnings calendar (unofficial)",
            "source_url": "https://www.nasdaq.com/market-activity/earnings"}


def parse_auctions(payload: dict, start: date, end: date) -> list:
    rows = []
    for row in payload.get("data") or []:
        try:
            when = date.fromisoformat(row["auction_date"])
        except (KeyError, ValueError, TypeError):
            continue
        if not start <= when < end:
            continue
        amount = _money(row.get("offering_amt"))
        rows.append({"auction_date": when.isoformat(), "type": row.get("security_type"),
                     "term": row.get("security_term"), "reopening": row.get("reopening") == "Yes",
                     "offering": amount, "issue_date": row.get("issue_date"),
                     "cusip": row.get("cusip")})
    order = {"Bill": 2, "Note": 0, "Bond": 0, "TIPS": 1, "FRN": 1}
    rows.sort(key=lambda r: (r["auction_date"], order.get(r["type"], 3), r["term"] or ""))
    return rows


def fetch_auctions(start: date, days: int = 7) -> dict:
    params = {"sort": "-auction_date", "page[size]": 120}
    payload = json.loads(get_bytes(_FISCAL + "?" + urlencode(params), _HEADERS))
    items = parse_auctions(payload, start, start + timedelta(days=days))
    listed = [row.get("auction_date") for row in payload.get("data") or [] if row.get("auction_date")]
    through = max(listed) if listed else None
    note = None
    if not items and through and through < start.isoformat():
        note = f"Treasury's dataset lists no auctions after {through}; check TreasuryDirect for the live schedule."
    return {"state": "partial" if not through or note else "ok", "items": items, "listed_through": through, "note": note,
            "provider": "U.S. Treasury FiscalData",
            "source_url": "https://fiscaldata.treasury.gov/datasets/upcoming-auctions/"}

