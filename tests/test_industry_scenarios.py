"""Banking-desk scenarios exercised through collection, publication and delivery."""
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from app import create_app
from src import archive, deliver, main
from src.collectors import edgar, market, rss
from src.models import RawItem


DAY = "2025-12-01"  # Monday after the Thanksgiving early close
SESSION = "2025-11-28"
ACCESSION = "0000042888-25-000098"
IR_FEED = "https://ir.example.org/releases.rss"
NEWS_FEED = "https://news.example.org/news.rss"


def submissions(*, history=()):
    return {"filings": {"recent": {
        "form": ["8-K"], "filingDate": [SESSION], "accessionNumber": [ACCESSION],
        "primaryDocument": ["report.htm"], "items": ["2.02,9.01"],
        "acceptanceDateTime": ["2025-11-28T18:16:00Z"],
    }, "files": list(history)}}


def rss_feed(*headlines):
    items = "".join(
        f"<item><title>{title}</title><link>https://example.org/story-{i}</link>"
        f"<pubDate>Mon, 01 Dec 2025 11:00:00 GMT</pubDate></item>"
        for i, title in enumerate(headlines)
    )
    return f'<rss version="2.0"><channel><title>Source</title>{items}</channel></rss>'.encode()


def bars():
    sessions = market._session_index("2025-07-01", SESSION)
    prices = [100.0]
    for i in range(1, len(sessions)):
        move = 0.08 if i == len(sessions) - 1 else (0.005 if i % 2 else -0.005)
        prices.append(prices[-1] * (1 + move))
    return pd.DataFrame({
        "Close": prices, "Adjusted": prices, "Volume": range(1000, 1000 + len(prices)),
    }, index=sessions)


def provider_frame(frame, name="Yahoo Finance"):
    return frame, {
        "source_url": "https://data.example.org/history", "provider": name,
        "currency": "USD", "return_basis": "adjusted close",
    }


def test_holiday_monday_brief_sources_a_guidance_cut_to_the_earnings_exhibit(cfg, monkeypatch):
    cfg.coverage.peers = []
    cfg.coverage.client.cik = "0000042888"
    reported = "Graco lowered full-year revenue guidance to $2.0 billion from $2.2 billion."
    primary = b"""
        <p>Item 2.02. Results of Operations and Financial Condition.</p>
        <p>The company issued a press release announcing quarterly results, attached as Exhibit 99.1.</p>
        <p>Item 9.01. Financial Statements and Exhibits.</p>
        <table><tr><td><a href="earnings-ex99.htm">99.1</a></td><td>Earnings release</td></tr></table>
    """
    exhibit = f"<h1>Graco reports third-quarter financial results</h1><p>{reported}</p>".encode()
    replies = {"CIK0000042888.json": json.dumps(submissions()).encode(),
               "report.htm": primary, "earnings-ex99.htm": exhibit}
    fetch = Mock(side_effect=lambda url, **kwargs: replies[url.rsplit("/", 1)[1]])
    monkeypatch.setattr(edgar, "_sec_bytes", fetch)
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, moves = main.run_pipeline(cfg, as_of_date=DAY)
    first = items[0]
    assert first.event_type == "guidance_change"
    assert first.summary == reported
    assert first.figures == ["$2.0 billion", "$2.2 billion"]
    assert first.source_url.endswith("/earnings-ex99.htm")
    assert first.evidence["index_url"].endswith("-index.html")
    assert moves[0].date == SESSION and moves[0].pct_change == pytest.approx(8)
    assert any(item.event_type == "large_price_move" for item in items)
    assert archive.read_payload(cfg.settings.archive_dir, DAY)["outcome"] == "complete"
    web = create_app(cfg).test_client()
    assert b"earnings-ex99.htm" in web.get(f"/brief/{DAY}").data
    assert b"$2.0 billion" in web.get(f"/brief/{DAY}/download/md").data


def test_unavailable_earnings_exhibit_keeps_filing_and_flags_incomplete_coverage(cfg, monkeypatch):
    cfg.coverage.peers = []
    cfg.coverage.client.cik = "0000042888"
    def read_sec(url, **kwargs):
        if url.endswith(".json"):
            return json.dumps(submissions()).encode()
        if url.endswith("report.htm"):
            return b'<p>Item 2.02. The release is attached as Exhibit 99.1.</p><p><a href="annex.htm">99.1</a></p>'
        raise OSError("exhibit temporarily unavailable")
    monkeypatch.setattr(edgar, "_sec_bytes", read_sec)
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, _ = main.run_pipeline(cfg, as_of_date=DAY)
    filing = next(item for item in items if item.source == "edgar")
    assert filing.source_url.endswith("/report.htm")
    assert filing.event_type == "earnings"
    payload = archive.read_payload(cfg.settings.archive_dir, DAY)
    assert payload["sources"]["filings"]["issuers"] == {"GGG": "partial"}


def test_earnings_and_new_buyback_in_same_release_survive_deduplication(cfg, monkeypatch):
    cfg.coverage.peers = []
    cfg.coverage.client.cik = "0000042888"
    income = "Graco reported net income of $120 million, or $0.75 per diluted share."
    buyback = "The board authorized a $2 billion increase to the company's share repurchase program."
    primary = b'<p>Item 2.02. The release is attached as Exhibit 99.1.</p><p><a href="earnings.htm">99.1</a></p>'
    exhibit = (f"<p>{income}</p><p>{buyback}</p><h2>Forward-Looking Statements</h2>"
               "<p>The company undertakes no obligation to update its outlook or guidance.</p>").encode()
    replies = {"CIK0000042888.json": json.dumps(submissions()).encode(),
               "report.htm": primary, "earnings.htm": exhibit}
    monkeypatch.setattr(edgar, "_sec_bytes", lambda url, **kw: replies[url.rsplit("/", 1)[1]])
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, _ = main.run_pipeline(cfg, as_of_date=DAY)
    stories = [item for item in items if item.evidence["accession"] == ACCESSION]
    assert [(item.event_type, item.summary) for item in stories] == [("financing", buyback), ("earnings", income)]
    assert all(item.source_url.endswith("/earnings.htm") and item.evidence["exhibit"] == "99.1" for item in stories)


def test_note_purchase_preserves_conditions_in_a_long_filing_paragraph(cfg, monkeypatch):
    paragraph = (
        "Graco entered into a Note Purchase Agreement with institutional investors. Pursuant to that agreement, "
        "it may issue up to $1.5 billion in aggregate principal amount of senior unsecured notes over time. "
        "Subject to customary closing conditions, the company agreed to sell $250 million of the notes, "
        "consisting of (i) $125 million in principal amount of its 5.93% Series A notes due 2032, "
        "(ii) $75 million in principal amount of its 6.10% Series B notes due 2034, and (iii) $50 million "
        "in principal amount of its 6.23% Series C notes due 2037. For additional clarity, the senior "
        "unsecured notes will be guaranteed by subsidiaries that guarantee the company's borrowed money "
        "indebtedness and the debt obligations under the company's revolving credit facilities. "
        "The sale of the initial series is expected to close in January, subject to the purchasers' "
        "receipt of a customary legal opinion and the accuracy of the company's representations and warranties."
    )
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/filing.htm", SESSION, form="8-K",
                     extra={"accepted_at": "2025-11-28T18:16:00Z", "text":
                            f"Item 1.01. Entry into a Material Definitive Agreement.\n{paragraph}\n"
                            "Item 2.03. Creation of a Direct Financial Obligation.\n"
                            "The information in Item 1.01 is incorporated herein by reference."})
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))
    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing])
    assert items[0].event_type == "financing"
    assert items[0].summary == paragraph
    assert {"$1.5 billion", "$250 million", "6.10%"} <= set(items[0].figures)


def test_officer_retirement_uses_the_disclosure_instead_of_the_form_heading(cfg, monkeypatch):
    reported = ("On November 28, an executive vice president provided notification of her retirement from "
                "such position, effective immediately, and agreed to serve in an advisory role through May 2027.")
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/filing.htm", SESSION, form="8-K",
                     extra={"accepted_at": "2025-11-28T18:16:00Z", "text":
                            "Item 5.02.\nDeparture of Directors or Certain Officers; Election of Directors; "
                            "Appointment of Certain Officers; Compensatory Arrangements of Certain Officers.\n"
                            f"{reported}"})
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))
    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing])
    personnel = next(item for item in items if item.event_type == "management_change")
    assert personnel.summary == reported


def test_sec_history_outage_does_not_discard_fresh_filings(cfg, monkeypatch):
    client = cfg.coverage.client
    client.cik = "0000042888"
    data = submissions(history=[{"name": "CIK0000042888-submissions-001.json",
                                 "filingFrom": "2025-01-01", "filingTo": SESSION}])
    def read_sec(url, **kwargs):
        if url.endswith("001.json"):
            raise OSError("historical filings temporarily unavailable")
        return json.dumps(data).encode()
    monkeypatch.setattr(edgar, "_sec_bytes", read_sec)
    status = {}
    items = edgar.collect_filings([client], SESSION, DAY, status=status)
    assert [item.accession for item in items] == [ACCESSION]
    assert status == {"GGG": "partial"}


def test_issuer_release_uses_its_configured_ticker_when_headline_names_a_peer(cfg, monkeypatch):
    cfg.coverage.rss_feeds = [
        {"url": IR_FEED, "type": "press", "ticker": "GGG"},
        {"url": NEWS_FEED, "type": "news"},
    ]
    releases = rss_feed("Signs merger agreement to acquire IDEX Corporation")
    news = rss_feed("Nordson announces acquisition of Apex Components")
    monkeypatch.setattr(rss, "get_bytes", lambda url, *a, **kw: releases if url == IR_FEED else news)
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))
    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[])
    deals = [item for item in items if item.event_type == "M&A"]
    assert [(item.ticker, item.role) for item in deals] == [("GGG", "client"), ("NDSN", "peer")]
    assert deals[0].evidence["feed"] == IR_FEED


def test_market_failover_uses_one_provider_when_primary_baseline_has_a_gap(cfg, monkeypatch):
    complete = bars()
    primary = complete.drop(complete.index[-4])
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(primary))
    monkeypatch.setattr(market, "_download_alpaca", lambda *args: provider_frame(complete, "Alpaca SIP"))
    cfg.settings.market_fallback = "alpaca"

    _, moves = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[])
    assert all(move.evidence["provider"] == "Alpaca SIP" for move in moves)
    assert all(move.z_score is not None and move.volume_z is not None for move in moves)
    assert archive.read_payload(cfg.settings.archive_dir, DAY)["outcome"] == "complete"


def test_syndicated_deal_reports_collapse_but_distinct_targets_survive(cfg, monkeypatch):
    first = "Graco announces acquisition of Apex Components for $50 million"
    second = "Graco announces acquisition of Northstar Pumps for $75 million"
    cfg.coverage.rss_feeds = [{"url": feed, "type": "news"} for feed in (NEWS_FEED, IR_FEED)]
    monkeypatch.setattr(rss, "get_bytes", lambda url, *a, **kw:
                        rss_feed(first, second) if url == NEWS_FEED else
                        rss_feed(first).replace(b"example.org/story", b"wire.example.org/reprint"))
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))
    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[])
    deals = [item for item in items if item.event_type == "M&A"]
    assert {item.summary for item in deals} == {first, second}
    assert len(next(item for item in deals if item.summary == first).related_sources) == 1


def test_required_complete_delivery_retries_source_outage_then_sends_once(cfg, monkeypatch):
    cfg.coverage.peers = []
    cfg.settings.require_complete = True
    cfg.settings.delivery = "both"
    cfg.settings.slack_webhook_url = "https://hooks.slack.com/services/example"
    cfg.settings.smtp_host = "smtp.example.org"
    cfg.settings.email_recipients = ["desk@example.org"]
    send_slack = Mock()
    send_email = Mock(return_value={})
    monkeypatch.setattr(deliver, "send_slack", send_slack)
    monkeypatch.setattr(deliver, "send_email", send_email)
    download = Mock(side_effect=[OSError("provider offline"), provider_frame(bars())])
    monkeypatch.setattr(market, "_download", download)

    with pytest.raises(RuntimeError, match="coverage is incomplete"):
        main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[], send=True)
    assert archive.read_payload(cfg.settings.archive_dir, DAY)["outcome"] == "partial"
    assert deliver.read_receipt(cfg, DAY) is None
    send_slack.assert_not_called()
    send_email.assert_not_called()

    main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[], send=True)
    main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[], send=True)
    assert send_slack.call_count == 1
    assert send_email.call_count == 1
    edition = archive.read_payload(cfg.settings.archive_dir, DAY)
    receipt = deliver.read_receipt(cfg, DAY)
    assert receipt["revision"] == edition["revision"]
    assert receipt["channels"] == {"slack": "accepted", "email": {"desk@example.org": "accepted"}}
    assert send_email.call_args.args[4] == (archive.artifacts(cfg.settings.archive_dir, DAY, edition)
                                           / "brief.html").read_text(encoding="utf-8")
    assert download.call_count == 2


def test_early_close_is_visible_to_a_desk_before_the_normal_close():
    cutoff = datetime(2025, 11, 28, 19, 0, tzinfo=timezone.utc)
    assert market.expected_session("2025-11-29", cutoff) == SESSION


def test_one_8k_publishes_refinancing_buyback_guidance_and_cfo_departure(cfg, monkeypatch):
    cfg.coverage.peers = []
    cfg.coverage.client.cik = "0000042888"
    debt = "Graco entered into a $400 million revolving credit agreement to refinance existing borrowings."
    buyback = "The board authorized a $200 million increase to Graco's share repurchase program."
    guidance = "Graco lowered its full-year revenue outlook to between $2.0 billion and $2.2 billion."
    departure = "Graco's CFO resigned on November 28 and will remain in an advisory role until March."
    primary = (
        "<p>Item 2.02. Graco announces results in the attached Exhibit 99.1.</p>"
        f"<p>Item 2.03. Creation of a Direct Financial Obligation.</p><p>{debt}</p>"
        f"<p>Item 5.02. Departure of Directors or Certain Officers.</p><p>{departure}</p>"
        '<p>Item 9.01. Financial Statements and Exhibits.</p><a href="results.htm">99.1</a>'
    ).encode()
    replies = {"CIK0000042888.json": json.dumps(submissions()).encode(),
               "report.htm": primary, "results.htm": f"<p>{guidance}</p><p>{buyback}</p>".encode()}
    monkeypatch.setattr(edgar, "_sec_bytes", lambda url, **kw: replies[url.rsplit("/", 1)[1]])
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, _ = main.run_pipeline(cfg, as_of_date=DAY)
    events = {item.summary: item for item in items if item.source == "edgar"}
    assert {summary: item.event_type for summary, item in events.items()} == {
        debt: "financing", buyback: "financing", guidance: "guidance_change", departure: "management_change",
    }
    assert events[guidance].source_url.endswith("/results.htm")
    assert events[buyback].source_url.endswith("/results.htm")
    assert events[debt].source_url.endswith("/report.htm")


@pytest.mark.parametrize(("section", "label", "reported"), [
    ("1.03", "bankruptcy", "On November 28, Graco filed voluntary petitions for relief under Chapter 11 of the U.S. Bankruptcy Code."),
    ("2.06", "impairment", "Graco concluded it expects to recognize a non-cash goodwill impairment charge of approximately $180 million."),
    ("4.02", "financial_reporting", "Graco determined its previously issued financial statements for the first two quarters should no longer be relied upon."),
])
def test_distressed_or_accounting_disclosures_are_actionable(cfg, monkeypatch, section, label, reported):
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/filing.htm", SESSION, form="8-K",
                     extra={"accepted_at": "2025-11-28T18:16:00Z", "text": f"Item {section}.\n{reported}"})
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing])
    event = next(item for item in items if item.source == "edgar")
    assert event.event_type == label
    assert event.summary == reported
    assert event.source_url.endswith("/filing.htm")


def test_debt_acceleration_quotes_the_notice_and_qualification(cfg):
    disclosure = ("Graco received a notice of acceleration for $125 million of outstanding borrowings "
                  "following a breach of a financial covenant and is disputing the notice.")
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/filing.htm", SESSION, form="8-K", extra={
                         "accepted_at": "2025-11-28T18:16:00Z", "text": f"Item 2.04.\n{disclosure}",
                     })

    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing], price_moves=[])
    event, = items
    assert event.event_type == "financing"
    assert event.summary == disclosure
    assert event.figures == ["$125 million"]


def test_long_impairment_disclosure_keeps_the_range_and_non_cash_qualification(cfg, monkeypatch):
    disclosure = (
        "Graco's audit committee concluded a material impairment was required. "
        + "The joint venture continues to assess operating conditions and competitive pressures. " * 18
        + "Graco expects to record an impairment of $2.6–2.9 billion and additional losses of about $2.7 billion. "
        "The charges are expected to be non-cash."
    )
    assert len(disclosure) > 1500
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/filing.htm", SESSION, form="8-K", extra={
                         "accepted_at": "2025-11-28T18:16:00Z", "text": f"Item 2.06.\n{disclosure}",
                     })

    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing], price_moves=[])
    event, = items
    assert event.summary == disclosure
    assert event.figures == ["$2.6–2.9 billion", "$2.7 billion"]
    post = Mock(return_value=Mock(status_code=200, text="ok"))
    monkeypatch.setattr(deliver.requests, "post", post)
    deliver.send_slack("https://hooks.slack.com/services/example", DAY, items, [])
    message = post.call_args.kwargs["json"]
    assert disclosure in message["text"]
    assert event.source_url in message["text"]
    assert any(disclosure in block.get("text", {}).get("text", "") for block in message["blocks"])


def test_referenced_but_unlinked_exhibit_marks_sec_coverage_partial(cfg, monkeypatch):
    cfg.coverage.peers = []
    cfg.coverage.client.cik = "0000042888"
    replies = {
        "CIK0000042888.json": json.dumps(submissions()).encode(),
        "report.htm": b"<p>Item 2.02. Graco furnishes its results in Exhibit 99.1.</p><p>99.1 Earnings release</p>",
    }
    monkeypatch.setattr(edgar, "_sec_bytes", lambda url, **kw: replies[url.rsplit("/", 1)[1]])
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, _ = main.run_pipeline(cfg, as_of_date=DAY)
    assert next(item for item in items if item.source == "edgar").source_url.endswith("/report.htm")
    assert archive.read_payload(cfg.settings.archive_dir, DAY)["sources"]["filings"]["state"] == "partial"


def test_malformed_sec_accession_keeps_other_filings_and_marks_partial(cfg, monkeypatch):
    company = cfg.coverage.client
    company.cik = "0000042888"
    data = submissions()
    recent = data["filings"]["recent"]
    for values in recent.values():
        values.insert(0, values[0])
    recent["accessionNumber"][0] = "malformed vendor record"
    monkeypatch.setattr(edgar, "_sec_bytes", lambda *a, **kw: json.dumps(data).encode())

    status = {}
    filings = edgar.collect_filings([company], SESSION, DAY, status=status)
    assert [item.accession for item in filings] == [ACCESSION]
    assert status == {"GGG": "partial"}


@pytest.mark.parametrize(("missing", "has_return", "has_volume"), [
    ("Volume", True, False),
    ("Adjusted", False, True),
])
def test_incomplete_vendor_bar_keeps_independently_supported_values(
    cfg, monkeypatch, missing, has_return, has_volume
):
    cfg.coverage.peers = []
    frame = bars()
    frame.loc[SESSION, missing] = float("nan")
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(frame))

    items, moves = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[])
    move, = moves
    assert move.close == frame.loc[SESSION, "Close"]
    assert (move.pct_change is not None) is has_return
    assert (move.z_score is not None) is has_return
    assert (move.volume is not None) is has_volume
    assert (move.volume_z is not None) is has_volume
    assert any(item.event_type == "large_price_move" for item in items) is has_return
    published = archive.read_payload(cfg.settings.archive_dir, DAY)
    assert published["sources"]["market"]["state"] == "partial"
    assert published["price_moves"][0]["evidence"]["source_url"] == "https://data.example.org/history"
    response = create_app(cfg).test_client().get(f"/brief/{DAY}")
    assert response.status_code == 200
    assert (b"1 of 1 scored" in response.data) is has_return


def test_agency_affirmation_is_distinct_from_a_credit_downgrade(cfg, monkeypatch):
    cfg.coverage.rss_feeds = [{"url": NEWS_FEED, "type": "news"}]
    monkeypatch.setattr(rss, "get_bytes", lambda *a, **kw: rss_feed(
        "Fitch affirms Graco's BBB credit rating and maintains a stable outlook",
        "S&P Global Ratings lowers Nordson to BBB- from BBB on weaker cash flow".replace("&", "&amp;"),
    ))
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))

    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[])
    reviews = [item for item in items if item.event_type == "rating_change"]
    assert [(item.ticker, item.summary) for item in reviews] == [
        ("NDSN", "S&P Global Ratings lowers Nordson to BBB- from BBB on weaker cash flow"),
    ]


def test_early_collection_is_labeled_preview_until_the_desk_cutoff(cfg, monkeypatch):
    class Clock(datetime):
        stamp = datetime.fromisoformat("2025-12-01T12:00:00+00:00")

        @classmethod
        def now(cls, tz=None):
            return cls.stamp.astimezone(tz)

    monkeypatch.setattr(main, "datetime", Clock)
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 10-Q.",
                     "https://www.sec.gov/report.htm", DAY, form="10-Q",
                     extra={"accepted_at": "2025-12-01T12:30:00Z"})
    web = create_app(cfg).test_client()

    preview, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing])
    assert not any(item.source == "edgar" for item in preview)
    payload = archive.read_payload(cfg.settings.archive_dir, DAY)
    assert payload["outcome"] == "complete" and payload["is_preview"]
    assert payload["scheduled_cutoff"] == "2025-12-01T12:45:00+00:00"
    for path in ("", "/download/html", "/download/md"):
        assert b"Pre-cutoff preview" in web.get(f"/brief/{DAY}{path}").data

    Clock.stamp = datetime.fromisoformat(payload["scheduled_cutoff"])
    final, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing])
    assert any(item.source == "edgar" for item in final)
    assert b"Pre-cutoff preview" not in web.get(f"/brief/{DAY}").data


def test_missed_desk_editions_recover_filings_and_send_one_brief(cfg, monkeypatch):
    for day, mode, outcome, preview in (
        ("2025-11-26", "live", "complete", False),
        ("2025-11-27", "live", "unavailable", False),
        ("2025-11-28", "live", "complete", True),
    ):
        _, cutoff = main.briefing_window(cfg, day)
        archive.save_brief(cfg, day, "", "", {
            "date": day, "mode": mode, "outcome": outcome,
            "is_preview": preview, "cutoff": cutoff.isoformat(),
        })

    def filings(companies, start, end, *, status):
        assert (start, end) == ("2025-11-26", DAY)
        status.update({cfg.coverage.client.ticker: "ok"})
        return [
            RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                    f"https://www.sec.gov/report-{i}.htm", stamp[:10], form="8-K",
                    accession=f"0000042888-25-{i:06}",
                    extra={"accepted_at": stamp, "text": f"Item 2.06. Graco recorded an impairment charge of ${amount} million."})
            for i, (stamp, amount) in enumerate((
                ("2025-11-26T12:45:00Z", 50),
                ("2025-11-26T22:00:00Z", 90),
                ("2025-12-01T12:45:01Z", 100),
            ))
        ]

    cfg.coverage.peers = []
    cfg.coverage.rss_feeds = [{"url": NEWS_FEED}]
    cfg.settings.delivery = "slack"
    cfg.settings.slack_webhook_url = "https://hooks.slack.com/services/example"
    monkeypatch.setattr(edgar, "collect_filings", filings)
    monkeypatch.setattr(market, "_download", lambda *args: provider_frame(bars()))
    monkeypatch.setattr(rss, "get_bytes", lambda *args, **kwargs: rss_feed())
    post = Mock(return_value=Mock(status_code=200, text="ok"))
    monkeypatch.setattr(deliver.requests, "post", post)

    for _ in range(2):
        stories, moves = main.run_pipeline(cfg, as_of_date=DAY, send=True, scheduled=True)
        assert [item.figures for item in stories if item.source == "edgar"] == [["$90 million"]]
        assert {move.date for move in moves} == {SESSION}
    post.assert_called_once()
    payload = archive.read_payload(cfg.settings.archive_dir, DAY)
    assert payload["window_start"] == "2025-11-26T12:45:00+00:00"
    assert payload["recovery"] == {"last_finalized_date": "2025-11-26", "omitted_older_windows": False}
    assert "News coverage depends on what the feeds still retain." in post.call_args.kwargs["json"]["text"]
    web = create_app(cfg).test_client()
    for route in ("", "/download/html", "/download/md"):
        assert b"Expanded reporting window" in web.get(f"/brief/{DAY}{route}").data

    window, recovery = main._live_window(cfg, "2025-12-02", datetime.now(timezone.utc))
    assert window[0].isoformat() == payload["cutoff"]
    assert recovery is None


def test_long_outage_recovery_limit_and_initial_start(cfg):
    now = datetime(2025, 12, 1, 13, tzinfo=timezone.utc)
    assert main._live_window(cfg, DAY, now) == (main.briefing_window(cfg, DAY), None)
    previous = "2025-11-14"
    archive.save_brief(cfg, previous, "", "", {
        "date": previous, "mode": "live", "outcome": "complete",
        "cutoff": main.briefing_window(cfg, previous)[1].isoformat(),
    })
    window, recovery = main._live_window(cfg, DAY, now)
    assert window[0] == main.briefing_window(cfg, "2025-11-21")[1]
    assert recovery == {"last_finalized_date": previous, "omitted_older_windows": True}
    assert "Older missed windows" in main.brief.recovery_note({"recovery": recovery})


@pytest.mark.parametrize(("section", "title", "statement", "qualification"), [
    ("2.04", "Triggering Events That Accelerate or Increase a Direct Financial Obligation "
             "or an Obligation under an Off-Balance Sheet Arrangement",
     "A lender sent a notice of acceleration covering $150 million of term loans.",
     "The company disputes the notice and has obtained a temporary waiver."),
    ("2.06", "Material Impairments",
     "The company expects a goodwill impairment charge of $200 million.",
     "The estimate is preliminary and the company expects no related cash expenditures."),
    ("4.02", "Non-Reliance on Previously Issued Financial Statements or a Related Audit Report "
             "or Completed Interim Review",
     "The audit committee determined that the financial statements should no longer be relied upon.",
     "The determination only applies to the interim period ended June 30, 2025."),
])
def test_risk_disclosures_keep_the_qualifying_paragraph(cfg, section, title, statement, qualification):
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/risk.htm", SESSION, form="8-K", extra={
                         "accepted_at": "2025-11-28T18:16:00Z",
                         "text": (f"Item {section}. {title}.\n{statement}\n{qualification}\n"
                                  "Cautionary Note on Forward-Looking Statements\n"
                                  + "Risk factors may affect actual results. " * 80
                                  + "\nSIGNATURES\nGraco Inc."),
                     })
    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing], price_moves=[])
    item, = items
    assert item.summary == f"{statement} {qualification}"
    assert item.source_url.endswith("/risk.htm")


def test_oversized_risk_disclosure_uses_filing_metadata(cfg):
    filing = RawItem("edgar", "Graco Inc.", "GGG", "client", "Graco filed Form 8-K.",
                     "https://www.sec.gov/risk.htm", SESSION, form="8-K", extra={
                         "accepted_at": "2025-11-28T18:16:00Z",
                         "text": ("Item 2.06. Material Impairments.\n"
                                  "The company expects a goodwill impairment of $280 million.\n"
                                  + "The company continues to assess the value of its assets.\n" * 45
                                  + "The estimate affects only a divested subsidiary."),
                     })
    items, _ = main.run_pipeline(cfg, as_of_date=DAY, edgar_items=[filing], price_moves=[])
    item, = items
    assert item.summary == f"Graco filed Form 8-K. SEC filing date: {SESSION}."
    assert "$280 million" not in item.figures
