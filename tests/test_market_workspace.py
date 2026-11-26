import json
from datetime import date, datetime, timedelta, timezone

from app import create_app
from src import archive, main, profile, snapshot_render
from src.collectors import calendar, capital, macro, official_calendar


def test_profile_save_drives_coverage_and_keeps_the_previous_edition(cfg, monkeypatch):
    cfg.profile_managed = True
    client = create_app(cfg).test_client()
    assert client.get("/settings").status_code == 200
    with client.session_transaction() as session:
        headers = {"X-Run-Token": session["run_token"]}
    start = client.get("/api/profile").json
    assert client.put("/api/profile", json=start).status_code == 403
    assert client.put("/api/profile", json=start, headers={**headers, "Origin": "https://example.net"}).status_code == 403

    start["profile"]["watchlist"] = [{"ticker": "AAPL", "name": "Apple Inc.", "role": "client"}]
    start["profile"]["markets"] = ["americas"]
    saved = client.put("/api/profile", json=start, headers=headers)
    assert saved.status_code == 200
    assert client.put("/api/profile", json=start, headers=headers).status_code == 409
    assert client.get("/api/coverage").json["companies"][0]["ticker"] == "AAPL"

    def fake_snapshot(current):
        assert current.desk_profile["markets"] == ["americas"]
        assert current.coverage.client.ticker == "AAPL"
        return {"sections": [], "timezone": "UTC", "markets": current.desk_profile["markets"],
                "sources": {"quotes": {"state": "ok"}}}

    monkeypatch.setattr(main, "_safe_snapshot", fake_snapshot)
    day = main.today_iso(cfg.settings.timezone)
    main.run_pipeline(cfg, edgar_items=[], price_moves=[], rss_items=[], include_snapshot=True)
    edition = archive.read_payload(cfg.settings.archive_dir, day)
    assert edition["coverage"][0]["ticker"] == "AAPL"
    assert edition["profile_revision"] == saved.json["revision"]

    next_profile = saved.json
    next_profile["profile"]["watchlist"] = []
    assert client.put("/api/profile", json=next_profile, headers=headers).status_code == 200
    response = client.get(f"/brief/{day}")
    assert b"preferences have changed since this edition" in response.data
    assert archive.read_payload(cfg.settings.archive_dir, day)["coverage"][0]["ticker"] == "AAPL"


def test_an_old_metadata_price_does_not_replace_a_newer_bar():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    stamps = [(now - timedelta(days=offset)).timestamp() for offset in (3, 2, 1)]
    result = {
        "meta": {"regularMarketPrice": 95, "regularMarketTime": stamps[0]},
        "timestamp": stamps,
        "indicators": {"quote": [{"close": [95, 100, 102]}]},
    }
    row = macro.build_quote({"symbol": "AAPL"}, result, "https://example.net/chart", now)
    assert row["last"] == 102
    assert row["prev"] == 100
    assert row["chg"] == 2
    assert row["as_of"].startswith("2026-11-20")


def test_missing_next_week_is_reported_as_partial(monkeypatch):
    def response(url, *_args, **_kwargs):
        if "nextweek" in url:
            raise OSError
        return b'[{"date":"2026-11-20T08:30:00-05:00", "country":"USD", "impact":"High", "title":"Example"}]'

    monkeypatch.setattr(calendar, "get_bytes", response)
    cal = calendar.fetch_economic(date(2026, 11, 23), 2, "America/New_York", ["USD"], "High")
    assert cal["items"] == []
    assert cal["state"] == "partial"
    assert cal["covered_through"] == "2026-11-20"


def test_published_actuals_are_sourced_and_only_visible_after_release(monkeypatch):
    now = datetime(2026, 11, 24, 16, tzinfo=timezone.utc)
    day = now.date()

    def reading(event_id, title, stamp, actual, **fields):
        return {"id": event_id, "name": title, "dateline": stamp.timestamp(),
                "impactName": "high", "currency": "USD", "actual": actual,
                "url": f"/calendar?day=nov24.2026#detail={event_id}", **fields}

    release_time = now.replace(hour=13, minute=30)
    rows = [
        reading(41, "Non-Farm Employment Change", release_time, "0K", forecast="10K", previous="-5K"),
        reading(42, "Unemployment Rate", release_time, "4.1%", forecast="4.2%"),
        reading(43, "Flash Manufacturing PMI", now - timedelta(days=1), "50.1"),
        reading(44, "GDP", now + timedelta(hours=1), "2.2%"),
        reading(45, "Retail Sales", now, "1.5%", leaked=True),
        reading(46, "Consumer Confidence", now, "N/A"),
    ]
    data = json.dumps([{"events": rows}])
    page = f"<script>window.calendarComponentStates[1] = {{ days: {data}, other: 1 }};</script>"
    monkeypatch.setattr(calendar, "get_bytes", lambda *_args, **_kwargs: page.encode())
    event = official_calendar._event("Employment Situation", day, release_time.time(), timezone.utc,
                                     "USD", "BLS", "https://example.net/schedule")
    monkeypatch.setattr(official_calendar, "fetch", lambda *_args, **_kwargs: ([event], []))
    monkeypatch.setattr(calendar, "fetch_economic", lambda *_args: {
        "state": "ok", "items": [], "provider": "Vendor", "source_url": "https://example.net/vendor"})
    tracker = calendar.fetch_release_tracker(day, 3, "UTC", ["USD"], "Medium", now=now)

    payrolls, jobless_rate = tracker["items"]
    assert payrolls["provider"] == "BLS"
    assert payrolls["actual"] == "0K"
    assert payrolls["forecast"] == "10K"
    assert payrolls["actual_metric"] == "Non-Farm Employment Change"
    assert payrolls["actual_source_url"] == "https://www.forexfactory.com/calendar?day=nov24.2026#detail=41"
    assert jobless_rate["actual"] == "4.1%"
    assert [e["actual"] for e in tracker["recent_results"]] == ["50.1"]
    snap = {"timezone": "UTC", "brief_date": str(day), "calendar": {"economic": tracker},
            "sections": [{"id": "calendar", "enabled": True}]}
    assert ">Actual<" in snapshot_render.html_block(snap)
    assert "#detail=41" in snapshot_render.html_block(snap)
    assert any("0K" in line for line in snapshot_render.markdown(snap))
    assert "50.1" in snapshot_render.slack_blocks(snap)[1]


def test_credit_spreads_ignore_future_and_missing_values():
    csv = (
        "observation_date,BAMLC0A0CM_20261121,BAMLH0A0HYM2_20261121\n"
        "2026-11-18,0.93,3.47\n"
        "2026-11-19,0.94,3.52\n"
        "2026-11-20,,3.53\n"
        "2026-11-23,1.03,3.53\n"
    )
    result = capital.parse_spreads(csv, date(2026, 11, 21))
    grade, high_yield = result["rows"]
    assert (grade["level_bp"], grade["change_bp"], grade["as_of"]) == (94, 1, "2026-11-19")
    assert (high_yield["level_bp"], high_yield["change_bp"], high_yield["as_of"]) == (353, 1, "2026-11-20")
