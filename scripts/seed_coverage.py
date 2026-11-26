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
  cik: "0000042888"

peers:
  - name: "IDEX Corporation"
    ticker: "IEX"
    cik: "0000832101"
  - name: "Nordson Corporation"
    ticker: "NDSN"
    cik: "0000072331"
  - name: "Lincoln Electric Holdings"
    ticker: "LECO"
    cik: "0000059527"
  - name: "Snap-on Incorporated"
    ticker: "SNA"
    cik: "0000091440"
  - name: "Donaldson Company"
    ticker: "DCI"
    cik: "0000029644"
  - name: "Dover Corporation"
    ticker: "DOV"
    cik: "0000029905"
  - name: "Ingersoll Rand"
    ticker: "IR"
    cik: "0001699150"
  - name: "Fortive Corporation"
    ticker: "FTV"
    cik: "0001659166"

# Event-type weights for the banking angle (higher = more relevant to coverage).
event_weights:
  M&A: 1.0
  activist: 0.95
  ownership: 0.35
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

# Publisher RSS feeds are preferred. These Google News feeds are a demo fallback.
# Add verified company IR endpoints with type: press and ticker: SYMBOL.
rss_feeds:
  - url: "https://news.google.com/rss/search?q=Graco+Inc+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"
    type: news
  - url: "https://news.google.com/rss/search?q=%28IDEX+Corporation+OR+Nordson+OR+Lincoln+Electric+OR+Snap-on%29+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"
    type: news
  - url: "https://news.google.com/rss/search?q=%28Donaldson+Company+OR+Dover+Corporation+OR+Ingersoll+Rand+OR+Fortive%29+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"
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
