"""Load and validate configuration from coverage.yaml and .env."""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from datetime import time

import yaml
from dotenv import load_dotenv

from .models import Company
from .paths import DATA_DIR, ENV_FILE, PROJECT_ROOT

logger = logging.getLogger(__name__)

COVERAGE_PATH = PROJECT_ROOT / "config" / "coverage.yaml"


@dataclass
class Settings:
    # LLM provider selection (pluggable)
    llm_provider: str = "none"
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None

    # Delivery
    slack_webhook_url: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    email_recipients: list = field(default_factory=list)
    delivery: str = "none"
    timezone: str = "America/New_York"
    briefing_time: time = time(7, 45)
    archive_dir: Path = DATA_DIR / "archive"
    demo_archive_dir: Path | None = None
    market_provider: str = "yahoo"
    market_fallback: str = "none"
    alpaca_key: str | None = None
    alpaca_secret: str | None = None
    alpaca_feed: str = "sip"
    require_complete: bool = False
    profile_path: Path | None = None

    @property
    def llm_enabled(self) -> bool:
        p = (self.llm_provider or "auto").strip().lower()
        if p == "none":
            return False
        if p in {"local", "lmstudio"}:
            return True
        if p == "openrouter":
            return bool(self.openrouter_api_key or self.llm_api_key)
        return bool(
            self.openai_api_key or self.anthropic_api_key or self.gemini_api_key
            or self.openrouter_api_key or self.llm_api_key or self.llm_base_url
        )


@dataclass
class CoverageConfig:
    sector: str
    client: Optional[Company]
    peers: list
    event_weights: dict
    relevance_weights: dict
    rss_feeds: list

    @property
    def companies(self) -> list:
        return ([self.client] if self.client else []) + self.peers

    def company_for_ticker(self, ticker: str) -> Optional[Company]:
        for c in self.companies:
            if c.ticker.upper() == ticker.upper():
                return c
        return None

    def relevance_weight(self, role: str) -> float:
        # A plain watchlist name matters about as much as a peer unless the user says otherwise.
        fallback = self.relevance_weights.get("peer", 0.8) if role == "watch" else self.relevance_weights.get("sector", 0.5)
        return _finite_float(self.relevance_weights.get(role, fallback), 0.5)

    def event_weight(self, event_type: str) -> float:
        return _finite_float(self.event_weights.get(event_type, self.event_weights.get("other", 0.2)), 0.2)


def _finite_float(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


@dataclass
class AppConfig:
    settings: Settings
    coverage: CoverageConfig
    profile_managed: bool = False  # True when the watchlist comes from the editable profile
    desk_profile: dict | None = None


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def _load_settings() -> Settings:
    load_dotenv(ENV_FILE, override=False)

    def split_emails(v: str) -> list:
        return [x.strip() for x in (v or "").split(",") if x.strip()]

    default_archive = DATA_DIR / "archive"
    archive_dir = Path(os.getenv("ARCHIVE_DIR") or default_archive)
    settings = Settings(
        llm_provider=(os.getenv("LLM_PROVIDER") or "none").strip().lower(),
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_model=os.getenv("LLM_MODEL") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=_int_env("SMTP_PORT", 587),
        smtp_user=os.getenv("SMTP_USER") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_from=os.getenv("SMTP_FROM") or None,
        email_recipients=split_emails(os.getenv("EMAIL_RECIPIENTS", "")),
        delivery=os.getenv("DELIVERY", "none").strip().lower(),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        archive_dir=archive_dir,
        demo_archive_dir=PROJECT_ROOT / "demo" if archive_dir == default_archive else None,
        briefing_time=time.fromisoformat(os.getenv("BRIEFING_TIME", "07:45")),
        market_provider=os.getenv("MARKET_PROVIDER", "yahoo").strip().lower(),
        market_fallback=os.getenv("MARKET_FALLBACK", "none").strip().lower(),
        alpaca_key=os.getenv("ALPACA_API_KEY") or None,
        alpaca_secret=os.getenv("ALPACA_API_SECRET") or None,
        alpaca_feed=os.getenv("ALPACA_FEED", "sip").strip().lower(),
        require_complete=os.getenv("REQUIRE_COMPLETE", "false").strip().lower() in {"1", "true", "yes"},
        profile_path=Path(os.environ["PROFILE_FILE"]) if os.getenv("PROFILE_FILE") else None,
    )
    ZoneInfo(settings.timezone)
    if settings.briefing_time.tzinfo or settings.briefing_time >= time(8):
        raise ValueError("BRIEFING_TIME must be a local time before 08:00")
    if settings.delivery not in {"none", "slack", "email", "both"}:
        raise ValueError("Invalid DELIVERY")
    if settings.llm_provider not in {"none", "auto", "openai", "anthropic", "gemini", "openrouter", "local", "lmstudio"}:
        raise ValueError("Invalid LLM_PROVIDER")
    if settings.market_provider not in {"yahoo", "alpaca"} or settings.market_fallback not in {"none", "yahoo", "alpaca"}:
        raise ValueError("Invalid market provider")
    if settings.alpaca_feed not in {"iex", "sip"}:
        raise ValueError("ALPACA_FEED must be sip or iex")
    if not 0 < settings.smtp_port < 65536:
        raise ValueError("SMTP_PORT out of range")
    return settings


def _read_weights(data: dict) -> tuple:
    out = []
    for key in ("event_weights", "relevance_weights"):
        weights = data.get(key) or {}
        if not isinstance(weights, dict) or any(not 0 <= _finite_float(v, -1) <= 100 for v in weights.values()):
            raise ValueError(f"coverage.yaml: {key} must be finite weights from 0 to 100")
        out.append(weights)
    return tuple(out)


def coverage_from_profile(settings: Settings, prof: dict | None = None) -> CoverageConfig:
    """Coverage as the user last saved it in the dashboard. Weights still come from coverage.yaml."""
    from . import profile as profile_mod

    data = {}
    if COVERAGE_PATH.exists():
        loaded = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8"))
        data = loaded if isinstance(loaded, dict) else {}
    event_weights, relevance_weights = _read_weights(data)
    prof = prof if prof is not None else profile_mod.load_profile(settings)
    companies = [Company(name=row["name"], ticker=row["ticker"], cik=row["cik"], role=row["role"])
                 for row in prof["watchlist"]]
    client = next((c for c in companies if c.role == "client"), None)
    peers = [c for c in companies if c is not client]
    if not companies:
        logger.warning("The watchlist is empty: only the market snapshot will be produced")
    return CoverageConfig(
        sector=prof["sector"], client=client, peers=peers, event_weights=event_weights,
        relevance_weights=relevance_weights, rss_feeds=profile_mod.news_feed_urls(prof["watchlist"]),
    )


def _load_coverage(settings: Settings | None = None) -> CoverageConfig:
    if settings is not None and not os.getenv("COVERAGE_FILE"):
        return coverage_from_profile(settings)
    path = Path(os.getenv("COVERAGE_FILE") or COVERAGE_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Coverage config not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"coverage.yaml must contain a YAML mapping, got {type(data).__name__}")

    def _req_str(mapping: dict, key: str, where: str) -> str:
        v = mapping.get(key)
        v = v.strip() if isinstance(v, str) else ""
        if not v:
            raise ValueError(f"coverage.yaml: {where}.{key} is required")
        return v

    client = data.get("client") or {}
    if not isinstance(client, dict):
        raise ValueError("coverage.yaml: client must be a mapping")
    client_cik = str(client.get("cik") or "").strip()
    if client_cik and not client_cik.isdigit():
        raise ValueError(f"coverage.yaml: client.cik must be numeric, got {client_cik!r}")

    client_company = Company(
        name=_req_str(client, "name", "client"),
        ticker=_req_str(client, "ticker", "client").upper(),
        cik=client_cik,
        role="client",
    )

    peers = []
    for p in data.get("peers") or []:
        if not isinstance(p, dict):
            raise ValueError("coverage.yaml: peers must contain mappings")
        cik = str(p.get("cik") or "").strip()
        if cik and not cik.isdigit():
            raise ValueError(f"coverage.yaml: peer {p.get('ticker')!r} cik must be numeric")
        peers.append(
            Company(
                name=_req_str(p, "name", "peers"),
                ticker=_req_str(p, "ticker", "peers").upper(),
                cik=cik,
                role="peer",
            )
        )

    if not peers:
        logger.warning("coverage.yaml: no peers configured")

    sector = data.get("sector") or ""
    if not sector:
        logger.warning("coverage.yaml: no sector configured")

    tickers = [co.ticker for co in [client_company, *peers]]
    if len(tickers) != len(set(tickers)) or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", ticker) for ticker in tickers):
        raise ValueError("coverage.yaml: tickers must be valid and unique")
    if not isinstance(sector, str):
        raise ValueError("coverage.yaml: sector must be text")
    feeds = data.get("rss_feeds") or []
    from .guardrails import safe_http_url
    if not isinstance(feeds, list) or any(
        not isinstance(feed, dict) or not safe_http_url(feed.get("url"))
        or not feed["url"].startswith("https://") or feed.get("type", "news") not in {"news", "press", "sector"}
        or (feed.get("ticker") and feed["ticker"] not in tickers)
        for feed in feeds
    ):
        raise ValueError("coverage.yaml: invalid RSS feed definition")
    _read_weights(data)
    return CoverageConfig(
        sector=sector,
        client=client_company,
        peers=peers,
        event_weights=data.get("event_weights") or {},
        relevance_weights=data.get("relevance_weights") or {},
        rss_feeds=feeds,
    )


def load_config() -> AppConfig:
    settings = _load_settings()
    return AppConfig(settings=settings, coverage=_load_coverage(settings),
                     profile_managed=not os.getenv("COVERAGE_FILE"))
