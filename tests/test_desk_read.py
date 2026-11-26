import hashlib
import json
import time
from base64 import urlsafe_b64encode
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app import create_app
from src import archive, chatgpt, main, profile, snapshot
from src.collectors import calendar, official_calendar

BASE = "http://127.0.0.1:5000"


def _browser(web):
    assert web.get("/settings", base_url=BASE).status_code == 200
    with web.session_transaction(base_url=BASE) as session:
        return {"X-Run-Token": session["run_token"]}


def test_oauth_binds_callback_to_browser_and_consumes_state_once(cfg, monkeypatch):
    state = {"active": None, "accounts": {}}
    monkeypatch.setattr(chatgpt.Connection, "_load", lambda self: state)
    monkeypatch.setattr(chatgpt.Connection, "_save", lambda self, new: state.update(new))

    def exchange(url, **form):
        assert url == chatgpt.TOKEN
        assert form["client_id"] == "oaiapp_test"
        assert form["redirect_uri"] == BASE + "/auth/callback"
        challenge = urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
        assert auth["code_challenge"] == [challenge]
        return {"access_token": "example-access", "id_token": "example-jwt", "refresh_token": "example-refresh",
                "token_type": "Bearer", "expires_in": 3600, "scope": f"openid {chatgpt.GRANT}"}

    def verify(token, client, nonce):
        assert (token, client, nonce) == ("example-jwt", "oaiapp_test", auth["nonce"][0])
        return {"sub": "user", "email": "trader@example.test"}

    monkeypatch.setattr(chatgpt, "_post", exchange)
    monkeypatch.setattr(chatgpt, "_verify", verify)
    app = create_app(cfg)
    client, other = app.test_client(), app.test_client()
    headers = _browser(client)
    _browser(other)
    assert client.post("/api/chatgpt/sign-in", json={}, base_url=BASE).status_code == 403
    response = client.post("/api/chatgpt/sign-in", json={}, headers=headers, base_url=BASE)
    target = urlsplit(response.json["url"])
    auth = parse_qs(target.query)
    assert target.netloc == "auth.openai.com"
    assert auth["client_id"] == [chatgpt.DYNAMIC]
    assert auth["ext_agent_host_id"][0].startswith("urn:uuid:")
    assert auth["code_challenge_method"] == ["S256"]
    callback = "/auth/callback?code=abc&client_id=oaiapp_test&state=" + auth["state"][0]
    assert other.get(callback, base_url=BASE).status_code == 400
    assert client.get("/auth/callback?state=incorrect", base_url=BASE).status_code == 400
    assert client.get(callback, base_url=BASE).status_code == 303
    assert client.get(callback, base_url=BASE).status_code == 400
    assert state["active"] == "oaiapp_test"
    page = client.get("/settings", base_url=BASE)
    assert b"trader@example.test" in page.data and b"example-access" not in page.data


def test_plan_stream_demands_completion_and_accepts_only_supported_fields(monkeypatch):
    url = chatgpt.RESOURCE + "/responses"
    events = [
        {"type": "response.output_text.delta", "delta": '{"summary":["quote0"]}'},
        {"type": "response.completed"},
    ]

    class Stream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def iter_lines(self, **_):
            return iter(b"data: " + json.dumps(e).encode() for e in events)

    def post(target, **kwargs):
        assert target == url
        assert kwargs["headers"]["Authorization"] == "Bearer example"
        assert kwargs["json"] == {"model": "enabled", "input": [{"role": "user", "content": "{}"}],
                                  "store": False, "stream": True, "instructions": "editor"}
        return Stream()

    monkeypatch.setattr(chatgpt, "_model_list", lambda _: [{"slug": "enabled"}])
    monkeypatch.setattr(chatgpt.requests, "post", post)
    assert chatgpt.Connection._generate("example", "editor", "{}") == {"summary": ["quote0"]}
    events.pop()
    with pytest.raises(chatgpt.ChatGPTError):
        chatgpt.Connection._generate("example", "editor", "{}")
    events.append({"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}})
    with pytest.raises(chatgpt.ChatGPTError) as result:
        chatgpt.Connection._generate("example", "editor", "{}")
    assert result.value.brief_status == "limit"


def test_plan_uses_encrypted_credentials_and_rotates_once(cfg, monkeypatch):
    store = {}

    class Vault:
        def get_password(self, service, name):
            return store.get((service, name))

        def set_password(self, service, name, value):
            store[(service, name)] = value

    conn = chatgpt.Connection(cfg.settings)
    monkeypatch.setattr(chatgpt.Connection, "_vault", lambda self: Vault())
    conn.path.parent.mkdir(parents=True, exist_ok=True)
    conn._save({"active": "selected", "accounts": {"selected": {
        "label": "Personal", "subject": "person", "expires_at": 0, "scope": chatgpt.GRANT,
        "access_token": "old-access", "refresh_token": "old-refresh",
    }}})
    assert b"old-refresh" not in conn.path.read_bytes()

    def rotate(_url, **form):
        assert form["refresh_token"] == "old-refresh" and form["client_id"] == "selected"
        return {"token_type": "Bearer", "access_token": "fresh-access", "expires_in": 3600,
                "refresh_token": "fresh-refresh"}

    monkeypatch.setattr(chatgpt, "_post", rotate)
    assert conn._access() == "fresh-access"
    assert conn._access(force=True, stale_token="old-access") == "fresh-access"
    assert conn._load()["accounts"]["selected"]["refresh_token"] == "fresh-refresh"


def test_tracker_preserves_date_only_local_day_and_source_precedence(monkeypatch):
    day = date(2026, 12, 4)
    primary = official_calendar._event("Employment Situation", day, official_calendar._clock("8:30 a.m."),
                                       official_calendar._NY, "USD", "BLS", "https://example.net/bls")
    cad = official_calendar._event("Bank of Canada Interest rate announcement", day, None,
                                   official_calendar._NY, "CAD", "Bank of Canada", "https://example.net/boc")

    def official(*_, **_kwargs):
        return [primary, cad], [{"provider": "BLS", "state": "ok", "country": "USD", "source_url": "https://example.net/bls"}]

    def vendor(*_):
        return {"items": [{"time": "2026-12-04T05:30:00-08:00", "country": "USD", "title": "Non-Farm Employment Change",
                           "impact": "High", "forecast": "150K", "previous": "100K", "local_date": str(day), "all_day": False}],
                "provider": "Vendor", "source_url": "https://example.net/vendor", "state": "partial"}

    monkeypatch.setattr(official_calendar, "fetch", official)
    monkeypatch.setattr(calendar, "fetch_economic", vendor)
    result = calendar.fetch_release_tracker(day, 1, "America/Los_Angeles", ["USD", "CAD"], "Medium")
    first, second = result["items"]
    assert first["local_date"] == "2026-12-04" and first["all_day"]
    assert second["provider"] == "BLS" and second["time"] == "2026-12-04T05:30:00-08:00"
    assert second["forecast"] == ""
    assert result["state"] == "partial"


def test_official_calendar_marks_recent_overlapping_cache_on_outage(tmp_path, monkeypatch):
    day = date(2026, 12, 17)
    event = official_calendar._event("Bank of England MPC announcement", day, None,
                                    official_calendar._UK, "GBP", "Bank of England", "https://example.test/mpc")
    monkeypatch.setattr(official_calendar, "_ons", lambda *_: ([], "partial"))
    monkeypatch.setattr(official_calendar, "_boe", lambda *_: [event])
    first, _ = official_calendar.fetch(date(2026, 12, 16), day, ["GBP"], cache_dir=tmp_path)
    assert first == [event]

    def unavailable(*_):
        raise OSError("offline")

    monkeypatch.setattr(official_calendar, "_boe", unavailable)
    second, coverage = official_calendar.fetch(day, date(2026, 12, 18), ["GBP"], cache_dir=tmp_path)
    assert second[0]["cached_at"] == coverage[1]["fetched_at"]
    assert coverage[1]["state"] == "cached" and coverage[1]["covered_through"] == str(day)


def test_same_click_archives_chatgpt_desk_read_and_rejects_unsupported_prose(cfg, monkeypatch):
    now = datetime.now(timezone.utc)
    day = main.today_iso(cfg.settings.timezone)
    cfg.desk_profile = profile.default_profile()
    cfg.desk_profile["watchlist"] = []
    cfg.desk_profile["sections"] = [{"id": sid, "label": sid, "enabled": True}
                                    for sid in ("heartbeats", "calendar", "headlines")]
    cfg.desk_profile["calendar"].update(earnings=False, auctions=False)
    quote = {"symbol": "CL=F", "label": "WTI crude", "group": "commodities", "state": "ok",
             "pinned": True, "kind": "price", "chg": -2.0, "chg_unit": "%", "last": 73.2,
             "as_of": now.isoformat(), "provider": "Test quote", "source_url": "https://example.test/oil"}
    old = {**quote, "symbol": "GC=F", "label": "Gold", "state": "stale"}
    headline = {"title": "Crude oil falls as supply outlook improves", "url": "https://example.test/news",
                "published_at": quote["as_of"], "publisher": "Wire", "themes": ["energy_commodities"]}
    release = {"title": "Employment Situation", "time": date.fromisoformat(day).isoformat() + "T08:30:00-05:00",
               "country": "USD", "local_date": day, "impact": "High", "all_day": False, "forecast": "", "previous": "",
               "provider": "BLS", "source_url": "https://example.test/jobs"}
    monkeypatch.setattr(snapshot.macro, "fetch_quotes", lambda *_a, **_k: [quote, old])
    monkeypatch.setattr(snapshot.heads, "collect_headlines", lambda *_a, **_k: {"top": [headline]})
    monkeypatch.setattr(snapshot.cal, "fetch_release_tracker",
                        lambda *_a, **_k: {"items": [release], "state": "ok"})
    monkeypatch.setattr(main, "_collect", lambda *_a: ([], [], [], {}, {}))
    monkeypatch.setattr(main, "_safe_snapshot", lambda current: snapshot.collect(current.settings, current.desk_profile))

    def generate(_instructions, evidence):
        data = json.loads(evidence)
        assert "https://" not in evidence and "quote1" not in data["rows"]
        assert "oil_down_supply" in data["rows"]["quote0"]["scenarios"]
        return {"summary": ["quote0", "release0", "hallucination"],
                "reads": {"quote0": {"scenario": "oil_down_supply", "related": 0},
                          "release0": {"scenario": "Unemployment hit 80% today"}}}

    monkeypatch.setattr(chatgpt.Connection, "generator", lambda self: generate)
    web = create_app(cfg).test_client()
    headers = _browser(web)
    assert web.post("/api/run", json={}, headers=headers, base_url=BASE).status_code == 202
    for _ in range(100):
        result = web.get("/api/run", base_url=BASE).json
        if result["state"] != "running":
            break
        time.sleep(0.02)
    assert result["state"] == "done"
    data = archive.read_payload(cfg.settings.archive_dir, day)["snapshot"]["desk_read"]
    assert data["state"] == "chatgpt" and len(data["items"]) == 2
    assert data["items"][0]["context"]["url"] == headline["url"]
    assert "improving supply" in data["items"][0]["impact"]
    assert "80%" not in str(data)
    page = web.get(result["url"], base_url=BASE)
    assert page.status_code == 200 and b"Before the open" in page.data
    assert b"https://example.test/jobs" in page.data
