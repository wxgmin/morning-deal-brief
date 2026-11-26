from datetime import date

from src import signals, snapshot_render
from src.collectors import calendar as cal
from src.collectors import macro


def test_earnings_results_compare_reported_eps_with_estimate():
    payload = {"data": {"rows": [
        {"symbol": "BJ", "name": "BJ's", "time": "time-pre-market", "eps": "$1.16", "epsForecast": "$1.24", "marketCap": "$13,989,352,400"},
        {"symbol": "LOSS", "name": "Loss Co", "time": "time-after-hours", "eps": "($0.12)", "epsForecast": "($0.20)", "marketCap": "$5,000,000,000"},
        {"symbol": "LATER", "name": "Later", "time": "time-after-hours", "eps": "N/A", "epsForecast": "$2.00", "marketCap": "$9,000,000,000"}]}}
    rows = {r["symbol"]: r for r in cal.parse_earnings(payload, date(2026, 11, 24), set())["rows"]}
    assert rows["BJ"]["result"] == "miss" and rows["BJ"]["eps_surprise_pct"] == -6.5
    assert rows["LOSS"]["result"] == "beat" and rows["LOSS"]["eps_surprise_pct"] == 40.0
    assert "result" not in rows["LATER"]
    assert cal.prev_weekday(date(2026, 11, 23)) == date(2026, 11, 20)


def q(symbol, last=100.0, chg=0.0, z=None, pct=None, m=None, unit="%"):
    return {"symbol": symbol, "label": symbol, "last": last, "chg": chg, "chg_unit": unit, "z": z, "state": "ok",
            "pctile_1y": pct, "chg_1m": m}


def fire(snap):
    return {s["id"]: s for s in signals.evaluate({"heartbeats": [], **snap})}


def test_credit_positioning_rotation_and_bank_rules():
    capital = {"spreads": {"rows": [
        {"series": "BAMLC0A0CM", "label": "US investment grade", "level_bp": 130, "change_bp": 3, "change_20d_bp": 30},
        {"series": "BAMLH0A0HYM2", "label": "US high yield", "level_bp": 420, "change_bp": 5, "change_20d_bp": 60}]}}
    boards = [{"quotes": [q("^GSPC", pct=97), q("^TNX", 4.5, -3, unit="bp", m=-20), q("KRE", 60, -3.4),
                          q("XLU", m=4), q("XLP", m=3), q("XLV", m=2), q("XLY", m=-4), q("XLI", m=-3), q("XLB", m=-2), q("XLF", m=-3),
                          q("BTC-USD", 90000, -9.0, z=-2.5), q("HG=F", m=6), q("GC=F", m=-1)]}]
    pos = {"rows": [{"label": "Euro", "trader": "Leveraged funds", "net": -76587, "pctile_3y": 1, "date": "2026-11-17"}]}
    curve = {"spreads": [{"name": "2s10s", "bp": 28, "chg_bp": 4}], "points": []}
    got = fire({"boards": boards, "capital": capital, "positioning": pos, "curve": curve})
    assert {"R25", "R27", "R32", "R34", "R35", "R19", "R17"} <= set(got)
    assert "+30bp to 130bp" in got["R25"]["text"] and "Euro" in got["R32"]["text"]
    assert "steepened by 4bp" in got["R35"]["text"] and "-9.00%" in got["R19"]["text"]
    assert "+7.0%" in got["R17"]["text"] and "-20bp" in got["R17"]["text"]


def test_rules_stay_quiet_in_normal_markets():
    boards = [{"quotes": [q("^GSPC", pct=60), q("^TNX", 4.5, 1, unit="bp", m=5), q("KRE", 60, -0.5),
                          q("BTC-USD", 90000, 1.0, z=0.4), q("HG=F", m=1), q("GC=F", m=0.5)]}]
    got = fire({"boards": boards, "positioning": {"rows": [{"label": "Gold", "trader": "x", "net": 1, "pctile_3y": 50, "date": "d"}]}})
    assert not {"R25", "R27", "R32", "R34", "R35", "R19", "R17", "R09"} & set(got)


def test_fed_repricing_uses_the_window_about_a_year_out():
    path = {"as_of": "2026-11-23", "target": {"low": 3.75, "high": 4.0}, "windows": [
        {"window_start": "2026-12-16", "mean_pct": 4.28, "mean_chg_bp": 30},
        {"window_start": "2027-12-15", "mean_pct": 3.50, "mean_chg_bp": -18}]}
    got = fire({"boards": [], "rate_path": path})
    assert "38bp of cuts" in got["R09"]["text"] and "-18bp" in got["R09"]["text"]
    assert got["R09"]["tag"] == "POLICY_REPRICE_DOVISH"


def test_one_year_chart_is_sampled_and_rendered_on_click():
    closes = [100 + i * 0.1 for i in range(260)]
    result = {"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": [1_700_000_000 + i * 86400 for i in range(260)],
              "indicators": {"quote": [{"close": closes}]}}
    row = macro.build_quote({"symbol": "X", "label": "X"}, result, "https://x")
    assert 50 <= len(row["hist_1y"]) <= 53 and row["hist_1y"][-1] == round(closes[-1], 5)
    data = {"timezone": "UTC", "generated_at": "2026-11-24T12:00:00+00:00", "sources": {},
            "sections": [{"id": "boards", "label": "b", "enabled": True}],
            "boards": [{"group": {"id": "rates", "label": "Rates", "why": "w"}, "quotes": [row]}]}
    html = str(snapshot_render.html_block(data))
    assert 'class="chart-pop"' in html and "1 year (weekly)" in html and 'style="' not in html


def test_ordinals_are_correct_everywhere():
    from src.macro_read import ordinal
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 81, 100, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "81st", "100th", "111th"]


def test_breadth_maths_and_thrust():
    from src.collectors import breadth
    up = [100 + i for i in range(260)]           # above both averages, new high, advancing
    down = [400 - i for i in range(260)]         # below both, new low, declining
    series = {f"U{i}": up for i in range(70)} | {f"D{i}": down for i in range(30)}
    out = breadth.compute(series)
    assert out["pct_above_200"] == 70.0 and out["pct_above_50"] == 70.0
    assert out["advancers"] == 70 and out["decliners"] == 30 and out["new_highs"] == 70 and out["new_lows"] == 30
    assert out["ema10_adv_share"] == 0.7 and not out["thrust"]


def test_breadth_and_fear_greed_rules():
    snap = {"boards": [{"quotes": [q("^GSPC", pct=95, m=2.0)]}],
            "breadth": {"pct_above_200": 42.0, "coverage": "500 of 503 S&P 500 stocks", "thrust": True, "ema10_adv_share": 0.64},
            "sentiment": {"score": 17.4, "previous_close": 17.0}}
    got = fire(snap)
    assert got["R28"]["tag"] == "BREADTH_NARROW" and "42%" in got["R28"]["text"]
    assert "R29" in got and got["R33"]["tag"] == "FG_EXTREME_FEAR" and "17" in got["R33"]["text"]
    calm = fire({"boards": [{"quotes": [q("^GSPC", pct=50)]}], "breadth": {"pct_above_200": 60.0, "thrust": False},
                 "sentiment": {"score": 50}})
    assert not {"R28", "R29", "R33"} & set(calm)


def test_fred_summary_and_economy_rules():
    from datetime import date as d, timedelta as td
    from src.collectors import fred
    claims = [(d(2025, 1, 4) + td(weeks=i), 200_000 + (i - 60) * 4_000 if i > 60 else 200_000) for i in range(78)]
    data = {"ICSA": claims, "UNRATE": [(d(2026, m, 1), 4.0 + m / 10) for m in range(1, 11)],
            "SAHMREALTIME": [(d(2026, 10, 1), 0.53)],
            "THREEFYTP10": [(d(2026, 1, 1) + td(days=i), 0.5 + i * 0.01) for i in range(40)],
            "WRESBAL": [(d(2026, 1, 1) + td(weeks=i), 3_000_000 - i * 10_000) for i in range(10)],
            "RRPONTSYD": [(d(2026, 11, 20), 0.3)], "IORB": [(d(2026, 11, 20), 3.90)],
            "T10Y2Y": [(d(2025, 1, 1) + td(days=i), -0.4 + i * 0.001) for i in range(420)],
            "BAMLH0A0HYM2": [(d(2022, 1, 1) + td(days=i), 6.5 - i * 0.002) for i in range(1300)]}
    e = fred.summarise(data)
    assert e["claims"]["vs_low_pct"] > 20 and e["sahm"]["latest"] == 0.53 and e["term_premium"]["chg20_bp"] == 20.0
    assert e["curve_2s10s"]["min_24m_bp"] == -40 and e["hy"]["pctile_5y"] == 0
    snap = {"boards": [{"quotes": [q("^TNX", 4.9, 2, unit="bp", m=30)]}], "economy": e,
            "funding": {"rates": {"SOFR": {"rate": 3.95}}},
            "curve": {"points": [{"tenor": "2Y", "chg_bp": -6}], "spreads": []}}
    got = fire(snap)
    assert {"R41", "R42", "R08", "R12", "R23"} <= set(got)
    assert "0.53pp" in got["R42"]["text"] and got["R23"]["tag"] == "HY_TIGHT"
    # The fixture's 2s10s crosses zero at day 400 of 420 (inside the 20-session window) with the 2Y falling.
    assert "R03" in got and "-6bp fall in 2Y" in got["R03"]["text"]


def test_fred_parser_skips_missing_values():
    from src.collectors import fred
    rows = fred.parse("observation_date,ICSA\n2026-11-07,220000\n2026-11-14,.\n2026-11-21,203000\n")
    assert [v for _, v in rows] == [220000.0, 203000.0]


def test_small_cap_and_data_reaction_rules():
    snap = {"boards": [{"quotes": [dict(q("RTY=F"), chg_5d=-4.0), dict(q("ES=F"), chg_5d=-1.0)]}],
            "economy": {"dgs2": {"latest": 4.6, "chg_5d_bp": 18.0, "date": "2026-11-24"}},
            "curve": {"as_of": "2026-11-24", "points": [{"tenor": "2Y", "chg_bp": 11}], "spreads": []},
            "surprises": [{"country": "USD", "title": "Core CPI m/m", "actual": "0.5%", "forecast": "0.3%",
                           "local_date": "2026-11-24", "surprise": {"notable": True, "direction": "above", "shown": "+0.2pp"}}]}
    got = fire(snap)
    assert "underperformed large caps by 3.0%" in got["R36"]["text"] and "+18bp" in got["R36"]["text"]
    assert "Core CPI" in got["R38"]["text"] and "+0.2pp" in got["R38"]["text"] and "+11bp" in got["R38"]["text"]
    snap["surprises"][0]["local_date"] = "2026-11-20"  # a release on another day must not be tied to today's 2Y move
    assert "R38" not in fire(snap)


def test_excess_bond_premium_and_credit_etf_rules():
    from src.collectors import fred
    csv_text = "date,gz_spread,ebp,est_prob\n2026-07-01,0.9,-0.10,0.08\n2026-08-01,0.95,0.02,0.09\n" \
               "2026-09-01,1.0,0.10,0.10\n2026-10-01,1.1,0.25,0.15\n"
    ebp = fred.parse_ebp(csv_text)
    assert ebp == {**ebp, "ebp_bp": 25, "chg_1m_bp": 15, "chg_3m_bp": 35, "recession_prob_pct": 15.0}
    got = fire({"boards": [{"quotes": [q("HYG", 78, -1.4), q("IEF", 95, 0.3)]}], "economy": {"ebp": ebp}})
    assert "+25bp" in got["R45"]["text"] and "1.70%" in got["R26"]["text"]
    assert all("ishares.com" not in c["url"] for c in got["R26"]["citations"]) and got["R26"]["citations"]
    calm = fire({"boards": [{"quotes": [q("HYG", 78, -0.2), q("IEF", 95, 0.1)]}],
                 "economy": {"ebp": {**ebp, "ebp_bp": -27, "chg_3m_bp": 11}}})
    assert not {"R26", "R45"} & set(calm)


def test_markdown_and_slack_cover_every_panel_with_ai_reads():
    import json
    from pathlib import Path
    from src import catalog
    archived = Path("var/archive/2026-11-24/events.json")
    if not archived.is_file():
        return
    snap = json.loads(archived.read_text(encoding="utf-8"))["snapshot"]
    snap["sections"] = [{"id": s["id"], "label": s["label"], "enabled": True} for s in catalog.SECTIONS]
    md = "\n".join(snapshot_render.markdown(snap))
    for heading in ("## Deal flow", "## Stock movers", "## Positioning", "## Breadth & sentiment", "## US economy"):
        assert heading in md, heading
    if (snap.get("morning_note") or {}).get("panels"):
        assert md.count("> **AI read:**") >= 5
    blocks, fallback = snapshot_render.slack_blocks(snap)
    text = json.dumps(blocks)
    assert len(blocks) <= 24 and "Deal flow" in text and "Economy" in text


def test_chart_endpoint_validates_and_serves_history(cfg, monkeypatch):
    from app import create_app
    from src.collectors import macro as macro_mod
    calls = []

    def fake_chart(symbol, **params):
        calls.append((symbol, params))
        return {"timestamp": [1, 2, 3], "indicators": {"quote": [{"close": [10.0, None, 12.0]}]}}, "https://x"
    monkeypatch.setattr(macro_mod, "_chart", fake_chart)
    macro_mod._CHART_CACHE.clear()
    web = create_app(cfg).test_client()
    ok = web.get("/api/chart?symbol=%5ETNX&range=5y").get_json()
    assert ok["points"] == [[1, 10.0], [3, 12.0]] and ok["chg_pct"] == 20.0 and calls[0][1]["interval"] == "1wk"
    web.get("/api/chart?symbol=%5ETNX&range=5y")
    assert len(calls) == 1  # cached
    assert web.get("/api/chart?symbol=../../etc&range=1y").status_code == 400
    assert web.get("/api/chart?symbol=SPY&range=10y").status_code == 400
