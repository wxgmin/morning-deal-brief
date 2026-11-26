import json

from app import create_app
from src import alerts, archive, narrative, profile, signals, snapshot_render
from src.collectors import dealflow, global_rates, movers, positioning

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>SCHEDULE 13D - Target Co (0000000001) (Subject)</title>
<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1/0001-26-000001-index.htm"/>
<updated>2026-11-24T10:00:00-05:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001-26-000001</id></entry>
<entry><title>SCHEDULE 13D - Activist Fund LP (0000000002) (Filed by)</title>
<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1/0001-26-000001-index.htm"/>
<updated>2026-11-24T10:00:00-05:00</updated><id>urn:tag:sec.gov,2008:accession-number=0001-26-000001</id></entry>
</feed>"""


def test_activist_filings_report_the_target_not_the_filer():
    rows = dealflow.parse_atom(ATOM, "activist")
    assert [r["company"] for r in rows] == ["Target Co"]
    assert rows[0]["url"].startswith("https://www.sec.gov/")


def test_merger_search_keeps_item_101_filings_once():
    hit = {"_source": {"adsh": "0001104659-26-130428", "ciks": ["0001786352"], "items": ["1.01", "9.01"],
                       "display_names": ["BILL Holdings, Inc.  (BILL)  (CIK 0001786352)"], "form": "8-K",
                       "file_date": "2026-11-20"}}
    other = {"_source": {**hit["_source"], "adsh": "x", "items": ["8.01"]}}
    rows = dealflow.parse_mna({"hits": {"hits": [hit, hit, other]}})
    assert len(rows) == 1 and rows[0]["tickers"] == ["BILL"] and rows[0]["company"] == "BILL Holdings, Inc."
    assert rows[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1786352/000110465926130428/0001104659-26-130428-index.htm"


def cot_rows(nets):
    return [{"report_date_as_yyyy_mm_dd": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}T00:00:00.000",
             "lev_money_positions_long": str(100000 + n), "lev_money_positions_short": "100000",
             "open_interest_all": "1000000"} for i, n in enumerate(nets)]


def test_positioning_extremes_describe_history_not_sign():
    rising = list(range(-300000, -60000, 10000))  # always net short, now the least short in the sample
    out = positioning.summarise("S&P", cot_rows(rising), "lev_money")
    assert out["net"] < 0 and out["pctile_3y"] == 100 and out["extreme"] == "least short in 3 years"
    falling = list(range(300000, 60000, -10000))
    assert positioning.summarise("Gold", cot_rows(falling), "lev_money")["extreme"] == "least long in 3 years"


def test_movers_drop_small_caps():
    payload = {"finance": {"result": [{"quotes": [
        {"symbol": "TINY", "marketCap": 5e8, "regularMarketChangePercent": 80},
        {"symbol": "ANF", "shortName": "Abercrombie", "marketCap": 7.4e9, "regularMarketChangePercent": 23.4,
         "regularMarketVolume": 3_000_000, "averageDailyVolume3Month": 1_500_000}]}]}}
    rows = movers.parse(payload)
    assert [r["symbol"] for r in rows] == ["ANF"] and rows[0]["rel_volume"] == 2.0


def test_global_yield_parsers():
    mof = "Interest Rate,,\nDate,1Y,10Y\n2026/11/19,1.6,2.870\n2026/11/20,1.6,2.838\n"
    jgb = global_rates.parse_mof(mof)
    assert jgb["yield"] == 2.838 and jgb["chg_bp"] == -3.2 and jgb["as_of"] == "2026-11-20"
    ecb = global_rates.parse_ecb("TIME_PERIOD,OBS_VALUE\n2026-11-20,3.3608\n2026-11-23,3.3209\n")
    assert ecb["chg_bp"] == -4.0
    boe = global_rates.parse_boe("DATE,IUDMNZC\n19 Nov 2026,5.30\n20 Nov 2026,5.233\n")
    assert boe["yield"] == 5.233 and boe["chg_bp"] == -6.7


def test_oil_backwardation_signal_uses_front_vs_twelfth():
    oil = {"state": "ok", "shape": "backwardation", "m1_m12": 6.39,
           "legs": [{"label": "Jan 2027", "price": 81.8}, {"label": "Feb 2027", "price": 80.71},
                    {"label": "Dec 2027", "price": 75.41}]}
    fired = signals.evaluate({"boards": [], "heartbeats": [], "oil_curve": oil})
    r16 = next(s for s in fired if s["id"] == "R16")
    assert "backwardation" in r16["text"] and "+6.39" in r16["text"] and r16["title"] == "Oil curve shape"
    assert not [s for s in signals.evaluate({"boards": [], "oil_curve": {**oil, "m1_m12": 0.8}}) if s["id"] == "R16"]


def alert_snap():
    return {"brief_date": "2026-11-24",
            "watchlist": [{"label": "NVDA", "state": "ok", "chg": -4.2, "last": 125.5, "source_url": "https://x",
                           "extended": {"label": "Pre-market", "pct": 3.6}},
                          {"label": "GOOG", "state": "ok", "chg": 0.4, "last": 170.0}],
            "since": {"new_signals": [{"id": "R22", "title": "VIX term structure"}]},
            "surprises": [{"country": "USD", "title": "CPI", "actual": "0.6%", "forecast": "0.3%",
                           "surprise": {"notable": True, "read": "hotter than forecast"}}],
            "dealflow": {"groups": {"mna": {"label": "Merger agreements", "items": [
                {"company": "NVIDIA", "form": "8-K", "date": "2026-11-24", "watch": True, "url": "https://sec"}]}}},
            "headlines": {"top": [{"title": "Fed weighs tender offer rules", "publisher": "FT", "url": "https://ft"}]}}


def test_alerts_cover_moves_signals_data_filings_and_keywords():
    found = alerts.evaluate(alert_snap(), {"enabled": True, "move_pct": 3.0, "keywords": ["tender offer"]})
    kinds = [a["kind"] for a in found]
    assert kinds.count("move") == 2 and {"signal", "data", "filing", "keyword"} <= set(kinds)
    assert alerts.evaluate(alert_snap(), {"enabled": False}) == []


def test_alert_push_sends_each_alert_once_per_day(cfg, monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "desk-test")
    calls = []

    class Ok:
        status_code = 200

    def post(url, **kwargs):
        calls.append(url)
        return Ok()

    found = alerts.evaluate(alert_snap(), {"move_pct": 3.0})
    first = alerts.push_new(found, cfg.settings, post=post)
    again = alerts.push_new(found, cfg.settings, post=post)
    assert len(first) == len(found) and again == [] and calls[0] == "https://ntfy.sh/desk-test"
    monkeypatch.delenv("NTFY_TOPIC")
    assert alerts.push_new(found, cfg.settings, post=post) == []


def test_alert_settings_are_validated():
    base = profile.default_profile()
    assert profile.validate_profile(base)["alerts"]["move_pct"] == 3.0
    for bad in ({"move_pct": 0}, {"move_pct": True}, {"keywords": "fed"}):
        try:
            profile.validate_profile({**base, "alerts": {**base["alerts"], **bad}})
            raise AssertionError(bad)
        except profile.ProfileError:
            pass


def panel_snap():
    return {"brief_date": "2026-11-24", "timezone": "UTC", "generated_at": "2026-11-24T12:00:00+00:00", "sources": {},
            "sections": [{"id": s, "label": s, "enabled": True} for s in ("summary", "positioning", "stockmovers")],
            "summary": [{"kind": "x", "text": "Gold fell 2.53%.", "section": "heartbeats"}],
            "oil_curve": {"state": "ok", "shape": "backwardation", "m1_m12": 6.39,
                          "legs": [{"label": "Jan 2027", "price": 81.8}, {"label": "Feb 2027", "price": 80.71}, {"label": "Dec 2027", "price": 75.41}]},
            "stock_movers": {"gainers": [{"symbol": "ANF", "pct": 23.4, "rel_volume": 2.0}], "losers": []}}


def test_panel_reads_are_checked_one_by_one():
    facts = narrative.build_facts(panel_snap())
    oil = next(f["id"] for f in facts if "WTI curve" in f["text"])
    anf = next(f["id"] for f in facts if "ANF" in f["text"])
    result = {"sentences": [{"text": "Gold fell 2.53%.", "facts": ["F1"]}], "panels": {
        "positioning": [{"text": "Backwardation of 6.39 has usually reflected tight supply.", "facts": [oil]}],
        "stockmovers": [{"text": "ANF rose 99% today.", "facts": [anf]}],          # invented figure
        "curve": [{"text": "Not requested.", "facts": [oil]}]}}
    panels, reasons = narrative.check_panels(result, facts, {"positioning", "stockmovers"})
    assert set(panels) == {"positioning"} and len(reasons) == 2
    data = {**panel_snap(), "morning_note": {"state": "ok", "sentences": [], "panels": panels}}
    html = str(snapshot_render.html_block(data))
    assert "AI read" in html and "tight supply" in html and "99%" not in html


def test_ask_endpoint_answers_only_from_the_edition(cfg, monkeypatch):
    snap = panel_snap()
    archive.save_brief(cfg, "2026-11-24", "md", "<p>x</p>", {"date": "2026-11-24", "snapshot": snap, "items": [],
                                                          "price_moves": []})
    from src import snapshot as snapshot_mod
    monkeypatch.setattr(snapshot_mod, "ai_generator", lambda settings: (
        lambda ins, ev: {"sentences": [{"text": "ANF rose 23.4%.", "facts": [
            next(f["id"] for f in json.loads(ev)["facts"] if "ANF" in f["text"])]}]}))
    web = create_app(cfg).test_client()
    token = web.get("/settings").get_data(as_text=True).split('name="run-token" content="')[1].split('"')[0]
    reply = web.post("/api/ask", json={"date": "2026-11-24", "question": "What moved?"}, headers={"X-Run-Token": token})
    assert reply.status_code == 200 and reply.get_json()["sentences"][0]["text"] == "ANF rose 23.4%."
    monkeypatch.setattr(snapshot_mod, "ai_generator", lambda settings: (lambda ins, ev: {"sentences": [
        {"text": "ANF will double.", "facts": ["F1"]}]}))
    assert web.post("/api/ask", json={"date": "2026-11-24", "question": "What next?"},
                    headers={"X-Run-Token": token}).get_json()["state"] == "rejected"
    assert web.post("/api/ask", json={"date": "2026-11-24", "question": "What next?"}).status_code == 403


def test_markdown_renders_a_full_snapshot_with_ai_note_and_desk_reads():
    data = {**panel_snap(), "sections": [{"id": s, "label": s, "enabled": True} for s in ("summary", "heartbeats")],
            "heartbeats": [{"symbol": "GC=F", "label": "Gold", "state": "ok", "last": 3559.0, "chg": -2.5, "chg_unit": "%",
                            "dp": 1, "desk_read": {"impact": "Gold impact text."}}],
            "morning_note": {"state": "ok", "sentences": [{"text": "Gold fell.", "facts": []}], "panels": {}}}
    md = "\n".join(snapshot_render.markdown(data))
    assert "Gold fell." in md and "Gold impact text." in md


def test_move_and_gold_signals_use_their_own_history():
    def q(symbol, last, chg, z, pct):
        return {"symbol": symbol, "label": symbol, "last": last, "chg": chg, "chg_unit": "%", "z": z,
                "state": "ok", "pctile_1y": pct}
    fired = {s["id"]: s for s in signals.evaluate({"boards": [{"quotes": [q("^MOVE", 131.2, 4.0, 1.0, 92),
                                                                           q("GC=F", 3700.0, 3.1, 2.4, 99)]}]})}
    assert "131.2" in fired["R10"]["text"] and "92nd 1-year" in fired["R10"]["text"]
    assert "+3.10%" in fired["R18"]["text"] and fired["R18"]["title"] == "Gold strength"
    calm = {s["id"] for s in signals.evaluate({"boards": [{"quotes": [q("^MOVE", 80.0, 1.0, 0.2, 40),
                                                                       q("GC=F", 3500.0, -2.0, -2.2, 5)]}]})}
    assert not {"R10", "R18"} & calm


def test_desk_read_collapses_when_the_checked_note_exists():
    data = {**panel_snap(), "desk_read": {"state": "builtin", "items": [{"title": "VIX", "impact": "x", "source": {}}]},
            "morning_note": {"state": "ok", "sentences": [{"text": "Gold fell.", "facts": []}], "panels": {}}}
    html = str(snapshot_render.html_block(data))
    assert "Show the desk read" in html
    data["morning_note"] = None
    assert "Show the desk read" not in str(snapshot_render.html_block(data))


FORM4 = b"""<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Chan Phillip</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>2026-11-20</value></transactionDate><transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>5.18</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>2026-11-20</value></transactionDate><transactionCoding><transactionCode>M</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>9999</value></transactionShares><transactionPricePerShare><value>1</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def test_form4_keeps_only_open_market_trades_and_flags_clusters():
    trades = dealflow.parse_form4(FORM4)
    assert trades == [{"insider": "Chan Phillip", "title": "Chief Executive Officer", "side": "Buy", "shares": 1000.0,
                       "price": 5.18, "value": 5180, "date": "2026-11-20"}]
    base = {"cik": "1", "company": "Cyto", "date": "2026-11-24", "url": "https://sec", "form": "4", "watch": False}
    rows = [{**base, "side": "Buy", "value": v, "shares": 1, "insider": n, "title": "Director"}
            for n, v in (("A", 100), ("B", 200), ("C", 300))] + [{**base, "cik": "2", "company": "Solo", "side": "Sell",
                                                                 "value": 9e6, "shares": 1, "insider": "D", "title": "CFO"}]
    rolled = dealflow.rollup_insiders(rows)
    assert rolled[0]["company"] == "Cyto" and rolled[0]["cluster"] and rolled[0]["value"] == 600
    assert rolled[0]["insider"].startswith("3 insiders") and rolled[1]["side"] == "Sell" and not rolled[1]["cluster"]
    snap = {"brief_date": "2026-11-24", "dealflow": {"groups": {"insiders": {"items": rolled}}}}
    assert any("cluster buy at Cyto" in a["text"] for a in alerts.evaluate(snap, {"move_pct": 3}))
