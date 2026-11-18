"""Load and validate configuration from coverage.yaml and .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from .models import Company

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COVERAGE_PATH = PROJECT_ROOT / "config" / "coverage.yaml"

EVENT_TAXONOMY = [
    "M&A",
    "earnings",
    "guidance_change",
    "financing",
    "management_change",
    "activist",
    "rating_change",
    "large_price_move",
    "other",
]


@dataclass
class Settings:
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_classify_model: str = "gpt-4o-mini"
    slack_webhook_url: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    email_recipients: list = field(default_factory=list)
    delivery: str = "none"
    timezone: str = "America/New_York"
    archive_dir: Path = PROJECT_ROOT / "archive"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)


@dataclass
class CoverageConfig:
    sector: str
    client: Company
    peers: list
    event_weights: dict
    relevance_weights: dict
    rss_feeds: list

    @property
    def companies(self) -> list:
        return [self.client] + self.peers

    def company_for_ticker(self, ticker: str) -> Optional[Company]:
        for c in self.companies:
            if c.ticker.upper() == ticker.upper():
                return c
        return None

    def relevance_weight(self, role: str) -> float:
        return float(self.relevance_weights.get(role, self.relevance_weights.get("sector", 0.5)))

    def event_weight(self, event_type: str) -> float:
        return float(self.event_weights.get(event_type, self.event_weights.get("other", 0.2)))


@dataclass
class AppConfig:
    settings: Settings
    coverage: CoverageConfig


def _load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    def split_emails(v: str) -> list:
        return [x.strip() for x in v.split(",") if x.strip()]

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        openai_classify_model=os.getenv("OPENAI_CLASSIFY_MODEL", "gpt-4o-mini"),
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=os.getenv("SMTP_USER") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_from=os.getenv("SMTP_FROM") or None,
        email_recipients=split_emails(os.getenv("EMAIL_RECIPIENTS", "")),
        delivery=os.getenv("DELIVERY", "none").strip().lower(),
        timezone=os.getenv("TIMEZONE", "America/New_York"),
        archive_dir=Path(os.getenv("ARCHIVE_DIR", str(PROJECT_ROOT / "archive"))),
    )


def _load_coverage() -> CoverageConfig:
    if not COVERAGE_PATH.exists():
        raise FileNotFoundError(f"Coverage config not found: {COVERAGE_PATH}")

    data = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8"))

    client = data.get("client", {})
    client_company = Company(
        name=client.get("name", ""),
        ticker=client.get("ticker", "").upper(),
        cik=client.get("cik", ""),
        role="client",
    )

    peers = [
        Company(
            name=p.get("name", ""),
            ticker=p.get("ticker", "").upper(),
            cik=p.get("cik", ""),
            role="peer",
        )
        for p in data.get("peers", [])
    ]

    return CoverageConfig(
        sector=data.get("sector", ""),
        client=client_company,
        peers=peers,
        event_weights=data.get("event_weights", {}),
        relevance_weights=data.get("relevance_weights", {}),
        rss_feeds=data.get("rss_feeds", []),
    )


def load_config() -> AppConfig:
    return AppConfig(settings=_load_settings(), coverage=_load_coverage())
