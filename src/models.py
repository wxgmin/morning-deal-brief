"""Shared data for collection, publication, and the dated archive."""
from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_EDGAR = "edgar"
SOURCE_RSS = "rss"
SOURCE_MARKET = "market"


@dataclass
class Company:
    name: str
    ticker: str
    cik: str = ""
    role: str = "peer"


@dataclass
class RawItem:
    source: str
    company: str
    ticker: str
    role: str
    title: str
    url: str
    published: str
    summary: str = ""
    form: str | None = None
    accession: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Event:
    item: RawItem
    event_type: str
    confidence: float = 1.0
    magnitude: float = 0.0
    source_text: str = ""
    also_reported_by: list = field(default_factory=list)
    score: float = 0.0


@dataclass
class Judgment:
    summary: str
    why_it_matters: str
    figures_cited: list = field(default_factory=list)
    confidence: float = 1.0
    grounded: bool = True
    ungrounded_figures: list = field(default_factory=list)
    method: str = ""


@dataclass
class BriefItem:
    rank: int
    ticker: str
    company: str
    event_type: str
    summary: str
    why_it_matters: str
    source_url: str
    figures: list = field(default_factory=list)
    confidence: float = 1.0
    source: str = ""
    publisher: str = ""
    published_at: str = ""
    role: str = ""
    evidence: dict = field(default_factory=dict)
    related_sources: list = field(default_factory=list)


@dataclass
class PriceMove:
    ticker: str
    company: str
    role: str
    date: str
    close: float
    pct_change: float | None
    volume: float | None
    volume_z: float | None
    z_score: float | None
    large_move: bool
    evidence: dict = field(default_factory=dict)
