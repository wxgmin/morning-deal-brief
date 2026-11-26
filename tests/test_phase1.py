from datetime import datetime

import pytest

from app import create_app
from src import catalog, morning, profile, snapshot_render, surprise
from src.collectors import rates_path


def tracker_rows():
    rows = []
    for day, shift in (("2026-11-20", 0.0), ("2026-11-23", 4.5), ("2026-11-19", -9.0)):  # unordered on purpose
        for start, mean in ((datetime(2026, 12, 16), 423.5), (datetime(2027, 3, 17), 442.0)):
            rows += [(day, start, "375bps - 400bps", "Rate: mean", f" {mean + shift:.2f}"),
                     (day, start, "375bps - 400bps", "Rate: 25th percentile", " 420.00"),
                     (day, start, "375bps - 400bps", "Rate: 75th percentile", " 430.00"),
                     (day, start, "375bps - 400bps", "Prob: cut", "   1.29"),
                     (day, start, "375bps - 400bps", "Prob: hike", "  97.07"),
                     (day, start, "375bps - 400bps", "Prob: 400bps - 425bps", "  26.26"),
                     (day, start, "375bps - 400bps", "Prob: 425bps - 450bps", "  66.15")]
    return rows


def test_rate_path_uses_newest_two_dates_and_reports_change():
    out = rates_path.parse_rows(tracker_rows())
    assert out["as_of"] == "2026-11-23" and out["prior"] == "2026-11-20"
    first = out["windows"][0]
    assert first["window_start"] == "2026-12-16" and first["mean_pct"] == 4.28 and first["mean_chg_bp"] == 4.5
    assert first["most_likely"] == {"low": 4.25, "high": 4.5, "p": 66.15}
    assert first["p_hold"] == pytest.approx(1.64)
    assert out["target"] == {"low": 3.75, "high": 4.0}


@pytest.mark.parametrize("title,actual,forecast,read", [
    ("CPI m/m", "0.4%", "0.3%", "hotter than forecast"),
    ("Unemployment Rate", "4.3%", "4.1%", "weaker than forecast"),
    ("Initial Jobless Claims", "205K", "220K", "stronger than forecast"),
    ("CB Consumer Confidence", "103.3", "92.4", "stronger than forecast"),
])
def test_surprise_direction_depends_on_the_indicator(title, actual, forecast, read):
    s = surprise.compute({"title": title, "actual": actual, "forecast": forecast, "previous": forecast})
    assert s["read"] == read


def test_surprise_refuses_mismatched_units_and_flags_big_misses():
    assert surprise.compute({"title": "GDP", "actual": "2.1%", "forecast": "2.1K"}) is None
    big = surprise.compute({"title": "Retail Sales m/m", "actual": "1.2%", "forecast": "0.3%", "previous": "0.2%"})
    small = surprise.compute({"title": "Retail Sales m/m", "actual": "0.35%", "forecast": "0.3%", "previous": "0.0%"})
    assert big["notable"] and not small["notable"] and big["shown"] == "+0.9pp"


def quote(symbol, last, chg, unit="%", z=None, pct=None):
    return {"symbol": symbol, "label": symbol, "last": last, "chg": chg, "chg_unit": unit, "z": z, "state": "ok",
            "dp": 2, "kind": "yield" if unit == "bp" else "price", "pctile_1y": pct}


def test_since_last_brief_compares_heartbeats_and_signals():
    now = {"heartbeats": [quote("^TNX", 4.906, -1, "bp"), quote("GC=F", 3562.2, -2.4)],
           "signals": [{"id": "R22", "title": "VIX term"}], "tone": {"label": "Mixed"}}
    prior = {"brief_date": "2026-11-23", "heartbeats": [quote("^TNX", 4.912, -14, "bp"), quote("GC=F", 3515.4, -4.6)],
             "signals": [{"id": "R05", "title": "10Y shock"}], "tone": {"label": "Risk-off"}}
    out = morning.since_last(now, prior)
    shown = {r["symbol"]: r["shown"] for r in out["markets"]}
    assert shown == {"^TNX": "-1bp", "GC=F": "+1.33%"}
    assert out["tone"] == {"then": "Risk-off", "now": "Mixed"}
    assert [s["id"] for s in out["new_signals"]] == ["R22"] and [s["id"] for s in out["cleared_signals"]] == ["R05"]
    assert morning.since_last(now, None) is None


def test_summary_is_built_only_from_snapshot_figures():
    snap = {"brief_date": "2026-11-24", "timezone": "UTC",
            "tone": {"label": "Mixed", "score": 0, "max": 5, "components": [{"name": "VIX level", "score": -1, "detail": "26.40 (x)"}]},
            "heartbeats": [quote("DX-Y.NYB", 103.785, -0.51, pct=98)],
            "boards": [{"quotes": [quote("NG=F", 3.725, 16.99, z=5.2)]}],
            "rate_path": {"windows": [{"window_start": "2026-12-16", "p_hike": 97.07, "p_cut": 1.29, "mean_chg_bp": 4.5,
                                       "most_likely": {"low": 4.25, "high": 4.5, "p": 66.15}}], "source_url": "https://x"},
            "headlines": {"top": [{"title": "Stocks slide", "publisher": "Bloomberg", "url": "https://b"}]}}
    bullets = morning.top_of_morning(snap)
    text = " ".join(b["text"] for b in bullets)
    for figure in ("+16.99%", "5.2σ", "DX-Y.NYB 103.7", "near 1-year high", "97%", "4.25–4.50%", "Stocks slide"):
        assert figure in text
    assert {b["section"] for b in bullets} <= {s["id"] for s in catalog.SECTIONS}


def test_roles_reorder_and_switch_on_but_never_remove():
    base = profile.default_profile()
    base["watchlist"].append({"ticker": "ZZZ", "name": "Custom Co", "cik": "", "role": "watch"})
    updated, changes = profile.apply_role(base, "rates_fx")
    enabled = [s["id"] for s in updated["sections"] if s["enabled"]]
    assert enabled[:3] == ["summary", "heartbeats", "ratepath"] and "deals" not in enabled
    assert any(r["ticker"] == "ZZZ" for r in updated["watchlist"])
    assert next(i for i in updated["instruments"] if i["symbol"] == "AUDUSD=X")["enabled"]
    assert updated["headlines"]["themes"]["fed_rates"] == 1.5 and "JPY" in updated["calendar"]["countries"]
    assert changes and all(isinstance(c, str) for c in changes)
    with pytest.raises(profile.ProfileError):
        profile.apply_role(base, "astronaut")


def test_apply_endpoint_previews_without_saving(cfg):
    web = create_app(cfg).test_client()
    page = web.get("/settings")
    token = page.get_data(as_text=True).split('name="run-token" content="')[1].split('"')[0]
    current = web.get("/api/profile").get_json()
    response = web.post("/api/profile/apply", json={"profile": current["profile"], "role": "ecm_dcm"},
                        headers={"X-Run-Token": token})
    assert response.status_code == 200
    assert [s["id"] for s in response.get_json()["profile"]["sections"]][:2] == ["summary", "capital"]
    assert web.get("/api/profile").get_json()["profile"] == current["profile"]  # nothing persisted
    assert web.post("/api/profile/apply", json={"profile": current["profile"], "role": "ecm_dcm"}).status_code == 403


def test_summary_and_rate_path_render_everywhere():
    data = {"timezone": "UTC", "generated_at": "2026-11-24T12:00:00+00:00", "brief_date": "2026-11-24", "sources": {},
            "sections": [{"id": "summary", "label": "s", "enabled": True}, {"id": "ratepath", "label": "r", "enabled": True}],
            "summary": [{"kind": "fed", "text": "Fed path: 97% hike odds.", "section": "ratepath"}],
            "rate_path": rates_path.parse_rows(tracker_rows())}
    html = str(snapshot_render.html_block(data))
    assert "Top of the morning" in html and 'href="#brief-ratepath"' in html and "odds-hike" in html
    assert 'style="' not in html
    md = "\n".join(snapshot_render.markdown(data))
    assert "## Top of the morning" in md and "## Fed path" in md
    blocks, _ = snapshot_render.slack_blocks(data)
    assert blocks and "Top of the morning" in blocks[0]["text"]["text"]
