"""Deterministic interpretation of the tape: risk tone, intermarket reads, abnormal moves.

No model, no invented numbers. Every sentence is assembled from collected quotes through `fmt_change`,
so the figure in the sentence is the figure in the table. Text that describes a *usual relationship*
("typically a headwind for gold") is labelled as a tendency, never as a forecast or as a fact about today.
"""
from __future__ import annotations

# Thresholds are shown on the page ("How this is scored"), so a reader can disagree with them.
THRESHOLDS = {
    "futures_pct": 0.5, "vix_high": 25.0, "vix_low": 15.0, "vix_move_pct": 8.0,
    "dollar_pct": 0.4, "credit_pct": 0.3, "yield_bp": 5.0, "dollar_read_pct": 0.25, "gold_pct": 1.0, "oil_pct": 2.0,
}


def ordinal(n) -> str:
    """1 -> '1st', 22 -> '22nd', 13 -> '13th'."""
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fmt_change(q: dict) -> str:
    if q.get("chg") is None:
        return "n/a"
    if q.get("chg_unit") == "bp":
        return f"{q['chg']:+.0f}bp"
    return f"{q['chg']:+.2f}%"


def fmt_level(q: dict) -> str:
    if q.get("last") is None:
        return "n/a"
    dp = q.get("dp", 2)
    text = f"{q['last']:,.{dp}f}"
    return text + "%" if q.get("kind") == "yield" else text


def _by_symbol(quotes):
    """Fresh quotes only: a stale or failed quote must never feed a sentence."""
    return {q["symbol"]: q for q in quotes if q.get("state") == "ok" and q.get("chg") is not None}


def tone(quotes: list) -> dict | None:
    t, by = THRESHOLDS, _by_symbol(quotes)
    parts = []

    def add(name, score, detail):
        parts.append({"name": name, "score": score, "detail": detail})

    es = by.get("ES=F")
    if es:
        s = 1 if es["chg"] >= t["futures_pct"] else -1 if es["chg"] <= -t["futures_pct"] else 0
        add("S&P 500 futures", s, f"{fmt_change(es)} (±{t['futures_pct']}% counts)")
    vix = by.get("^VIX")
    if vix:
        s = -1 if vix["last"] >= t["vix_high"] else 1 if vix["last"] <= t["vix_low"] else 0
        add("VIX level", s, f"{fmt_level(vix)} (≥{t['vix_high']:.0f} stressed, ≤{t['vix_low']:.0f} calm)")
        s = -1 if vix["chg"] >= t["vix_move_pct"] else 1 if vix["chg"] <= -t["vix_move_pct"] else 0
        add("VIX change", s, f"{fmt_change(vix)} (±{t['vix_move_pct']:.0f}% counts)")
    dxy = by.get("DX-Y.NYB")
    if dxy:
        s = -1 if dxy["chg"] >= t["dollar_pct"] else 1 if dxy["chg"] <= -t["dollar_pct"] else 0
        add("US dollar", s, f"{fmt_change(dxy)} (stronger dollar = tighter conditions)")
    hyg = by.get("HYG")
    if hyg:
        s = -1 if hyg["chg"] <= -t["credit_pct"] else 1 if hyg["chg"] >= t["credit_pct"] else 0
        add("High-yield credit (HYG)", s, f"{fmt_change(hyg)} (price proxy, not spreads)")
    if len(parts) < 2:
        return None
    total = sum(p["score"] for p in parts)
    label = ("Risk-off" if total <= -2 else "Cautious" if total == -1 else "Mixed" if total == 0
             else "Constructive" if total == 1 else "Risk-on")
    return {"label": label, "score": total, "max": len(parts), "components": parts,
            "note": "Sum of the signals above (each −1, 0 or +1). A summary of the inputs, not a forecast."}


def reads(quotes: list, curve: dict | None = None) -> list:
    t, by = THRESHOLDS, _by_symbol(quotes)
    out = []

    def add(text, symbols, kind):
        out.append({"text": text, "symbols": [s for s in symbols if s in by], "kind": kind})

    futures = [by[s] for s in ("ES=F", "NQ=F", "YM=F", "RTY=F") if s in by]
    if futures:
        listing = ", ".join(f"{q['label'].replace(' futures', '')} {fmt_change(q)}" for q in futures)
        add(f"US equity futures, latest available changes: {listing}.", [q["symbol"] for q in futures], "fact")
    y, d, g, o = by.get("^TNX"), by.get("DX-Y.NYB"), by.get("GC=F"), by.get("CL=F")
    if y and d:
        up_y, dn_y = y["chg"] >= t["yield_bp"], y["chg"] <= -t["yield_bp"]
        up_d, dn_d = d["chg"] >= t["dollar_read_pct"], d["chg"] <= -t["dollar_read_pct"]
        inputs = f"10Y {fmt_change(y)}, DXY {fmt_change(d)}"
        if up_y and up_d:
            add(f"Higher yields and a firmer dollar together ({inputs}). Tendency: tighter financial conditions, "
                "a headwind for gold, long-duration growth stocks and emerging markets.", ["^TNX", "DX-Y.NYB"], "tendency")
        elif dn_y and dn_d:
            add(f"Lower yields and a softer dollar together ({inputs}). Tendency: easier financial conditions, "
                "supportive for gold, equities and emerging markets.", ["^TNX", "DX-Y.NYB"], "tendency")
        elif (up_y and dn_d) or (dn_y and up_d):
            add(f"Yields and the dollar are moving in opposite directions ({inputs}), which usually means the driver is "
                "not US rates alone (relative growth, flows or a foreign central bank).", ["^TNX", "DX-Y.NYB"], "tendency")
    if y and g and abs(g["chg"]) >= t["gold_pct"] and abs(y["chg"]) >= t["yield_bp"]:
        inputs = f"gold {fmt_change(g)}, 10Y {fmt_change(y)}"
        same = (g["chg"] > 0) == (y["chg"] > 0)
        if same:
            add(f"Gold and the nominal 10-year yield moved in the same direction ({inputs}). "
                "Check real yields and dollar moves when evaluating gold.",
                ["GC=F", "^TNX"], "tendency")
        elif g["chg"] < 0:
            add(f"Gold fell and the nominal 10-year yield rose ({inputs}).", ["GC=F", "^TNX"], "fact")
        else:
            add(f"Gold rose and the nominal 10-year yield fell ({inputs}).", ["GC=F", "^TNX"], "fact")
    if o and abs(o["chg"]) >= t["oil_pct"]:
        extra = f", 10Y {fmt_change(y)}" if y else ""
        if o["chg"] > 0:
            add(f"Crude is sharply higher (WTI {fmt_change(o)}{extra}). Tendency: adds to inflation expectations and "
                "helps energy producers while squeezing fuel-intensive sectors.", ["CL=F", "^TNX"], "tendency")
        else:
            add(f"Crude is sharply lower (WTI {fmt_change(o)}{extra}). Tendency: eases inflation pressure and "
                "weighs on energy producers and energy credit.", ["CL=F", "^TNX"], "tendency")
    hyg, vix = by.get("HYG"), by.get("^VIX")
    if hyg and vix:
        inputs = f"HYG {fmt_change(hyg)}, VIX {fmt_change(vix)}"
        if hyg["chg"] <= -t["credit_pct"] and vix["chg"] >= t["vix_move_pct"]:
            add(f"Credit and equity volatility are both signalling stress ({inputs}).", ["HYG", "^VIX"], "fact")
        elif hyg["chg"] >= t["credit_pct"] and vix["chg"] <= -t["vix_move_pct"]:
            add(f"Credit and equity volatility are both easing ({inputs}).", ["HYG", "^VIX"], "fact")
    if curve:
        s = next((x for x in curve.get("spreads", []) if x["name"] == "2s10s"), None)
        if s and (s["inverted"] or abs(s.get("chg_bp") or 0) >= 3):
            shape = "inverted" if s["inverted"] else "positively sloped"
            change = "" if s.get("chg_bp") is None else f" ({s['chg_bp']:+.0f}bp on the day)"
            add(f"The 2s10s curve is {shape} at {s['bp']:+.0f}bp{change} (official Treasury par yields, {curve['as_of']}).",
                [], "fact")
    for region, label in (("asia", "Asia-Pacific"), ("europe", "Europe")):
        board = [q for q in by.values() if q.get("group") == "global_equities" and q.get("region") == region]
        if len(board) >= 2:
            up, dn = sum(q["chg"] > 0 for q in board), sum(q["chg"] < 0 for q in board)
            add(f"{label}: {dn} of {len(board)} indices lower, {up} higher "
                f"(biggest move {max(board, key=lambda q: abs(q['chg']))['label']} "
                f"{fmt_change(max(board, key=lambda q: abs(q['chg'])))}).", [q["symbol"] for q in board], "fact")
    return out[:8]


def movers(quotes: list, limit: int = 6) -> list:
    rows = [q for q in quotes if q.get("abnormal") and q.get("state") == "ok"]
    rows.sort(key=lambda q: -abs(q["z"]))
    return [{"symbol": q["symbol"], "label": q["label"], "change": fmt_change(q), "level": fmt_level(q),
             "z": q["z"], "chg": q["chg"], "group": q.get("group")} for q in rows[:limit]]
