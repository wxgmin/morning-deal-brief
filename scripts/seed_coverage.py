#!/usr/bin/env python
"""Regenerate the default demo coverage list at config/coverage.yaml."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "config" / "coverage.yaml"

DEFAULT = """# Morning Deal Brief — coverage list.
# Edit this file to change the client, peers, sector, feeds, and event weights.
# CIK is optional: if blank, it is resolved from the SEC company_tickers.json map
# at run time using the ticker.

sector: "Industrial / Fluid Handling & Flow Control"

client:
  name: "Graco Inc."
  ticker: "GGG"
  cik: ""

peers:
  - name: "IDEX Corporation"
    ticker: "IEX"
    cik: ""
  - name: "Nordson Corporation"
    ticker: "NDSN"
    cik: ""
  - name: "Lincoln Electric Holdings"
    ticker: "LECO"
    cik: ""
  - name: "Snap-on Incorporated"
    ticker: "SNA"
    cik: ""
  - name: "Donaldson Company"
    ticker: "DCI"
    cik: ""
  - name: "Dover Corporation"
    ticker: "DOV"
    cik: ""
  - name: "Ingersoll Rand"
    ticker: "IR"
    cik: ""
  - name: "Fortive Corporation"
    ticker: "FTV"
    cik: ""

# Event-type weights for the banking angle (higher = more relevant to coverage).
event_weights:
  M&A: 1.0
  activist: 0.95
  rating_change: 0.9
  financing: 0.85
  management_change: 0.8
  guidance_change: 0.75
  earnings: 0.7
  large_price_move: 0.65
  other: 0.2

# Relevance weight by relationship to the coverage list.
relevance_weights:
  client: 1.0
  peer: 0.8
  sector: 0.5

# News / press-release RSS feeds (live enrichment; RSS is not backfillable).
rss_feeds:
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GGG&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=IEX&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NDSN&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=LECO&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SNA&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DCI&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DOV&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=IR&region=US&lang=en-US"
    type: news
  - url: "https://feeds.finance.yahoo.com/rss/2.0/headline?s=FTV&region=US&lang=en-US"
    type: news
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the demo coverage list.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing coverage.yaml")
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(f"{TARGET} already exists; use --force to overwrite.")
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(DEFAULT, encoding="utf-8")
    print(f"Wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
