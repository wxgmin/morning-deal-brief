"""Shared data models for the Morning Deal Brief pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SOURCE_EDGAR = "edgar"
SOURCE_RSS = "rss"
SOURCE_MARKET = "market"


@dataclass
class Company:
    name: str
    ticker: str
    cik: str = ""
    role: str = "peer"  # client | peer | sector


@dataclass
class RawItem:
    """A single collected data point before detection/judgment."""
    source: str
    company: str
    ticker: str
    role: str
    title: str
    url: str
    published: str  # ISO date, YYYY-MM-DD
    summary: str = ""
    form: Optional[str] = None
    accession: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class Event:
    """A RawItem that has been tagged with an event type and scored."""
    item: RawItem
    event_type: str
    confidence: float = 1.0
    magnitude: float = 0.0
    source_text: str = ""
    also_reported_by: list = field(default_factory=list)


@dataclass
class Judgment:
    """LLM (or fallback) output for a single item, before grounding check."""
    summary: str
    why_it_matters: str
    figures_cited: list = field(default_factory=list)
    confidence: float = 1.0
    grounded: bool = True
    ungrounded_figures: list = field(default_factory=list)


@dataclass
class BriefItem:
    """A fully-verified item that renders in the brief."""
    rank: int
    ticker: str
    company: str
    event_type: str
    summary: str
    why_it_matters: str
    source_url: str
    figures: list = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class PriceMove:
    """One company's daily price/volume snapshot for the coverage table."""
    ticker: str
    company: str
    role: str
    date: str
    close: float
    pct_change: float
    volume: float
    volume_z: float
    z_score: float
    large_move: bool
