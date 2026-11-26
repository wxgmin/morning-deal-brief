import re

from src import signals, snapshot_render
from src.collectors import rates


def q(symbol, last, chg, *, unit="%", z=None, prev=None, label=None, chg_1m=None, chg_5d=None):
    return {"symbol": symbol, "label": label or symbol, "last": last, "chg": chg, "chg_unit": unit, "z": z,
            "prev": prev, "state": "ok", "chg_1m": chg_1m, "chg_5d": chg_5d, "kind": "yield" if unit == "bp" else "price"}


def snap(quotes, **extra):
    return {"boards": [{"group": {"id": "x"}, "quotes": quotes}], "heartbeats": [], **extra}


def ids(fired):
    return [s["id"] for s in fired]


def test_vix_term_structure_inversion_fires_with_exact_figures():
    fired = signals.evaluate(snap([q("^VIX", 26.54, 4.0, prev=25.52), q("^VIX3M", 25.59, 1.0)]))
    r22 = next(s for s in fired if s["id"] == "R22")
    assert "26.54" in r22["text"] and "25.59" in r22["text"]
    assert "R20" in ids(fired) and "R46" in ids(fired)  # elevated band + IPO window strained


def test_normal_conditions_produce_no_vix_signals():
    fired = signals.evaluate(snap([q("^VIX", 17.0, 1.0, prev=16.8), q("^VIX3M", 19.0, 0.5)]))
    assert not {"R20", "R21", "R22", "R46"} & set(ids(fired))


def test_stale_or_failed_quotes_never_feed_a_signal():
    stale = q("^VIX", 40.0, 30.0, prev=30.0)
    stale["state"] = "stale"
    assert signals.evaluate(snap([stale])) == []


def test_every_number_in_a_sentence_comes_from_the_inputs():
    fired = signals.evaluate(snap([q("^TNX", 4.912, -14.0, unit="bp", z=-2.6, label="US 10-year")]))
    r05 = next(s for s in fired if s["id"] == "R05")
    assert "-14bp" in r05["text"] and "-2.6" in r05["text"]


def test_regime_needs_every_leg_beyond_one_sigma():
    legs = [q("^TNX", 4.5, 9.0, unit="bp", z=1.5), q("DX-Y.NYB", 106, 0.6, z=1.4),
            q("ES=F", 6000, -1.5, z=-1.8), q("^VIX", 24, 12.0, z=1.6, prev=21.4)]
    assert "G1" in ids(signals.evaluate(snap(legs)))
    legs[1]["z"] = 0.4  # dollar flat: no tightening-shock pattern
    assert "G1" not in ids(signals.evaluate(snap(legs)))


def test_curve_and_breakeven_rules_use_official_inputs():
    curve = {"as_of": "2026-11-23", "spreads": [{"name": "2s10s", "bp": -12.0}, {"name": "3m10y", "bp": 30.0}],
             "points": [{"tenor": "2Y", "chg_bp": -9.0}, {"tenor": "10Y", "chg_bp": -1.0}]}
    real = {"as_of": "2026-11-23", "real10": 2.79, "real10_chg20_bp": 13.0, "be10": 2.13, "be10_chg20_bp": -21.0}
    fired = signals.evaluate(snap([], curve=curve, real_yields=real))
    assert {"R01", "R04", "R07"} <= set(ids(fired)) and "R02" not in ids(fired) and "R06" not in ids(fired)
    r04 = next(s for s in fired if s["id"] == "R04")
    assert r04["tag"] == "CURVE_BULL_STEEPENER" and "-9bp" in r04["text"]


def test_hy_widening_uses_twenty_session_change():
    capital = {"spreads": {"rows": [{"series": "BAMLH0A0HYM2", "label": "US high yield", "level_bp": 520.0,
                                     "change_bp": 8.0, "change_20d_bp": 140.0, "as_of": "2026-11-23", "stale": False}]}}
    fired = signals.evaluate(snap([], capital=capital))
    assert "R24" in ids(fired) and "+140bp" in next(s for s in fired if s["id"] == "R24")["text"]


def test_only_reverified_citations_are_shown_and_none_are_known_only():
    kb = signals.knowledge()["rules"]
    for rid, rule in kb.items():
        shown = signals._citations(rule)
        assert all(c["url"].startswith("https://") for c in shown)
        known = {c["url"] for c in rule.get("citations", []) if c.get("verified") == "known"}
        assert not known & {c["url"] for c in shown}, rid


def test_missing_placeholder_skips_instead_of_half_rendering():
    assert signals._fill("VIX is {level} vs {other}", {"level": "20"}) is None
    assert signals._fill("VIX is {level}", {"level": "20"}) == "VIX is 20"


def test_funding_parser_reports_range_position():
    payload = {"refRates": [
        {"type": "EFFR", "percentRate": 3.88, "effectiveDate": "2026-11-23", "targetRateFrom": 3.75, "targetRateTo": 4.0},
        {"type": "SOFR", "percentRate": 4.05, "effectiveDate": "2026-11-23"}]}
    out = rates.parse_funding(payload)
    assert out["target"] == {"low": 3.75, "high": 4.0}
    assert out["sofr_minus_top_bp"] == 5.0 and out["effr_minus_bottom_bp"] == 13.0


def test_breakevens_are_nominal_minus_real_on_the_same_day():
    nominal = 'Date,"5 Yr","10 Yr"\n' + "\n".join(f"11/{d:02d}/2026,4.00,{4.20 + d / 100:.2f}" for d in range(1, 24))
    real = 'Date,"5 YR","10 YR"\n' + "\n".join(f"11/{d:02d}/2026,2.00,2.50" for d in range(1, 24))
    out = rates.build_real_yields(nominal, real)
    assert out["as_of"] == "2026-11-23" and out["be10"] == round(4.43 - 2.50, 3)
    assert out["be10_chg_bp"] == 1.0 and out["be10_chg20_bp"] == 20.0


def test_signals_render_with_sources_in_html_and_markdown():
    fired = signals.evaluate(snap([q("^VIX", 31.0, 25.0, prev=24.8), q("^VIX3M", 27.0, 5.0)]))
    data = {"timezone": "UTC", "generated_at": "2026-11-24T12:00:00+00:00", "brief_date": "2026-11-24",
            "sections": [{"id": "signals", "label": "s", "enabled": True}], "signals": fired, "sources": {}}
    html = str(snapshot_render.html_block(data))
    assert "What this usually means" in html and "cboe.com" in html and "<script" not in html
    md = "\n".join(snapshot_render.markdown(data))
    assert "## What this usually means" in md and re.search(r"VIX is 31\.00", md)


def test_new_sections_slot_in_after_their_default_predecessor():
    from src import catalog, profile
    saved = [s for s in profile.default_profile()["sections"] if s["id"] != "signals"][::-1]  # user reversed order
    out = [s["id"] for s in profile.validate_profile({**profile.default_profile(), "sections": saved})["sections"]]
    assert out.index("signals") == out.index("reads") + 1
    assert len(out) == len(catalog.SECTIONS)
