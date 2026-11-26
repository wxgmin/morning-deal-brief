from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app import create_app
from src import archive, dedupe, deliver, main, rank
from src.collectors import edgar, market
from src.events import detect
from src.guardrails import verify_grounding
from src.llm import core, providers
from src.models import BriefItem, Event, RawItem


def raw(title="Revenue was $500M.", *, url="https://example.org/story", source="rss", **kwargs):
    fields = dict(company="Graco Inc.", ticker="GGG", role="client", published="2026-11-10")
    fields.update(kwargs)
    return RawItem(source=source, title=title, url=url, **fields)


def test_model_prose_cannot_publish_and_a_broken_provider_trips_once(cfg, monkeypatch):
    cfg.settings.llm_provider = "local"
    provider = SimpleNamespace(name="local", chat_json=Mock(return_value={
        "evidence": 0, "follow_up": 0, "summary": "Net debt was $500M.",
    }))
    cfg.settings.llm_model = "mock"
    monkeypatch.setattr(core, "get_provider", lambda _: provider)
    state = {}
    event = Event(raw(), "earnings", source_text="Revenue was $500M.")
    for _ in range(2):
        judgment = core.judgment_for_event(cfg, event, event.source_text, cfg.coverage, state=state)
        assert judgment.summary == event.source_text and judgment.method == "deterministic"
        assert verify_grounding(judgment.summary, judgment.why_it_matters, judgment.figures_cited,
                                event.source_text)[0]
    assert provider.chat_json.call_count == 1


def test_model_can_choose_intact_evidence_and_followup(cfg, monkeypatch):
    cfg.settings.llm_provider = "local"
    cfg.settings.llm_model = "mock"
    provider = SimpleNamespace(name="local", chat_json=lambda *args: {"evidence": 1, "follow_up": 1})
    monkeypatch.setattr(core, "get_provider", lambda _: provider)
    event = Event(raw(), "earnings")
    text = "Revenue was $500M.\nSales fell 8% in Europe."
    judgment = core.judgment_for_event(cfg, event, text, cfg.coverage)
    assert judgment.summary == "Sales fell 8% in Europe."
    assert judgment.figures_cited == ["8%"]
    assert judgment.method == "model selection"


def test_openrouter_never_reuses_the_openai_key(cfg, monkeypatch):
    cfg.settings.llm_provider = "openrouter"
    cfg.settings.openai_api_key = "test-openai-key"
    client = Mock()
    monkeypatch.setattr(providers, "OpenAICompatProvider", client)
    assert isinstance(providers.get_provider(cfg), providers.NoneProvider)
    client.assert_not_called()
    cfg.settings.openrouter_api_key = "test-openrouter-key"
    providers.get_provider(cfg)
    client.assert_called_once_with("openrouter", "test-openrouter-key", base_url=providers.OPENROUTER_BASE)


def test_unsafe_or_missing_url_cannot_be_published():
    for url in ("", "javascript:alert(0)", r"https://host.invalid\path", "https://user@host.invalid/"):
        event = Event(raw(url=url), "earnings", source_text="Revenue was $500M.")
        assert not core.fallback_judgment(event).grounded


def test_ownership_forms_and_cover_page_are_classified_without_imputing_intent():
    assert {"8-K/A", "10-K/A", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13G/A"} <= edgar.ALLOWED_FORMS
    passive = raw(source="edgar", form="SCHEDULE 13G/A")
    assert detect._detect_edgar(passive).event_type == "ownership"
    content = "Principal executive offices\n" + "Cover page filler.\n" * 140
    content += "\nItem 8.01. Other Events.\nThe Company agreed to an accelerated share repurchase of $600 million."
    event = detect._detect_edgar(raw(source="edgar", form="8-K", extra={"text": content}))
    assert event.event_type == "financing"
    assert "$600 million" in event.source_text
    assert "executive offices" not in event.source_text


def test_distinct_acquisitions_survive_and_client_financing_leads(cfg):
    originals = [Event(raw(f"Graco announces acquisition of {name}", url=f"https://example.org/{name}"),
                       "M&A", confidence=0.8) for name in ("Acme", "Beta")]
    market_event = Event(raw("GGG rallies", source="market", role="peer"), "large_price_move",
                         confidence=0.8, magnitude=10.0)
    assert len(dedupe.dedupe(originals)) == 2
    assert rank.rank_events([market_event, originals[0]], cfg)[0] == originals[0]


def test_acceptance_cutoff_weekend_and_dst(cfg):
    start, cutoff = main.briefing_window(cfg, "2026-11-02")
    assert start == datetime(2026, 10, 30, 11, 45, tzinfo=timezone.utc)
    assert cutoff == datetime(2026, 11, 2, 12, 45, tzinfo=timezone.utc)
    candidates = [raw(extra={"accepted_at": stamp}) for stamp in (
        "2026-10-31T17:00:00Z", "2026-11-02T12:45:00Z",
        "2026-11-02T12:45:01Z", "", "2026-10-30T11:45:00Z",
    )]
    published, unknown = main._items_in_window(candidates, start, cutoff, "accepted_at")
    assert published == candidates[:2]
    assert unknown == 1
    assert market.expected_session("2026-11-27") == "2026-11-25"
    # A before-close UTC cutoff is possible for a desk very far ahead of New York.
    assert market.expected_session("2026-11-06", datetime(2026, 11, 5, 18, tzinfo=timezone.utc)) == "2026-11-04"


def test_scheduled_entry_point_is_weekday_and_zone_aware(cfg, monkeypatch):
    worker = Mock()
    monkeypatch.setattr(main, "run_pipeline", worker)
    for stamp in ("2026-11-02T12:44:59+00:00", "2026-11-02T13:00:00+00:00", "2026-11-01T12:46:00+00:00"):
        main._run_scheduled(cfg, datetime.fromisoformat(stamp))
    worker.assert_not_called()
    main._run_scheduled(cfg, datetime.fromisoformat("2026-11-02T12:46:00+00:00"))
    worker.assert_called_once_with(cfg, as_of_date="2026-11-02", send=True, scheduled=True)


def test_z_score_uses_only_prior_sessions_and_preserves_input_prices(cfg):
    days = market._session_index("2026-07-01", "2026-11-06")[-62:]
    prices = [100.0]
    for i in range(60):
        prices.append(prices[-1] * (1.01 if i % 2 else 0.99))
    prices.append(prices[-1] * 1.2)
    frame = pd.DataFrame({"Close": prices, "Adjusted": prices, "Volume": range(1000, 1062)}, index=days)
    date_str = days[-1].date().isoformat()
    moves = market._moves(cfg.coverage.client, frame, {"source_url": "https://example.org"},
                          days[0].date().isoformat(), date_str, date_str)
    move = moves[date_str]
    assert move.z_score == pytest.approx(20)
    assert move.evidence["previous_adjusted_close"] == prices[-2]
    assert move.large_move


def test_missing_latest_session_does_not_silently_use_stale_prices(cfg, monkeypatch):
    observed = Mock(return_value={"2026-11-04": [object()]})
    monkeypatch.setattr(market, "collect_market_series", observed)
    assert market.collect_market(cfg.coverage.companies, "2026-11-06") == []
    assert observed.call_args.args[1:3] == ("2026-11-05", "2026-11-05")


def test_no_enabled_sources_is_an_archived_failure(cfg, monkeypatch):
    monkeypatch.setattr(edgar, "collect_filings", lambda *a, **kw: [])
    monkeypatch.setattr(market, "collect_market", lambda *a, **kw: [])
    monkeypatch.setattr(market, "collect_market_series", lambda *a, **kw: {})
    with pytest.raises(RuntimeError, match="No source"):
        main.run_pipeline(cfg, as_of_date="2026-11-10", include_news=False)
    with pytest.raises(RuntimeError, match="unavailable"):
        main._run_backfill(cfg, 1, "2026-11-11")
    payload = archive.read_payload(cfg.settings.archive_dir, "2026-11-10")
    assert payload["outcome"] == "unavailable"
    assert payload["sources"]["market"]["issuers"]["GGG"] == "unavailable"


def test_receipts_retry_only_refused_recipients(cfg, monkeypatch):
    cfg.settings.delivery = "both"
    cfg.settings.smtp_host = "smtp.example.org"
    cfg.settings.slack_webhook_url = "https://hooks.slack.com/services/example"
    cfg.settings.email_recipients = ["one@example.org", "two@example.org"]
    slack = Mock()
    mail = Mock(side_effect=[{"two@example.org": (450, b"temporarily refused")}, {}])
    monkeypatch.setattr(deliver, "send_slack", slack)
    monkeypatch.setattr(deliver, "send_email", mail)
    day = "2026-11-10"
    meta = {"date": day}
    archive.save_brief(cfg, day, "markdown", "html", meta)
    meta = archive.read_payload(cfg.settings.archive_dir, day)
    with pytest.raises(RuntimeError, match="rejected 1 recipient"):
        deliver.deliver(cfg, day, "markdown", "html", [], [], meta)
    deliver.deliver(cfg, day, "markdown", "html", [], [], meta)
    assert slack.call_count == 1
    assert mail.call_args.args[1] == ["two@example.org"]
    assert set(deliver.read_receipt(cfg, day)["channels"]["email"].values()) == {"accepted"}


def test_smtp_requires_tls_before_authentication_and_preserves_partial_response(monkeypatch):
    smtp = Mock()
    smtp.send_message.return_value = {"two@example.org": (550, b"no such recipient")}
    monkeypatch.setattr(deliver.smtplib, "SMTP", Mock(return_value=smtp))
    recipients = ["one@example.org", "two@example.org"]
    refused = deliver.send_email(
        {"host": "smtp.example.org", "user": "sender@example.org", "password": "test"},
        recipients, "Brief", "Text", "<b>HTML</b>",
    )
    assert [call[0] for call in smtp.method_calls] == ["starttls", "login", "send_message", "close"]
    assert smtp.send_message.call_args.kwargs["to_addrs"] == recipients
    assert list(refused) == ["two@example.org"]


def test_uncertain_slack_acknowledgement_never_reposts(cfg, monkeypatch):
    cfg.settings.delivery = "slack"
    cfg.settings.slack_webhook_url = "https://hooks.slack.com/services/example"
    send = Mock(side_effect=RuntimeError("timeout"))
    monkeypatch.setattr(deliver, "send_slack", send)
    day = "2026-11-10"
    archive.save_brief(cfg, day, "markdown", "html", {"date": day})
    meta = archive.read_payload(cfg.settings.archive_dir, day)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="uncertain"):
            deliver.deliver(cfg, day, "markdown", "html", [], [], meta)
    assert send.call_count == 1


def test_dashboard_filters_links_rejects_cross_origin_and_reads_atomic_version(cfg):
    day = "2026-11-10"
    item = BriefItem(1, "GGG", "Graco", "other", "<example>", "Review the source.",
                     "javascript:invalid", role="client")
    target = archive.save_brief(cfg, day, "revision markdown", "revision html", {
        "schema_version": 2, "date": day, "items": [asdict(item)], "price_moves": [],
        "outcome": "partial", "mode": "live",
    })
    assert Path(target, "brief.md").read_text(encoding="utf-8") == "revision markdown"
    web = create_app(cfg).test_client()
    page = web.get(f"/brief/{day}")
    assert page.status_code == 200
    assert b"javascript:invalid" not in page.data
    assert b"&lt;example&gt;" in page.data
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert web.get(f"/brief/{day}/download/md").data == b"revision markdown"
    assert web.get("/brief/2026-02-30").status_code == 404
    assert web.get("/", headers={"Host": "untrusted.example.org"}).status_code == 400
    with web.session_transaction() as session:
        token = session["run_token"]
    assert web.post("/api/run", headers={"Origin": "https://other.example.org", "X-Run-Token": token},
                    json={}).status_code == 403
    assert web.post("/api/run", json={}).status_code == 403
