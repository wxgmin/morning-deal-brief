"""First-party release schedules and a targeted FRED cross-check for U.S. data."""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from icalendar import Calendar

from ..archive import _atomic_write
from ..util import get_bytes

_BLS = "https://www.bls.gov/schedule/news_release/bls.ics"
_BEA = "https://www.bea.gov/news/schedule"
_CENSUS = "https://www.census.gov/economic-indicators/calendar-listview"
_FED = "https://www.federalreserve.gov/newsevents"
_FRED = "https://fred.stlouisfed.org/releases/calendar"
_EIA = "https://www.eia.gov/petroleum/supply/weekly/schedule.php"
_ISM = "https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar"
_EUROSTAT = "https://ec.europa.eu/eurostat/o/calendars/eventsIcal"
_ECB = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
_ONS = "https://www.ons.gov.uk/releasecalendar"
_BOE = "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates"
_STATCAN = "https://www150.statcan.gc.ca/n1/dai-quo/cal2-eng.htm"
_BOC = "https://www.bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/"
_NY, _CHI, _EU, _UK = map(ZoneInfo, ("America/New_York", "America/Chicago", "Europe/Brussels", "Europe/London"))
_HEADERS = {"User-Agent": "Mozilla/5.0 (Morning Deal Brief)", "Accept": "*/*"}
_HIGH = (
    ("regional", r"metropolitan|state employment|by state|county|regional|industry level|city average"),
    ("payrolls", r"employment situation|non.?farm (?:employment|payroll)|unemployment rate|labou?r force survey"),
    ("cpi", r"\b(?:cpi|hicp)\b|consumer price(?: index| inflation)"),
    ("pce", r"personal income and outlays|\bpce\b|personal consumption expenditure"),
    ("ppi", r"\bppi\b|producer price"),
    ("gdp", r"\bgdp\b|gross domestic product"),
    ("policy", r"fomc|monetary policy|(?:official bank|interest) rate announcement|bank rate|rate decision|mpc announcement"),
    ("retail", r"retail sales|retail and food services|retail trade"),
    ("pmi", r"\bpmi\b|purchasing managers"),
    ("jobs", r"job openings|\bjolts\b|unemployment insurance weekly claims|jobless claims|employment cost index|adp"),
    ("industrial", r"industrial production|capacity utilization"),
    ("trade", r"international trade in goods and services|trade balance"),
)
_FRED_RELEASES = {50: "Payrolls and unemployment", 10: "Consumer prices", 46: "Producer prices",
                  192: "Job openings", 180: "Weekly jobless claims", 194: "ADP employment"}


def _html(url, *, params=None):
    return BeautifulSoup(get_bytes(url, _HEADERS, params=params, limit=3_000_000), "html.parser")


def _tidy(value):
    return " ".join(value.split())


def _day(value, pattern="%B %d, %Y"):
    try:
        return datetime.strptime(_tidy(value), pattern).date()
    except ValueError:
        return None


def _clock(value):
    value = _tidy(value).upper().replace(".", "")
    for pattern in ("%I:%M %p", "%I:%M%p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(value, pattern).time()
        except ValueError:
            continue
    return None


def family(title):
    text = title.lower()
    return next((name for name, pattern in _HIGH if re.search(pattern, text)), "")


def _event(title, day, when, zone, country, provider, url, *, impact=None, period=None):
    stamp = datetime.combine(day, when or time.min, zone)
    label = family(title)
    return {
        "time": stamp.isoformat(), "all_day": when is None, "local_date": day.isoformat(),
        "country": country, "title": _tidy(title)[:200], "period": period,
        "impact": impact or ("High" if label and label != "regional" else "Medium"),
        "provider": provider, "source_url": url, "forecast": "", "previous": "", "primary": not provider.startswith("FRED"),
    }


def _ical(url, zone, country, name, public_url, params=None):
    data = get_bytes(url, _HEADERS, params=params, limit=3_000_000)
    feed = Calendar.from_ical(data)
    rows = []
    for entry in feed.walk("VEVENT"):
        if entry.get("STATUS") == "CANCELLED":
            continue
        title = str(entry.get("SUMMARY") or "")
        if not title or "DTSTART" not in entry:
            continue
        stamp = entry.decoded("DTSTART")
        if isinstance(stamp, datetime):
            stamp = stamp.astimezone(zone) if stamp.tzinfo else stamp.replace(tzinfo=zone)
            day, when = stamp.date(), stamp.time()
        elif isinstance(stamp, date):
            day, when = stamp, None
        else:
            continue
        rows.append(_event(title, day, when, zone, country, name, public_url,
                           impact=None if family(title) else "Low"))
    if not rows:
        raise ValueError("No calendar entries")
    return rows


def _bea(_start, _end):
    url = _BEA + "?full=1"
    soup = _html(url)
    rows = []
    for table in soup.select("table#release-schedule-table"):
        header = table.find("th")
        year = re.search(r"\b20\d{2}\b", header.get_text()) if header else None
        if not year:
            continue
        for row in table.select("tbody tr"):
            day, clock, title = (row.select_one(s) for s in (".release-date", ".scheduled-date small", ".release-title"))
            if not day or not title:
                continue
            stamp = _day(f"{day.get_text()} {year[0]}", "%B %d %Y")
            if stamp:
                rows.append(_event(title.get_text(" "), stamp, _clock(clock.get_text()) if clock else None,
                                   _NY, "USD", "BEA release schedule", url))
    if not rows:
        raise ValueError("No BEA release dates")
    return rows


def _census(start, end):
    rows = []
    current = None
    for year in range(start.year, end.year + 1):
        url = f"{_CENSUS}-{year}.html"
        try:
            soup = _html(url)
        except (OSError, ValueError):
            current = current or _html(f"{_CENSUS}.html")
            soup, url = current, f"{_CENSUS}.html"
        batch = []
        for row in soup.select("table#calendar tr"):
            cells = [_tidy(td.get_text(" ")) for td in row.find_all("td", recursive=False)]
            if len(cells) < 4 or not (day := _day(cells[1])) or day.year != year:
                continue
            batch.append(_event(cells[0], day, _clock(cells[2]), _NY, "USD", "Census Bureau", url, period=cells[3]))
        if not batch:
            raise ValueError("Census schedule did not cover the requested year")
        rows.extend(batch)
    return rows


def _month_pages(start, end):
    month = start.replace(day=1)
    while month <= end:
        yield month
        month = (month + timedelta(days=32)).replace(day=1)


def _fed(start, end):
    rows, ok = [], 0
    for month in _month_pages(start, end):
        url = f"{_FED}/{month:%Y-%B}.htm".lower()
        try:
            soup = _html(url)
        except (OSError, ValueError):
            continue
        found = False
        for row in soup.select(".panel-body .row"):
            timing, event, dates = (row.find("div", class_=k, recursive=False)
                                    for k in ("col-xs-2", "col-xs-7", "col-xs-3"))
            if not timing or not event or not dates:
                continue
            title = event.find("p")
            days = re.fullmatch(r"\d{1,2}(?:\s*,\s*\d{1,2})*", _tidy(dates.get_text(" ")))
            if not title or not days:
                continue
            found = True
            name = _tidy(title.get_text(" "))
            if not name or not re.search(r"FOMC|industrial production|consumer credit|financial accounts|dealer financing", name, re.I):
                continue
            for d in re.findall(r"\d+", days[0]):
                rows.append(_event(name, month.replace(day=int(d)), _clock(timing.get_text(" ")),
                                   _NY, "USD", "Federal Reserve calendar", url))
        ok += found
    if not ok:
        raise ValueError("No Federal Reserve calendar dates")
    return rows, "ok" if ok == len(list(_month_pages(start, end))) else "partial"


def _fred(start, end, rid):
    url = f"{_FRED}?{urlencode({'rid': rid, 'od': 'asc', 'view': 'range', 'vs': start, 've': end})}"
    soup = _html(url)
    table = soup.select_one("#release-dates-pager table")
    if not table:
        # FRED's no-results page uses this text in place of the results table.
        if "no releases" in soup.get_text(" ", strip=True).lower():
            return []
        raise ValueError("Missing FRED release dates")
    rows, day = [], None
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 1:
            cell = _tidy(cells[0].get_text(" ")).removesuffix(" Updated")
            day = _day(cell, "%A %B %d, %Y")
        if len(cells) == 2 and day and cells[1].find("a"):
            rows.append(_event(cells[1].get_text(" "), day, _clock(cells[0].get_text()), _CHI,
                               "USD", "FRED release calendar", url))
    return rows


def _eia(start, end):
    soup = _html(_EIA)
    exceptions = {}
    for row in soup.select("table tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
        if len(cells) == 5 and (week := _day(cells[0])) and (release := _day(cells[1])):
            # The standard release is the Wednesday after the reference Friday.
            exceptions[week + timedelta(days=5)] = (release, _clock(cells[3]))
    if not exceptions:
        raise ValueError("EIA exception schedule unavailable")
    rows, day = [], start - timedelta(days=3)
    while day <= end:
        if day.weekday() == 2:
            release, stamp = exceptions.get(day, (day, time(10, 30)))
            rows.append(_event("EIA Weekly Petroleum Status Report", release, stamp, _NY, "USD",
                               "EIA published release schedule", _EIA, impact="Medium"))
        day += timedelta(days=1)
    return rows


def _ism(start, end):
    rows = []
    for row in _html(_ISM).select("table tr"):
        cells = [_tidy(td.get_text(" ")) for td in row.find_all(["td", "th"], recursive=False)]
        if len(cells) != 3 or not (month := _day(cells[0], "%B %Y")):
            continue
        for title, value in zip(("ISM Manufacturing PMI", "ISM Services PMI"), cells[1:]):
            if match := re.fullmatch(r"(\d{1,2})\s*[*†‡]*", value):
                if day := _day(f"{month:%B} {match[1]}, {month.year}"):
                    rows.append(_event(title, day, time(10), _NY, "USD", "ISM release schedule", _ISM))
    if not rows:
        raise ValueError("No ISM calendar dates")
    published_months = {(date.fromisoformat(row["local_date"]).year, date.fromisoformat(row["local_date"]).month)
                        for row in rows}
    state = "ok" if all((month.year, month.month) in published_months for month in _month_pages(start, end)) else "partial"
    return rows, state


def _ecb(_start, _end):
    rows, dates = [], []
    for node in _html(_ECB).select("dl dt"):
        item = node.find_next_sibling()
        day = _day(node.get_text(), "%d/%m/%Y")
        if day and item and item.name == "dd":
            dates.append(day)
            title = item.get_text(" ", strip=True)
            if "monetary policy" in title and "Day 2" in title:
                rows.append(_event(title, day, None, _EU, "EUR", "ECB Governing Council schedule", _ECB))
    if not dates:
        raise ValueError("No ECB calendar dates")
    return rows


def _ons(_start, end):
    rows, seen = [], set()
    for page in range(1, 13):
        params = {"view": "upcoming", "page": page}
        soup = _html(_ONS, params=params)
        events = soup.select("a[data-gtm-release-date]")
        if not events:
            raise ValueError("ONS calendar did not contain release rows")
        new = False
        for node in events:
            href = node.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            new = True
            day = _day(node["data-gtm-release-date"], "%Y%m%d")
            if not day:
                continue
            if day > end:
                return rows
            title = node.get_text(" ", strip=True)
            rows.append(_event(title, day, _clock(node.get("data-gtm-release-time", "")), _UK, "GBP", "ONS release calendar",
                               "https://www.ons.gov.uk" + href if href.startswith("/") and not href.startswith("//") else _ONS,
                               impact=None if family(title) else "Low"))
        if not new or not soup.select_one(".ons-pagination__item--next a"):
            return rows, "partial" if not new else "ok"
    return rows, "partial"


def _boe(_start, _end):
    rows = []
    for heading in _html(_BOE).find_all(["h2", "h3"]):
        match = re.search(r"(20\d{2}) confirmed dates", heading.get_text(" ", strip=True), re.I)
        if not match:
            continue
        table = heading.find_next("table")
        if not table:
            continue
        for row in table.select("tr"):
            cell = row.find("td")
            if not cell or not (day := _day(f"{cell.get_text(' ', strip=True)} {match[1]}", "%A %d %B %Y")):
                continue
            rows.append(_event("Bank of England MPC announcement", day, None, _UK, "GBP", "Bank of England", _BOE))
    if not rows:
        raise ValueError("No MPC dates")
    return rows


def _statcan(start, end):
    soup = _html(_STATCAN)
    rows, dates = [], []
    for heading in soup.select(".calendar h3"):
        name = _tidy(heading.get_text())
        days = [_day(f"{name}, {year}") for year in range(start.year - 1, end.year + 2)]
        day = min((d for d in days if d), key=lambda d: abs(d - start), default=None)
        if not day:
            continue
        listing = heading.find_next_sibling("ol")
        if listing and listing.find_previous_sibling("h3") == heading:
            dates.append(day)
            for item in listing.select(".rtitle"):
                title = item.get_text(" ", strip=True)
                rows.append(_event(title, day, time(8, 30), _NY, "CAD", "Statistics Canada release schedule",
                                   _STATCAN, impact=None if family(title) else "Low"))
    if not dates:
        raise ValueError("No Statistics Canada dates")
    return rows, "partial" if max(dates) < end - timedelta(days=2) else "ok"


def _boc(_start, _end):
    rows = []
    for caption in _html(_BOC).select("table caption"):
        year = re.search(r"Schedule for (20\d{2})", caption.get_text(" ", strip=True))
        if not year:
            continue
        for row in caption.parent.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) == 2 and (day := _day(f"{cells[0].get_text(' ', strip=True)}, {year[1]}")):
                rows.append(_event("Bank of Canada " + cells[1].get_text(" ", strip=True), day, None, _NY,
                                   "CAD", "Bank of Canada", _BOC))
    if not rows:
        raise ValueError("No Bank of Canada dates")
    return rows


def fetch(start, end, countries, *, cache_dir=None):
    """Fetch each source independently so agency downtime cannot erase the calendar."""
    adapters = {
        "USD": [
            ("BLS", "https://www.bls.gov/schedule/", lambda *_: _ical(_BLS, _NY, "USD", "BLS release schedule", "https://www.bls.gov/schedule/")),
            ("BEA", _BEA, _bea), ("Census Bureau", f"{_CENSUS}.html", _census),
            ("Federal Reserve", f"{_FED}/calendar.htm", _fed), ("EIA", _EIA, _eia), ("ISM", _ISM, _ism),
            *((f"FRED · {label}", f"{_FRED}?rid={rid}", partial(_fred, rid=rid)) for rid, label in _FRED_RELEASES.items()),
        ],
        "EUR": [
            ("Eurostat", "https://ec.europa.eu/eurostat/subscribe/ics.format",
             lambda *_: _ical(_EUROSTAT, _EU, "EUR", "Eurostat", "https://ec.europa.eu/eurostat/news/release-calendar",
                              {"theme": "0", "category": "2"})),
            ("ECB", _ECB, _ecb),
        ],
        "GBP": [("ONS", _ONS, _ons), ("Bank of England", _BOE, _boe)],
        "CAD": [("Statistics Canada", _STATCAN, _statcan), ("Bank of Canada", _BOC, _boc)],
    }
    jobs = [(country, label, url, parse) for country in countries for label, url, parse in adapters.get(country, ())]

    def read(job):
        country, label, url, parse = job
        now = datetime.now(timezone.utc)
        path = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json") if cache_dir else None
        previous = None
        if path:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                age = now - datetime.fromisoformat(saved["fetched_at"])
                overlap = saved["end"] >= start.isoformat() and saved["start"] <= end.isoformat()
                if timedelta(0) <= age < timedelta(days=2) and overlap and isinstance(saved["rows"], list):
                    previous = saved
            except (OSError, ValueError, TypeError, KeyError):
                pass
        try:
            result = parse(start, end)
            rows, status = result if isinstance(result, tuple) else (result, "ok")
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            rows, status = [], "unavailable"
        source = {"country": country, "provider": label, "source_url": url, "state": status}
        if status == "unavailable" and previous:
            source.update(state="cached", fetched_at=previous["fetched_at"], covered_through=previous["end"])
            rows = [{**row, "cached_at": previous["fetched_at"]} for row in previous["rows"]
                    if previous["start"] <= row["local_date"] <= previous["end"]]
        elif path and status == "ok":
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(path, json.dumps({"rows": rows, "fetched_at": now.isoformat(),
                                               "start": start.isoformat(), "end": end.isoformat()}))
            except OSError:
                pass
        return rows, source

    with ThreadPoolExecutor(max_workers=10) as pool:
        batches = list(pool.map(read, jobs))
    return [row for batch, _ in batches for row in batch], [status for _, status in batches]

