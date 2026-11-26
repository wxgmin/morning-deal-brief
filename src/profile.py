"""The user's editable profile: watchlist, markets, instruments, feeds, calendar, section order.

One JSON file (var/profile.json by default). The dashboard edits it through /api/profile; the CLI,
scheduler and brief all read the same file, so a change made in the UI is what tomorrow's 7:45 run uses.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import yaml
from filelock import FileLock

from . import catalog
from .archive import _atomic_write
from .guardrails import safe_http_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_VERSION = 1
MAX_WATCHLIST, MAX_INSTRUMENTS, MAX_FEEDS = 60, 200, 40
ROLES = ("client", "peer", "watch")
IMPACTS = ("Low", "Medium", "High")
_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
_SYMBOL = re.compile(r"[A-Za-z0-9^=.\-]{1,24}")
_ID = re.compile(r"[a-z0-9\-]{1,40}")
_CIK = re.compile(r"\d{1,10}")


class ProfileError(ValueError):
    """The submitted profile is invalid; the message is safe to show the user."""


class ProfileConflict(ProfileError):
    """Preferences have changed since the caller read them."""


def profile_path(settings=None) -> Path:
    explicit = os.getenv("PROFILE_FILE") or getattr(settings, "profile_path", None)
    if explicit:
        return Path(explicit)
    archive = getattr(settings, "archive_dir", None)
    return (Path(archive).parent if archive else PROJECT_ROOT / "var") / "profile.json"


def _text(value, field, limit=120, required=True):
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = " ".join(value.split())
    if required and not value:
        raise ProfileError(f"{field} is required")
    if len(value) > limit or any(ord(c) < 32 for c in value):
        raise ProfileError(f"{field} is too long or contains control characters")
    return value


def _flag(value, default=False):
    return value if isinstance(value, bool) else default


def _legacy_watchlist() -> tuple[str, list]:
    """Seed from the old coverage.yaml so existing coverage carries over once, then never again."""
    path = PROJECT_ROOT / "config" / "coverage.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = []
        client = data.get("client") or {}
        if client.get("ticker") and client.get("name"):
            rows.append({"ticker": str(client["ticker"]).upper(), "name": str(client["name"]),
                         "cik": str(client.get("cik") or ""), "role": "client"})
        for peer in data.get("peers") or []:
            if peer.get("ticker") and peer.get("name"):
                rows.append({"ticker": str(peer["ticker"]).upper(), "name": str(peer["name"]),
                             "cik": str(peer.get("cik") or ""), "role": "peer"})
        if rows:
            return str(data.get("sector") or ""), rows
    except (OSError, yaml.YAMLError, AttributeError, TypeError):
        pass
    preset = catalog.WATCHLIST_PRESETS["big_banks"]
    return preset["sector"], [{"ticker": t, "name": n, "cik": "", "role": r} for n, t, r in preset["names"]]


def default_profile() -> dict:
    sector, watchlist = _legacy_watchlist()
    return {
        "version": PROFILE_VERSION,
        "sector": sector,
        "watchlist": watchlist,
        "markets": list(catalog.DEFAULT_MARKETS),
        "instruments": [dict(item) for item in catalog.INSTRUMENTS],
        "feeds": [{**feed, "custom": False} for feed in catalog.FEEDS],
        "calendar": {"countries": ["USD"], "min_impact": "Medium", "earnings": True, "auctions": True,
                     "horizon_days": 14},
        "headlines": {"max_items": 24, "max_age_hours": 36,
                      "themes": {key: theme["weight"] for key, theme in catalog.THEMES.items()}},
        "sections": [dict(section) for section in catalog.SECTIONS],
        "alerts": {"enabled": True, "move_pct": 3.0, "keywords": []},
    }


def validate_profile(raw) -> dict:
    """Strict, bounded normalisation. Unknown keys are dropped; anything malformed raises ProfileError."""
    if not isinstance(raw, dict):
        raise ProfileError("profile must be an object")
    base = default_profile()

    sector = _text(raw.get("sector", base["sector"]), "sector", 120, required=False)

    watchlist, seen = [], set()
    rows = raw.get("watchlist", base["watchlist"])
    if not isinstance(rows, list) or len(rows) > MAX_WATCHLIST:
        raise ProfileError(f"watchlist must be a list of at most {MAX_WATCHLIST} names")
    for row in rows:
        if not isinstance(row, dict):
            raise ProfileError("each watchlist entry must be an object")
        ticker = _text(row.get("ticker"), "ticker", 15).upper()
        if not _TICKER.fullmatch(ticker):
            raise ProfileError(f"'{ticker}' is not a valid ticker")
        if ticker in seen:
            raise ProfileError(f"{ticker} appears twice in the watchlist")
        seen.add(ticker)
        role = row.get("role", "watch")
        if role not in ROLES:
            raise ProfileError(f"role for {ticker} must be one of {', '.join(ROLES)}")
        cik = _text(row.get("cik", ""), "cik", 10, required=False)
        if cik and not _CIK.fullmatch(cik):
            raise ProfileError(f"CIK for {ticker} must be numeric")
        watchlist.append({"ticker": ticker, "name": _text(row.get("name") or ticker, "name", 100),
                          "cik": cik, "role": role})
    if sum(row["role"] == "client" for row in watchlist) > 1:
        raise ProfileError("Choose at most one focus company")

    known = {m["id"] for m in catalog.MARKETS}
    markets = raw.get("markets", base["markets"])
    if not isinstance(markets, list) or any(not isinstance(m, str) or m not in known for m in markets):
        raise ProfileError("markets must be a list of known market ids")
    markets = [m for m in dict.fromkeys(markets)]

    instruments, symbols = [], set()
    rows = raw.get("instruments", base["instruments"])
    if not isinstance(rows, list) or len(rows) > MAX_INSTRUMENTS:
        raise ProfileError(f"instruments must be a list of at most {MAX_INSTRUMENTS}")
    groups = {g["id"] for g in catalog.GROUPS}
    for row in rows:
        if not isinstance(row, dict):
            raise ProfileError("each instrument must be an object")
        symbol = _text(row.get("symbol"), "symbol", 24)
        if not _SYMBOL.fullmatch(symbol):
            raise ProfileError(f"'{symbol}' is not a valid symbol")
        if symbol.upper() in symbols:
            raise ProfileError(f"{symbol} appears twice in instruments")
        symbols.add(symbol.upper())
        group = row.get("group", "commodities")
        kind = row.get("kind", "price")
        region = row.get("region")
        if not isinstance(group, str) or group not in groups or kind not in (
            "yield", "vol", "fx", "price", "index", "etf", "crypto"
        ):
            raise ProfileError(f"{symbol}: unknown group or kind")
        if region is not None and (not isinstance(region, str) or region not in known):
            raise ProfileError(f"{symbol}: unknown market")
        dp = row.get("dp", 2)
        instruments.append({
            "symbol": symbol, "label": _text(row.get("label") or symbol, "label", 60), "group": group,
            "kind": kind, "dp": dp if isinstance(dp, int) and 0 <= dp <= 6 else 2,
            "enabled": _flag(row.get("enabled"), True), "pinned": _flag(row.get("pinned")),
            "region": region, "custom": _flag(row.get("custom")),
        })

    feeds, ids = [], set()
    rows = raw.get("feeds", base["feeds"])
    if not isinstance(rows, list) or len(rows) > MAX_FEEDS:
        raise ProfileError(f"feeds must be a list of at most {MAX_FEEDS}")
    for row in rows:
        if not isinstance(row, dict):
            raise ProfileError("each feed must be an object")
        fid = _text(row.get("id"), "feed id", 40)
        if not _ID.fullmatch(fid) or fid in ids:
            raise ProfileError(f"feed id '{fid}' is invalid or duplicated")
        ids.add(fid)
        url = safe_http_url(row.get("url"))
        if not url or len(url) > 2048 or not url.startswith("https://"):
            raise ProfileError(f"feed '{fid}' needs a valid https:// URL")
        feeds.append({"id": fid, "label": _text(row.get("label") or fid, "feed label", 90), "url": url,
                      "category": _text(row.get("category") or "general", "category", 24),
                      "enabled": _flag(row.get("enabled"), True), "custom": _flag(row.get("custom"))})

    cal = raw.get("calendar", base["calendar"])
    if not isinstance(cal, dict):
        raise ProfileError("calendar must be an object")
    countries = cal.get("countries", ["USD"])
    if not isinstance(countries, list) or any(c not in catalog.CALENDAR_COUNTRIES for c in countries):
        raise ProfileError("calendar countries must come from the supported list")
    impact = cal.get("min_impact", "Medium")
    horizon = cal.get("horizon_days", 14)
    if impact not in IMPACTS or not isinstance(horizon, int) or not 1 <= horizon <= 30:
        raise ProfileError("calendar impact or horizon is out of range")
    calendar = {"countries": list(dict.fromkeys(countries)), "min_impact": impact,
                "earnings": _flag(cal.get("earnings"), True), "auctions": _flag(cal.get("auctions"), True),
                "horizon_days": horizon}

    head = raw.get("headlines", base["headlines"])
    if not isinstance(head, dict):
        raise ProfileError("headlines must be an object")
    max_items, max_age = head.get("max_items", 24), head.get("max_age_hours", 36)
    if (not isinstance(max_items, int) or not 3 <= max_items <= 40
            or not isinstance(max_age, int) or not 2 <= max_age <= 168):
        raise ProfileError("headline limits are out of range")
    themes = {}
    supplied = head.get("themes", {})
    if not isinstance(supplied, dict):
        raise ProfileError("theme weights must be an object")
    for key, theme in catalog.THEMES.items():
        weight = supplied.get(key, theme["weight"])
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 2:
            raise ProfileError(f"weight for theme '{key}' must be between 0 and 2")
        themes[key] = float(weight)
    headlines = {"max_items": max_items, "max_age_hours": max_age, "themes": themes}

    valid_sections = {s["id"] for s in catalog.SECTIONS}
    order, flags = [], {}
    rows = raw.get("sections", base["sections"])
    if not isinstance(rows, list):
        raise ProfileError("sections must be a list")
    for row in rows:
        sid = row.get("id") if isinstance(row, dict) else None
        if not isinstance(sid, str) or sid not in valid_sections or sid in flags:
            raise ProfileError("sections contain an unknown or repeated id")
        flags[sid] = _flag(row.get("enabled"), True)
        order.append(sid)
    labels = {s["id"]: s["label"] for s in catalog.SECTIONS}
    catalog_ids = [s["id"] for s in catalog.SECTIONS]
    for missing in (sid for sid in catalog_ids if sid not in flags):
        # A section added in a later release goes right after its default predecessor, not to the bottom.
        before = [sid for sid in catalog_ids[:catalog_ids.index(missing)] if sid in order]
        order.insert(order.index(before[-1]) + 1 if before else 0, missing)
        flags[missing] = next(s["enabled"] for s in catalog.SECTIONS if s["id"] == missing)
    sections = [{"id": sid, "label": labels[sid], "enabled": flags[sid]} for sid in order]

    rules = raw.get("alerts", base["alerts"])
    if not isinstance(rules, dict):
        raise ProfileError("alerts must be an object")
    move = rules.get("move_pct", 3.0)
    words = rules.get("keywords", [])
    if isinstance(move, bool) or not isinstance(move, (int, float)) or not 0.5 <= move <= 50:
        raise ProfileError("alert move threshold must be between 0.5% and 50%")
    if not isinstance(words, list) or len(words) > 20:
        raise ProfileError("alert keywords must be a list of at most 20 words")
    alerts = {"enabled": _flag(rules.get("enabled"), True), "move_pct": float(move),
              "keywords": [w for w in (_text(w, "alert keyword", 40) for w in words) if w]}

    return {"version": PROFILE_VERSION, "sector": sector, "watchlist": watchlist, "markets": markets,
            "instruments": instruments, "feeds": feeds, "calendar": calendar, "headlines": headlines,
            "sections": sections, "alerts": alerts}


def _merge_catalog(profile: dict) -> dict:
    """Surface instruments/feeds added to the catalog after the profile was written."""
    have = {i["symbol"].upper() for i in profile["instruments"]}
    for item in catalog.INSTRUMENTS:
        if item["symbol"].upper() not in have:
            profile["instruments"].append(dict(item))
    have = {f["id"] for f in profile["feeds"]}
    missing = [feed for feed in catalog.FEEDS if feed["id"] not in have]
    available = MAX_FEEDS - len(profile["feeds"])
    profile["feeds"].extend({**feed, "custom": False} for feed in missing[:available])
    return profile


def load_profile(settings=None) -> dict:
    path = profile_path(settings)
    try:
        profile = validate_profile(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        profile = default_profile()
    except (OSError, ValueError):
        # A read can race a save. Leave the original file in place for repair.
        profile = default_profile()
    return _merge_catalog(profile)


def revision(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def save_profile(raw, settings=None, *, expected=None) -> dict:
    profile = validate_profile(_merge_catalog(validate_profile(raw)))
    path = profile_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=5):
        if expected is not None and expected != revision(load_profile(settings)):
            raise ProfileConflict("Preferences changed in another session. Reload before saving.")
        _atomic_write(path, json.dumps(profile, indent=2, ensure_ascii=False))
    return profile


def reset_profile(settings=None) -> dict:
    path = profile_path(settings)
    with FileLock(str(path) + ".lock", timeout=5):
        path.unlink(missing_ok=True)
    return load_profile(settings)


def apply_preset(profile: dict, preset_id: str) -> dict:
    preset = catalog.WATCHLIST_PRESETS.get(preset_id)
    if not preset:
        raise ProfileError("unknown preset")
    updated = copy.deepcopy(profile)
    updated["sector"] = preset["sector"]
    updated["watchlist"] = [{"ticker": t, "name": n, "cik": "", "role": r} for n, t, r in preset["names"]]
    return updated


def active_instruments(profile: dict) -> list:
    """Instruments that should be collected and shown: enabled, and their market switched on."""
    markets = set(profile["markets"])
    return [i for i in profile["instruments"]
            if i["enabled"] and (i.get("region") is None or i["region"] in markets)]


def section_enabled(profile: dict, section_id: str) -> bool:
    return any(s["id"] == section_id and s["enabled"] for s in profile["sections"])


def news_feed_urls(watchlist: list, chunk: int = 3) -> list:
    """Google News searches covering every watchlist name, so editing the list edits the news too."""
    names = []
    for row in watchlist:
        name = row["name"].strip(" ,.")  # keep the full name: "Dover" alone matches Dover, Delaware
        if name and name not in names:
            names.append(name)
    feeds = []
    for start in range(0, len(names), chunk):
        query = "(" + " OR ".join(f'"{name}"' for name in names[start:start + chunk]) + ") when:7d"
        feeds.append({"url": "https://news.google.com/rss/search?q=" + quote(query, safe="")
                      + "&hl=en-US&gl=US&ceid=US%3Aen", "type": "news"})
    return feeds


def apply_role(profile: dict, role_id: str) -> tuple[dict, list]:
    """Re-shape layout, emphasis and sources for a desk role. Returns (new profile, plain-English changes).

    The watchlist, custom instruments and custom feeds are never removed: a role only switches things on,
    re-orders sections and re-weights headline themes, so applying one is always safe to undo by hand.
    """
    role = catalog.ROLE_PRESETS.get(role_id)
    if not role:
        raise ProfileError("unknown role")
    p = copy.deepcopy(profile)
    order = [sid for sid in role["sections"] if any(s["id"] == sid for s in p["sections"])]
    rest = [s for s in p["sections"] if s["id"] not in order]
    p["sections"] = ([{**next(s for s in p["sections"] if s["id"] == sid), "enabled": True} for sid in order]
                     + [{**s, "enabled": False} for s in rest])
    groups, symbols = set(role["groups_on"]), set(role["symbols_on"])
    switched = 0
    for inst in p["instruments"]:
        if (inst["group"] in groups or inst["symbol"] in symbols) and not inst["enabled"]:
            inst["enabled"], switched = True, switched + 1
    p["markets"] = list(dict.fromkeys(role["markets"]))
    p["calendar"] = {**p["calendar"], **role["calendar"]}
    defaults = {key: theme["weight"] for key, theme in catalog.THEMES.items()}
    p["headlines"] = {**p["headlines"], "themes": {**defaults, **role["themes"]}}
    feeds_on = set(role["feeds_on"])
    turned = [f["label"] for f in p["feeds"] if f["id"] in feeds_on and not f["enabled"]]
    for feed in p["feeds"]:
        if feed["id"] in feeds_on:
            feed["enabled"] = True
    labels = {s["id"]: s["label"] for s in p["sections"]}
    changes = [f"Reading order: {', '.join(labels[s] for s in order[:5])} …",
               f"{len(rest)} section(s) hidden" if rest else "All sections shown",
               f"Markets: {', '.join(p['markets'])}; calendar: {', '.join(p['calendar']['countries'])} "
               f"({p['calendar']['min_impact']}+ impact)"]
    if switched:
        changes.append(f"{switched} instrument(s) switched on")
    if turned:
        changes.append("Feeds switched on: " + ", ".join(turned))
    return validate_profile(p), changes
