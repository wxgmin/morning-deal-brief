"""Sourced signal engine: "what this move usually means", from a researched rulebook.

Rules live in src/knowledge/signal_rules.json (sentence templates, caveats, confidence, threshold provenance
and citations). This module only decides *whether* a rule fires and fills its placeholders with figures taken
verbatim from the snapshot. Guarantees:
  * every number in a sentence comes from a collected quote/curve/spread (formatted here, never invented);
  * a rule whose placeholders cannot all be filled is skipped, never half-rendered;
  * thresholds the app chose itself are labelled APP_HEURISTIC and never presented as sourced;
  * only citations that research re-verified are displayed;
  * every sentence is a historical tendency, never a forecast.
"""
from __future__ import annotations

import json
import logging
import string
from functools import lru_cache
from pathlib import Path

from .macro_read import ordinal

logger = logging.getLogger(__name__)
_KB = Path(__file__).resolve().parent / "knowledge" / "signal_rules.json"
# Desk names for display. The research "indicator" field is precise but too long for a headline.
_TITLES = {"R01": "2s10s curve", "R02": "3m10y curve", "R04": "Curve shape", "R05": "10Y yield move",
           "R06": "Real yields", "R07": "Inflation breakevens", "R13": "Dollar strength", "R14": "Yen carry unwind",
           "R15": "Oil shock", "R20": "VIX level", "R21": "VIX spike", "R22": "VIX term structure",
           "R24": "High-yield spreads", "R46": "IPO window", "R16": "Oil curve shape", "R10": "Treasury volatility (MOVE)", "R18": "Gold strength",
           "R09": "Fed path repricing", "R17": "Copper/gold vs yields", "R19": "Bitcoin risk move",
           "R25": "Investment-grade spreads", "R27": "Credit vs equities", "R32": "Positioning extreme",
           "R34": "Sector rotation", "R35": "Regional banks vs curve",
           "R28": "Market breadth", "R29": "Breadth thrust", "R33": "Fear & Greed",
           "R41": "Jobless claims", "R42": "Sahm rule", "R08": "Term premium", "R12": "Money-market liquidity",
           "R03": "Curve dis-inversion", "R23": "High-yield spreads (history)",
           "R36": "Small caps vs rates", "R38": "Hawkish data reaction", "R45": "Excess bond premium",
           "R26": "Credit ETFs vs Treasuries"}
_ORDER = {"H": 0, "H (descriptive)": 0, "H (definitional)": 0, "M-H": 1, "M": 2, "L-M": 3, "L": 4}


@lru_cache(maxsize=1)
def knowledge() -> dict:
    return json.loads(_KB.read_text(encoding="utf-8"))


def _citations(rule):
    return [{"url": c["url"], "title": c.get("title") or c["url"].split("/")[2]}
            for c in rule.get("citations", []) if c.get("verified") != "known" and str(c.get("url", "")).startswith("https://")]


def _caveats(rule):
    caveats = rule.get("caveats") or []
    return [caveats] if isinstance(caveats, str) else list(caveats)[:2]


def _fill(template: str, values: dict) -> str | None:
    fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    if any(values.get(name) in (None, "") for name in fields):
        return None
    return template.format(**{k: values[k] for k in fields})


def _num(v, spec="+.0f"):
    return format(v, spec) if isinstance(v, (int, float)) else None


class _Engine:
    def __init__(self, snap):
        self.snap = snap
        self.rules = knowledge()["rules"]
        self.out = []
        self.q = {q["symbol"]: q for q in (snap.get("boards_flat") or []) if q.get("state") == "ok" and q.get("chg") is not None}

    def emit(self, rid, values, *, tag=None, inputs=(), provenance=None, sentence=None):
        rule = self.rules.get(rid)
        if not rule:
            return
        text = _fill(sentence or rule["sentence"], values)
        if not text:
            logger.info("Signal %s skipped: missing inputs", rid)
            return
        self.out.append({
            "id": rid, "tag": tag or rule.get("tag"), "title": _TITLES.get(rid) or rule.get("indicator") or rid,
            "indicator": rule.get("indicator"), "text": text,
            "confidence": rule.get("confidence"), "provenance": provenance or rule.get("threshold_provenance"),
            "caveats": _caveats(rule), "citations": _citations(rule), "inputs": list(inputs),
        })

    # ── rates ──
    def curve(self):
        curve = self.snap.get("curve") or {}
        spreads = {s["name"]: s for s in curve.get("spreads", [])}
        pts = {p["tenor"]: p for p in curve.get("points", [])}
        src = f"Treasury par curve {curve.get('as_of', '')}".strip()
        s = spreads.get("2s10s")
        if s and s["bp"] < 0:
            self.emit("R01", {"level": _num(s["bp"])}, inputs=[f"2s10s {s['bp']:+.0f}bp ({src})"],
                      provenance="SOURCE for the inversion concept; the app checks the latest daily value, not a monthly average.")
        s = spreads.get("3m10y")
        if s and s["bp"] < 0:
            self.emit("R02", {"level": _num(s["bp"])}, inputs=[f"3m10y {s['bp']:+.0f}bp ({src})"],
                      provenance="SOURCE (NY Fed model uses monthly averages; the app checks the latest daily value).")
        two, ten = pts.get("2Y"), pts.get("10Y")
        if two and ten and two.get("chg_bp") is not None and ten.get("chg_bp") is not None:
            c2, c10 = two["chg_bp"], ten["chg_bp"]
            slope = c10 - c2
            if abs(slope) >= 5:
                kind = ("BULL_STEEPENER" if slope > 0 and c2 < 0 else "BEAR_STEEPENER" if slope > 0 and c10 > 0
                        else "BULL_FLATTENER" if slope < 0 and c10 < 0 else "BEAR_FLATTENER" if slope < 0 and c2 > 0 else None)
                meaning = (self.rules["R04"].get("implies") or {}).get(kind) if kind else None
                if kind and meaning:
                    self.emit("R04", {"type": kind.replace("_", " ").lower(), "chg_2y": _num(c2), "chg_10y": _num(c10),
                                      "type_meaning": meaning[0].lower() + meaning[1:].rstrip(".")},
                              tag=f"CURVE_{kind}", inputs=[f"2Y {c2:+.0f}bp, 10Y {c10:+.0f}bp ({src})"])

    def ten_year(self):
        q = self.q.get("^TNX")
        if q and (abs(q["chg"]) >= 10 or abs(q.get("z") or 0) >= 2):
            self.emit("R05", {"chg": _num(q["chg"]), "z": _num(q.get("z"), "+.1f")},
                      inputs=[f"US 10Y {q['last']:.3f}% ({q['chg']:+.0f}bp, Yahoo Finance)"])

    def real_yields(self):
        ry = self.snap.get("real_yields") or {}
        if ry.get("real10_chg20_bp") is not None and ry["real10_chg20_bp"] >= 25:
            self.emit("R06", {"chg": _num(ry["real10_chg20_bp"]), "window": "20 sessions"},
                      inputs=[f"10Y real yield {ry['real10']:.2f}% (U.S. Treasury, {ry['as_of']})"])
        if ry.get("be10_chg20_bp") is not None and abs(ry["be10_chg20_bp"]) >= 20:
            self.emit("R07", {"chg": _num(ry["be10_chg20_bp"]), "level": _num(ry["be10"], ".2f")},
                      tag="INFLATION_COMP_UP" if ry["be10_chg20_bp"] > 0 else "INFLATION_COMP_DOWN",
                      inputs=[f"10Y breakeven {ry['be10']:.2f}%, 20-session change {ry['be10_chg20_bp']:+.0f}bp (U.S. Treasury)"])

    # ── cross-asset ──
    def dollar_oil_yen(self):
        dxy = self.q.get("DX-Y.NYB")
        if dxy and dxy.get("chg_1m") is not None and dxy["chg_1m"] >= 3:
            self.emit("R13", {"chg": _num(dxy["chg_1m"], "+.1f"), "window": "the past month"},
                      inputs=[f"DXY {dxy['last']:.2f}, 1-month {dxy['chg_1m']:+.1f}%"])
        jpy, vix = self.q.get("JPY=X"), self.q.get("^VIX")
        if jpy and vix and jpy.get("chg_5d") is not None and jpy["chg_5d"] <= -3 and vix["chg"] > 0:
            self.emit("R14", {"chg": _num(abs(jpy["chg_5d"]), ".1f"), "window": "five sessions"},
                      inputs=[f"USD/JPY {jpy['last']:.2f}, 5-day {jpy['chg_5d']:+.1f}%", f"VIX {vix['chg']:+.1f}%"])
        oil = self.q.get("CL=F")
        if oil and oil.get("chg_1m") is not None and oil["chg_1m"] >= 20:
            self.emit("R15", {"chg": _num(oil["chg_1m"], "+.1f"), "window": "the past month", "level": f"${oil['last']:.2f}"},
                      inputs=[f"WTI {oil['last']:.2f}, 1-month {oil['chg_1m']:+.1f}%"])

    def volatility(self):
        vix = self.q.get("^VIX")
        if not vix:
            return
        level = vix["last"]
        band = ("VIX_COMPLACENT", "below 15", "calm markets, which can coincide with leverage building up") if level < 15 else \
               ("VIX_NORMAL", "between 15 and 20", "ordinary conditions") if level < 20 else \
               ("VIX_ELEVATED", "between 20 and 30", "above-normal demand for downside protection") if level < 30 else \
               ("VIX_STRESS", "of 30 and above", "drawdowns and wider credit spreads, and also above-average subsequent returns on average")
        if band[0] != "VIX_NORMAL":
            self.emit("R20", {"level": f"{level:.2f}", "band_text": band[1], "band_meaning": band[2]}, tag=band[0],
                      inputs=[f"VIX {level:.2f} (Cboe via Yahoo Finance)"])
        prev = vix.get("prev")
        if prev:
            pts = level - prev
            if pts >= 4 or vix["chg"] >= 20:
                self.emit("R21", {"chg": f"{pts:+.1f}", "chg_pct": f"{vix['chg']:+.0f}"},
                          inputs=[f"VIX {prev:.2f} → {level:.2f}"])
        v3 = self.q.get("^VIX3M")
        if v3 and v3["last"] > 0 and level / v3["last"] > 1.0:
            self.emit("R22", {"vix": f"{level:.2f}", "vix3m": f"{v3['last']:.2f}"},
                      inputs=[f"VIX/VIX3M ratio {level / v3['last']:.2f}"])
        if level >= 20:
            self.emit("R46", {"vix": f"{level:.2f}"}, tag="IPO_WINDOW_SHUT" if level >= 25 else "IPO_WINDOW_STRAINED",
                      inputs=[f"VIX {level:.2f}"])

    def rates_vol_and_gold(self):
        move = self.q.get("^MOVE")
        if move and move.get("pctile_1y") is not None and move["pctile_1y"] >= 80:
            self.emit("R10", {"level": f"{move['last']:.1f}", "pctile": f"{ordinal(move['pctile_1y'])} 1-year"},
                      tag="RATES_VOL_ELEVATED", inputs=[f"MOVE {move['last']:.1f} (ICE via Yahoo Finance)"],
                      provenance="The research rule uses a 5-year percentile; the app has 1 year of history, "
                                 "so it uses the 1-year percentile >= 80 (APP_HEURISTIC).")
        gold = self.q.get("GC=F")
        if gold and (gold.get("z") or 0) >= 2:
            self.emit("R18", {"chg": f"{gold['chg']:+.2f}", "context": f"a {gold['z']:+.1f} standard-deviation day"},
                      tag="GOLD_STRENGTH", inputs=[f"Gold {gold['last']:,.1f} ({gold['chg']:+.2f}%)"])

    def more_rules(self):
        q, rows = self.q, ((self.snap.get("capital") or {}).get("spreads") or {}).get("rows") or []
        spreads = {r.get("series") or r.get("label"): r for r in rows if not r.get("stale")}
        ig, hy = spreads.get("BAMLC0A0CM"), spreads.get("BAMLH0A0HYM2")
        if ig and (ig.get("change_20d_bp") or 0) >= 25:
            self.emit("R25", {"chg": f"{ig['change_20d_bp']:+.0f}", "level": f"{ig['level_bp']:.0f}"},
                      inputs=[f"IG OAS {ig['level_bp']:.0f}bp, about-1-month change {ig['change_20d_bp']:+.0f}bp (ICE BofA via FRED)"])
        spx = q.get("^GSPC")
        if spx and hy and (spx.get("pctile_1y") or 0) >= 95 and (hy.get("change_20d_bp") or 0) >= 50:
            self.emit("R27", {"chg": f"{hy['change_20d_bp']:+.0f}"},
                      inputs=[f"S&P 500 at the {ordinal(spx['pctile_1y'])} percentile of its year", f"HY OAS {hy['change_20d_bp']:+.0f}bp in about a month"])
        extremes = [r for r in (self.snap.get("positioning") or {}).get("rows") or []
                    if r.get("pctile_3y") is not None and (r["pctile_3y"] >= 95 or r["pctile_3y"] <= 5)]
        if extremes:  # one signal naming every extreme market, not one card per market
            names = [r["label"] for r in extremes]
            market = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            pct = "/".join(ordinal(r['pctile_3y']) for r in extremes) + " 3-year"
            self.emit("R32", {"market": market, "pctile": pct},
                      inputs=[f"CFTC {r['label']}: {r['trader'].lower()} net {r['net']:+,} ({r['date']})" for r in extremes])
        defensives = [q[s]["chg_1m"] for s in ("XLU", "XLP", "XLV") if s in q and q[s].get("chg_1m") is not None]
        cyclicals = [q[s]["chg_1m"] for s in ("XLY", "XLI", "XLB", "XLF") if s in q and q[s].get("chg_1m") is not None]
        ten = q.get("^TNX")
        if len(defensives) >= 2 and len(cyclicals) >= 3 and ten and (ten.get("chg_1m") or 0) < 0:
            spread = sum(defensives) / len(defensives) - sum(cyclicals) / len(cyclicals)
            if spread >= 5:
                self.emit("R34", {"spread": f"{spread:.1f}", "window": "the past month"}, tag="DEFENSIVE_ROTATION",
                          inputs=[f"Defensive ETFs avg {sum(defensives) / len(defensives):+.1f}%, cyclical ETFs avg "
                                  f"{sum(cyclicals) / len(cyclicals):+.1f}%, 10Y {ten['chg_1m']:+.0f}bp over a month"],
                          provenance="Research uses a 2-sigma spread; the app uses a 5-point gap over one month (APP_HEURISTIC).")
        kre = q.get("KRE")
        curve = {s["name"]: s for s in (self.snap.get("curve") or {}).get("spreads", [])}.get("2s10s")
        if kre and abs(kre["chg"]) >= 3 and curve and curve.get("chg_bp") is not None:
            move = ("steepened by %.0fbp" % curve["chg_bp"] if curve["chg_bp"] >= 1 else
                    "flattened by %.0fbp" % -curve["chg_bp"] if curve["chg_bp"] <= -1 else "was little changed")
            self.emit("R35", {"chg": f"{kre['chg']:+.2f}", "curve_move": move},
                      inputs=[f"KRE {kre['chg']:+.2f}%", f"2s10s {curve['chg_bp']:+.0f}bp (Treasury)"])
        btc = q.get("BTC-USD")
        if btc and abs(btc.get("z") or 0) >= 2:
            self.emit("R19", {"chg": f"{btc['chg']:+.2f}", "z": f"{btc['z']:+.1f}"},
                      sentence="Bitcoin moved {chg}% ({z} standard deviations versus its recent daily moves). Since 2020, "
                               "IMF research finds crypto has traded more like a high-beta risk asset than a hedge, so large "
                               "crypto moves have often accompanied broader risk appetite shifts.",
                      provenance="The research rule also requires a 60-day S&P correlation above 0.3, which the app does not compute.")
        cu, au = q.get("HG=F"), q.get("GC=F")
        if cu and au and ten and None not in (cu.get("chg_1m"), au.get("chg_1m"), ten.get("chg_1m")):
            ratio = cu["chg_1m"] - au["chg_1m"]
            if abs(ratio) >= 5 and abs(ten["chg_1m"]) >= 15 and (ratio > 0) != (ten["chg_1m"] > 0):
                self.emit("R17", {"chg": f"{ratio:+.1f}", "chg10": f"{ten['chg_1m']:+.0f}"},
                          inputs=[f"Copper {cu['chg_1m']:+.1f}%, gold {au['chg_1m']:+.1f}%, 10Y {ten['chg_1m']:+.0f}bp over a month"],
                          provenance="Research uses 1.5-sigma divergences; the app uses 5% vs 15bp over a month (APP_HEURISTIC).")
        path = self.snap.get("rate_path") or {}
        windows, target = path.get("windows") or [], path.get("target")
        if windows and target:
            w = min(windows, key=lambda x: abs((int(x["window_start"][:4]) * 12 + int(x["window_start"][5:7]))
                                               - (int(path["as_of"][:4]) * 12 + int(path["as_of"][5:7]) + 12)))
            if w.get("mean_pct") is not None and abs(w.get("mean_chg_bp") or 0) >= 15:
                mid = (target["low"] + target["high"]) / 2
                implied = round((mid - w["mean_pct"]) * 100)
                self.emit("R09", {"cuts": f"{abs(implied):.0f}", "direction": "cuts" if implied >= 0 else "hikes",
                                  "chg": f"{w['mean_chg_bp']:+.0f}"},
                          tag="POLICY_REPRICE_" + ("DOVISH" if w["mean_chg_bp"] < 0 else "HAWKISH"),
                          sentence="Markets now price about {cuts}bp of {direction} over roughly 12 months, a {chg}bp "
                                   "change on the day. These are market-implied probabilities, not forecasts, and they "
                                   "have historically swung sharply on payrolls and CPI surprises.",
                          inputs=[f"Atlanta Fed tracker window from {w['window_start']}: mean {w['mean_pct']:.2f}% vs "
                                  f"target midpoint {mid:.3f}%"],
                          provenance="Uses the Atlanta Fed SOFR-options tracker rather than CME FedWatch; fires on a "
                                     "15bp one-day repricing (APP_HEURISTIC).")

    def breadth_and_mood(self):
        b, fg, spx = self.snap.get("breadth") or {}, self.snap.get("sentiment") or {}, self.q.get("^GSPC")
        pct = b.get("pct_above_200")
        if pct is not None:
            near_high = bool(spx and (spx.get("pctile_1y") or 0) >= 90)
            band = ("BREADTH_WASHOUT" if pct < 20 else "BREADTH_NARROW" if near_high and pct < 50 else None)
            if band:
                ctx = ("near the top of its 1-year range" if near_high else
                       f"{spx['chg_1m']:+.1f}% over the past month" if spx and spx.get("chg_1m") is not None else "as shown in the boards")
                self.emit("R28", {"level": f"{pct:.0f}", "index_context": ctx}, tag=band,
                          inputs=[f"{pct:.1f}% of {b.get('coverage', 'S&P 500 stocks')} above their 200-day average (computed)"])
        if b.get("thrust"):
            self.emit("R29", {}, inputs=[f"10-day EMA of the advancing share {b.get('ema10_adv_share')}"])
        score = fg.get("score")
        if score is not None and (score <= 24 or score >= 76):
            band = "EXTREME_FEAR" if score <= 24 else "EXTREME_GREED"
            self.emit("R33", {"level": f"{score:.0f}", "band": band.replace("_", " ").lower()}, tag="FG_" + band,
                      inputs=[f"CNN Fear & Greed {score:.1f} (previous close {fg.get('previous_close')})"])

    def economy(self):
        e = self.snap.get("economy") or {}
        c = e.get("claims")
        if c and (c["avg4"] >= 1.2 * c["low12_avg4"] or c["latest"] > 400_000):
            self.emit("R41", {"level": f"{c['avg4'] / 1000:.0f}", "chg_pct": f"{c['vs_low_pct']:.0f}"},
                      inputs=[f"Initial claims 4-week average {c['avg4']:,} vs 12-month low {c['low12_avg4']:,} (FRED ICSA, {c['date']})"])
        s = e.get("sahm")
        if s and s["latest"] >= 0.5:
            self.emit("R42", {"level": f"{s['latest']:.2f}"}, inputs=[f"FRED SAHMREALTIME {s['latest']:.2f} ({s['date']})"])
        tp, ten = e.get("term_premium"), self.q.get("^TNX")
        if tp and ten and (ten.get("chg_1m") or 0) >= 25 and tp["chg20_bp"] >= 0.5 * ten["chg_1m"]:
            self.emit("R08", {"chg": f"{ten['chg_1m']:.0f}", "model": "Kim-Wright"},
                      inputs=[f"10Y {ten['chg_1m']:+.0f}bp over a month; Kim-Wright term premium {tp['chg20_bp']:+.0f}bp over 20 sessions"])
        res, rrp, iorb = e.get("reserves_bn"), e.get("rrp_bn"), e.get("iorb")
        sofr = ((self.snap.get("funding") or {}).get("rates") or {}).get("SOFR")
        if res and rrp and iorb and sofr and sofr["rate"] > iorb["latest"]:
            self.emit("R12", {"level": f"{res['latest']:,}", "rrp": f"{rrp['latest']:,.1f}"},
                      inputs=[f"SOFR {sofr['rate']:.2f}% above IORB {iorb['latest']:.2f}%", f"Reserves ${res['latest']:,}bn, ON RRP ${rrp['latest']:,.1f}bn"],
                      provenance="The research rule wants SOFR above IORB for several days; the app checks the latest day.")
        cv = e.get("curve_2s10s")
        pts = {p["tenor"]: p for p in (self.snap.get("curve") or {}).get("points", [])}
        if cv and cv["min_24m_bp"] < 0 and cv["disinverted_recently"] and (pts.get("2Y") or {}).get("chg_bp") is not None \
                and pts["2Y"]["chg_bp"] < 0:
            self.emit("R03", {"level": f"{cv['latest_bp']:+d}", "chg_2y": f"{pts['2Y']['chg_bp']:+.0f}"},
                      inputs=[f"2s10s {cv['latest_bp']:+d}bp, 2-year low {cv['min_24m_bp']:+d}bp (FRED T10Y2Y)"],
                      provenance="Crossing above zero within the last 20 sessions, with the 2Y falling on the day (APP_HEURISTIC window).")
        rty, es, two = self.q.get("RTY=F"), self.q.get("ES=F"), e.get("dgs2")
        if rty and es and two and None not in (rty.get("chg_5d"), es.get("chg_5d")):
            gap = rty["chg_5d"] - es["chg_5d"]
            if abs(gap) >= 1.5 and abs(two["chg_5d_bp"]) >= 5 and (gap > 0) == (two["chg_5d_bp"] < 0):
                self.emit("R36", {"out_under": "out" if gap > 0 else "under", "spread": f"{abs(gap):.1f}",
                                  "chg2y": f"{two['chg_5d_bp']:+.0f}"},
                          inputs=[f"Russell 2000 futures {rty['chg_5d']:+.1f}% vs S&P 500 futures {es['chg_5d']:+.1f}% over 5 days",
                                  f"2Y Treasury {two['chg_5d_bp']:+.0f}bp over 5 days (FRED DGS2)"],
                          provenance="Research uses 1.5-sigma moves; the app uses a 1.5-point gap and a 5bp 2Y move over 5 days (APP_HEURISTIC).")
        pts2 = {p["tenor"]: p for p in (self.snap.get("curve") or {}).get("points", [])}.get("2Y")
        curve_day = (self.snap.get("curve") or {}).get("as_of")
        for item in self.snap.get("surprises") or []:
            title = item.get("title", "")
            hawkish = ("CPI" in title or "Non-Farm" in title or "Nonfarm" in title) and item["surprise"]["notable"]                 and item["surprise"]["direction"] == "above"
            if hawkish and pts2 and (pts2.get("chg_bp") or 0) >= 8 and item.get("local_date") == curve_day:
                self.emit("R38", {"release": f"{item.get('country', '')} {title}".strip(), "surprise": item["surprise"]["shown"],
                                  "chg2y": f"{pts2['chg_bp']:+.0f}"},
                          inputs=[f"{title} {item['actual']} vs {item['forecast']} forecast", f"2Y {pts2['chg_bp']:+.0f}bp on {curve_day} (Treasury)"])
                break
        ebp = e.get("ebp")
        if ebp and ebp["ebp_bp"] > 0 and ebp["chg_3m_bp"] > 0:
            self.emit("R45", {"level": f"{ebp['ebp_bp']:+d}"},
                      inputs=[f"Excess bond premium {ebp['ebp_bp']:+d}bp, {ebp['chg_3m_bp']:+d}bp over 3 months; Fed model "
                              f"12-month recession probability {ebp['recession_prob_pct']:.1f}% ({ebp['date'][:7]})"])
        hyg, ief = self.q.get("HYG"), self.q.get("IEF")
        if hyg and ief and hyg["chg"] - ief["chg"] <= -1.0:
            self.emit("R26", {"spread_ret": f"{abs(hyg['chg'] - ief['chg']):.2f}"},
                      inputs=[f"HYG {hyg['chg']:+.2f}% vs IEF {ief['chg']:+.2f}%"],
                      provenance="Research uses a 2-sigma gap or a 1% NAV discount; the app uses a 1-point daily gap (APP_HEURISTIC).")
        hy = e.get("hy")
        if hy and hy.get("pctile_5y") is not None and (hy["pctile_5y"] <= 10 or hy["level_bp"] >= 600):
            band = "HY_TIGHT" if hy["pctile_5y"] <= 10 else "HY_STRESS"
            self.emit("R23", {"level": f"{hy['level_bp']}", "pctile": f"{ordinal(hy['pctile_5y'])} {hy['years']:.0f}-year"},
                      tag=band, inputs=[f"HY OAS {hy['level_bp']}bp (ICE BofA via FRED, {hy['date']})"])
        ig = e.get("ig")
        if ig and (ig.get("pctile_5y") or 0) >= 80 and not any(s["id"] == "R25" for s in self.out):
            self.emit("R25", {"chg": "+0", "level": f"{ig['level_bp']}"},
                      sentence="IG spreads are {level}bp, in the top fifth of their recent history. Historically, IG spreads "
                               "widen with rate volatility and heavy supply as well as with growth fears. Sustained widening has "
                               "raised borrowing costs for investment-grade acquirers.",
                      inputs=[f"IG OAS {ig['level_bp']}bp, {ordinal(ig['pctile_5y'])} percentile of {ig['years']:.0f} years"])

    def oil_curve(self):
        oc = self.snap.get("oil_curve") or {}
        if oc.get("state") == "ok" and oc.get("m1_m12") is not None and abs(oc["m1_m12"]) >= 2:
            self.emit("R16", {"state": oc["shape"], "spread": f"{oc['m1_m12']:+.2f}"},
                      tag="OIL_" + oc["shape"].upper(),
                      inputs=[f"WTI {oc['legs'][0]['label']} {oc['legs'][0]['price']:.2f} vs "
                              f"{oc['legs'][2]['label']} {oc['legs'][2]['price']:.2f} (NYMEX via Yahoo)"],
                      provenance="Concept SOURCE; the app shows the curve only when M1-M12 exceeds $2 (APP_HEURISTIC).")

    def credit(self):
        rows = ((self.snap.get("capital") or {}).get("spreads") or {}).get("rows") or []
        hy = next((r for r in rows if r.get("series") == "BAMLH0A0HYM2" or r.get("label") == "US high yield"), None)
        if not hy or hy.get("stale"):
            return
        c20, c1 = hy.get("change_20d_bp"), hy.get("change_bp")
        if c20 is not None and c20 >= 100:
            self.emit("R24", {"chg": _num(c20), "window": "about one month (20 observations)"},
                      inputs=[f"HY OAS {hy['level_bp']:.0f}bp (ICE BofA via FRED, {hy['as_of']})"])
        elif c1 is not None and c1 >= 25:
            self.emit("R24", {"chg": _num(c1), "window": "one day"},
                      inputs=[f"HY OAS {hy['level_bp']:.0f}bp (ICE BofA via FRED, {hy['as_of']})"])

    # ── regimes (all legs must move at least 1σ of their own recent daily changes) ──
    def regimes(self):
        def leg(symbol, sign):
            q = self.q.get(symbol)
            return q if q and q.get("z") is not None and q["z"] * sign >= 1 else None

        spx = lambda s: leg("ES=F", s) or leg("^GSPC", s)  # noqa: E731
        fmt = lambda q: f"{q['label']} {('%+.0fbp' % q['chg']) if q['chg_unit'] == 'bp' else ('%+.2f%%' % q['chg'])}"  # noqa: E731
        patterns = [
            ("G1", "TIGHTENING_SHOCK", [leg("^TNX", 1), leg("DX-Y.NYB", 1), spx(-1), leg("^VIX", 1)],
             "Yields, the dollar and volatility are up together while stocks fall ({legs}). Historically this combination "
             "has reflected a hawkish or discount-rate shock that tightens financial conditions, especially for EM and "
             "long-duration assets.", ["R13", "R05"]),
            ("G3", "RISK_ON", [spx(1), leg("^VIX", -1), leg("DX-Y.NYB", -1)],
             "Stocks are up with lower volatility and a softer dollar ({legs}), the classic risk-on pattern; historically "
             "supportive of IPO and leveraged-finance windows.", ["R46"]),
            ("G6", "FLIGHT_TO_QUALITY", [leg("^TNX", -1), leg("GC=F", 1), leg("^VIX", 1)],
             "Treasuries and gold are rallying while volatility jumps ({legs}), a classic flight-to-quality pattern; "
             "historically sharp, and often brief unless credit keeps widening.", ["R22", "R14"]),
            ("G7", "CARRY_UNWIND", [leg("JPY=X", -1), leg("^N225", -1), leg("^VIX", 1)],
             "The yen is strengthening while Japanese equities fall and volatility rises ({legs}). Historically, this has "
             "marked carry-trade unwinds amplified by deleveraging.", ["R14"]),
        ]
        for gid, tag, legs, sentence, related in patterns:
            if all(legs):
                cites = [c for rid in related for c in _citations(self.rules.get(rid, {}))]
                self.out.append({"id": gid, "tag": tag, "title": "Cross-asset regime", "confidence": "M",
                                 "text": sentence.format(legs=", ".join(fmt(q) for q in legs)),
                                 "provenance": "Pattern match: every leg moved at least 1 standard deviation of its own recent daily changes (APP_HEURISTIC qualifier).",
                                 "caveats": ["A pattern describes today's moves; it is not a forecast."],
                                 "citations": list({c["url"]: c for c in cites}.values())[:3],
                                 "inputs": [fmt(q) for q in legs]})


def evaluate(snap: dict) -> list:
    """Return fired signals, regimes first, then by confidence."""
    flat = [q for board in snap.get("boards") or [] for q in board.get("quotes", [])]
    engine = _Engine({**snap, "boards_flat": flat + (snap.get("heartbeats") or [])})
    for step in (engine.regimes, engine.curve, engine.ten_year, engine.real_yields, engine.dollar_oil_yen,
                 engine.volatility, engine.credit, engine.oil_curve, engine.rates_vol_and_gold, engine.more_rules, engine.breadth_and_mood, engine.economy):
        try:
            step()
        except Exception as exc:  # one malformed input must not hide the other signals
            logger.warning("Signal step %s failed (%s)", step.__name__, type(exc).__name__)
    engine.out.sort(key=lambda s: (not s["id"].startswith("G"), _ORDER.get(s.get("confidence"), 5)))
    return engine.out
