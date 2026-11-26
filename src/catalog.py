"""Built-in library: what a desk looks at first, grouped, with a plain-English reason for each group.

Everything here is a *default*. The user's profile (var/profile.json) can switch any instrument,
market, feed or section on or off, add custom ones, and re-order sections. Nothing in this file
contains a market level, so it can never go stale or become an unsourced figure.
"""
from __future__ import annotations

# kind: "yield"  level is a percent, change is shown in basis points
#       "vol"    volatility index level, change in points and percent
#       "fx" / "price" / "index" / "etf" / "crypto"  level + percent change
# region: only shown when that market is switched on in the profile (None = always on)

GROUPS = [
    {"id": "rates", "label": "US Treasury yields", "why":
        "The price of money. Higher yields raise discount rates and financing costs for everyone; "
        "the shape of the curve carries growth and policy expectations."},
    {"id": "dollar_fx", "label": "Dollar & FX", "why":
        "The dollar is the world's funding currency. A stronger dollar tightens global conditions and "
        "weighs on dollar-priced commodities and multinational earnings."},
    {"id": "commodities", "label": "Commodities", "why":
        "Oil feeds inflation and energy credit; gold prices fear and real rates; copper is the "
        "industrial-demand barometer."},
    {"id": "equity_futures", "label": "US equity futures", "why":
        "Index futures trade outside US cash hours and can reflect overnight developments."},
    {"id": "us_equities", "label": "US cash indices (prior close)", "why":
        "The latest available cash index prints. Futures changes use their own reference levels."},
    {"id": "volatility", "label": "Volatility", "why":
        "VIX reflects US equity volatility implied by options. MOVE tracks US Treasury volatility."},
    {"id": "global_equities", "label": "Global equities", "why":
        "Check local timestamps for the latest Asian, European and Americas session moves."},
    {"id": "credit", "label": "Credit & bond ETFs (proxies)", "why":
        "Bond ETF prices reflect interest rates as well as credit. See Capital markets for option-adjusted spreads."},
    {"id": "sectors", "label": "US sectors", "why":
        "Shows what is leading or lagging underneath the index; financials and regional banks matter most "
        "for a banking audience."},
    {"id": "crypto", "label": "Crypto", "why":
        "Trades around the clock, so it often shows risk appetite over weekends and overnight."},
]

MARKETS = [
    {"id": "us", "label": "United States"},
    {"id": "europe", "label": "Europe"},
    {"id": "asia", "label": "Asia-Pacific"},
    {"id": "americas", "label": "Canada & Latin America"},
]


def _i(symbol, label, group, kind="price", dp=2, enabled=True, pinned=False, region=None):
    return {"symbol": symbol, "label": label, "group": group, "kind": kind, "dp": dp,
            "enabled": enabled, "pinned": pinned, "region": region, "custom": False}


INSTRUMENTS = [
    # Rates (Yahoo's ^-yield indices quote the yield itself, in percent)
    _i("^IRX", "US 3-month bill", "rates", "yield", 3),
    _i("^FVX", "US 5-year", "rates", "yield", 3),
    _i("^TNX", "US 10-year", "rates", "yield", 3, pinned=True),
    _i("^TYX", "US 30-year", "rates", "yield", 3),
    # Dollar & FX
    _i("DX-Y.NYB", "US Dollar Index (DXY)", "dollar_fx", "fx", 3, pinned=True),
    _i("EURUSD=X", "EUR/USD", "dollar_fx", "fx", 4),
    _i("JPY=X", "USD/JPY", "dollar_fx", "fx", 2),
    _i("GBPUSD=X", "GBP/USD", "dollar_fx", "fx", 4),
    _i("CNY=X", "USD/CNY", "dollar_fx", "fx", 4),
    _i("CHF=X", "USD/CHF", "dollar_fx", "fx", 4),
    _i("AUDUSD=X", "AUD/USD", "dollar_fx", "fx", 4, enabled=False),
    _i("CAD=X", "USD/CAD", "dollar_fx", "fx", 4, enabled=False),
    _i("MXN=X", "USD/MXN", "dollar_fx", "fx", 3, enabled=False),
    # Commodities
    _i("CL=F", "WTI crude", "commodities", "price", 2, pinned=True),
    _i("BZ=F", "Brent crude", "commodities", "price", 2),
    _i("NG=F", "Natural gas", "commodities", "price", 3),
    _i("GC=F", "Gold", "commodities", "price", 1, pinned=True),
    _i("SI=F", "Silver", "commodities", "price", 2),
    _i("HG=F", "Copper", "commodities", "price", 3),
    _i("ZW=F", "Wheat", "commodities", "price", 2, enabled=False),
    # Futures
    _i("ES=F", "S&P 500 futures", "equity_futures", "index", 2, region="us"),
    _i("NQ=F", "Nasdaq-100 futures", "equity_futures", "index", 2, region="us"),
    _i("YM=F", "Dow futures", "equity_futures", "index", 0, region="us"),
    _i("RTY=F", "Russell 2000 futures", "equity_futures", "index", 1, region="us"),
    # US cash indices
    _i("^GSPC", "S&P 500", "us_equities", "index", 2, region="us"),
    _i("^IXIC", "Nasdaq Composite", "us_equities", "index", 2, region="us"),
    _i("^DJI", "Dow Jones", "us_equities", "index", 2, region="us"),
    _i("^RUT", "Russell 2000", "us_equities", "index", 2, region="us"),
    # Volatility
    _i("^VIX", "VIX", "volatility", "vol", 2),
    _i("^MOVE", "MOVE (Treasury vol)", "volatility", "vol", 2),
    _i("^VIX9D", "VIX 9-day", "volatility", "vol", 2, enabled=False),
    _i("^VIX3M", "VIX 3-month", "volatility", "vol", 2),
    # Global equities (shown per market)
    _i("^STOXX50E", "Euro Stoxx 50", "global_equities", "index", 2, region="europe"),
    _i("^FTSE", "FTSE 100", "global_equities", "index", 2, region="europe"),
    _i("^GDAXI", "DAX", "global_equities", "index", 2, region="europe"),
    _i("^FCHI", "CAC 40", "global_equities", "index", 2, region="europe"),
    _i("^IBEX", "IBEX 35", "global_equities", "index", 2, enabled=False, region="europe"),
    _i("^AEX", "AEX", "global_equities", "index", 2, enabled=False, region="europe"),
    _i("^N225", "Nikkei 225", "global_equities", "index", 2, region="asia"),
    _i("^HSI", "Hang Seng", "global_equities", "index", 2, region="asia"),
    _i("000001.SS", "Shanghai Composite", "global_equities", "index", 2, region="asia"),
    _i("^KS11", "KOSPI", "global_equities", "index", 2, region="asia"),
    _i("^AXJO", "ASX 200", "global_equities", "index", 2, region="asia"),
    _i("^NSEI", "Nifty 50", "global_equities", "index", 2, region="asia"),
    _i("^BSESN", "BSE Sensex", "global_equities", "index", 2, enabled=False, region="asia"),
    _i("^TWII", "Taiwan Weighted", "global_equities", "index", 2, enabled=False, region="asia"),
    _i("^GSPTSE", "S&P/TSX Composite", "global_equities", "index", 2, region="americas"),
    _i("^BVSP", "Bovespa", "global_equities", "index", 0, region="americas"),
    _i("^MXX", "S&P/BMV IPC", "global_equities", "index", 2, region="americas"),
    # Credit & bond proxies (ETF prices, NOT spreads)
    _i("HYG", "High-yield bonds (HYG)", "credit", "etf", 2),
    _i("LQD", "Investment-grade bonds (LQD)", "credit", "etf", 2),
    _i("TLT", "Long Treasuries (TLT)", "credit", "etf", 2),
    _i("IEF", "7-10Y Treasuries (IEF)", "credit", "etf", 2),
    _i("JNK", "High-yield bonds (JNK)", "credit", "etf", 2, enabled=False),
    _i("EMB", "EM dollar bonds (EMB)", "credit", "etf", 2, enabled=False),
    # US sectors
    _i("XLF", "Financials (XLF)", "sectors", "etf", 2, region="us"),
    _i("KRE", "Regional banks (KRE)", "sectors", "etf", 2, region="us"),
    _i("XLE", "Energy (XLE)", "sectors", "etf", 2, region="us"),
    _i("XLK", "Technology (XLK)", "sectors", "etf", 2, region="us"),
    _i("XLV", "Health care (XLV)", "sectors", "etf", 2, region="us"),
    _i("XLI", "Industrials (XLI)", "sectors", "etf", 2, region="us"),
    _i("XLY", "Consumer discretionary (XLY)", "sectors", "etf", 2, region="us"),
    _i("XLP", "Consumer staples (XLP)", "sectors", "etf", 2, region="us"),
    _i("XLU", "Utilities (XLU)", "sectors", "etf", 2, region="us"),
    _i("XLRE", "Real estate (XLRE)", "sectors", "etf", 2, enabled=False, region="us"),
    _i("XLB", "Materials (XLB)", "sectors", "etf", 2, enabled=False, region="us"),
    _i("XLC", "Communication services (XLC)", "sectors", "etf", 2, enabled=False, region="us"),
    # Crypto
    _i("BTC-USD", "Bitcoin", "crypto", "crypto", 0),
    _i("ETH-USD", "Ether", "crypto", "crypto", 1),
]

# News. Publisher feeds first; Google News topic searches fill the deal / ECM / credit gaps that
# have no free publisher feed. "category" only affects display; themes are tagged from headlines.
FEEDS = [
    {"id": "cnbc-top", "label": "CNBC · Top news", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "category": "general", "enabled": True},
    {"id": "cnbc-economy", "label": "CNBC · Economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "category": "macro", "enabled": True},
    {"id": "cnbc-finance", "label": "CNBC · Finance", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "category": "markets", "enabled": True},
    {"id": "cnbc-energy", "label": "CNBC · Energy", "url": "https://www.cnbc.com/id/19836768/device/rss/rss.html", "category": "commodities", "enabled": True},
    {"id": "mw-top", "label": "MarketWatch · Top stories", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories", "category": "markets", "enabled": True},
    {"id": "mw-pulse", "label": "MarketWatch · MarketPulse", "url": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse", "category": "markets", "enabled": True},
    {"id": "wsj-markets", "label": "WSJ · Markets", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "category": "markets", "enabled": True},
    {"id": "fed-press", "label": "Federal Reserve · Press releases", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "category": "policy", "enabled": True},
    {"id": "fed-speeches", "label": "Federal Reserve · Speeches", "url": "https://www.federalreserve.gov/feeds/speeches.xml", "category": "policy", "enabled": True},
    {"id": "bea-releases", "label": "BEA · Economic releases", "url": "https://apps.bea.gov/rss/rss.xml", "category": "macro", "enabled": True},
    {"id": "boe-news", "label": "Bank of England · News", "url": "https://www.bankofengland.co.uk/rss/news", "category": "policy", "enabled": False},
    {"id": "oilprice", "label": "OilPrice.com", "url": "https://oilprice.com/rss/main", "category": "commodities", "enabled": False},
    {"id": "bbc-business", "label": "BBC · Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "category": "general", "enabled": False},
    {"id": "ecb-press", "label": "ECB · Press", "url": "https://www.ecb.europa.eu/rss/press.html", "category": "policy", "enabled": False},
    {"id": "sec-press", "label": "SEC · Press releases", "url": "https://www.sec.gov/news/pressreleases.rss", "category": "policy", "enabled": False},
    {"id": "gn-mna", "label": "Deals · M&A and buyouts (Google News)", "category": "deals", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28merger+OR+acquisition+OR+takeover+OR+buyout%29+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-ecm", "label": "Capital markets · IPOs and offerings (Google News)", "category": "deals", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28IPO+OR+%22follow-on+offering%22+OR+%22convertible+notes%22%29+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-credit", "label": "Credit · loans, high yield, private credit (Google News)", "category": "credit", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28%22leveraged+loan%22+OR+%22high-yield%22+OR+%22private+credit%22+OR+downgrade%29+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-geo", "label": "Geopolitics and trade (Google News)", "category": "macro", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28tariffs+OR+sanctions+OR+OPEC+OR+%22central+bank%22%29+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-global-tape", "label": "Overnight markets · Asia and Europe (Google News)", "category": "markets", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28%22Asian+stocks%22+OR+%22European+stocks%22+OR+%22market+wrap%22%29+when%3A3d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-rates-fx", "label": "Bonds · Dollar and yields (Google News)", "category": "macro", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28%22Treasury+yields%22+OR+%22dollar+index%22+OR+%22rate+expectations%22%29+when%3A3d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-oil-gold", "label": "Commodities · Crude and gold (Google News)", "category": "commodities", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28%22oil+prices%22+OR+%22gold+prices%22+OR+%22crude+inventories%22%29+when%3A3d&hl=en-US&gl=US&ceid=US%3Aen"},
    # Added after the 2026-11-24 source survey (every URL returned HTTP 200 with same-day items).
    {"id": "bbg-markets", "label": "Bloomberg · Markets (headlines)", "url": "https://www.bloomberg.com/feeds/markets/news.rss", "category": "markets", "enabled": True},
    {"id": "bbg-economics", "label": "Bloomberg · Economics (headlines)", "url": "https://www.bloomberg.com/feeds/economics/news.rss", "category": "macro", "enabled": True},
    {"id": "ft-markets", "label": "Financial Times · Markets (headlines)", "url": "https://www.ft.com/markets?format=rss", "category": "markets", "enabled": True},
    {"id": "ft-mna", "label": "Financial Times · M&A (headlines)", "url": "https://www.ft.com/mergers-acquisitions?format=rss", "category": "deals", "enabled": True},
    {"id": "prn-mna", "label": "PR Newswire · Mergers and acquisitions", "url": "https://www.prnewswire.com/rss/financial-services-latest-news/acquisitions-mergers-and-takeovers-list.rss", "category": "deals", "enabled": True},
    {"id": "sa-currents", "label": "Seeking Alpha · Market Currents", "url": "https://seekingalpha.com/market_currents.xml", "category": "markets", "enabled": True},
    {"id": "politico-money", "label": "Politico · Morning Money", "url": "https://rss.politico.com/morningmoney.xml", "category": "policy", "enabled": True},
    {"id": "wh-actions", "label": "White House · Presidential actions (tariffs, executive orders)", "url": "https://www.whitehouse.gov/presidential-actions/feed/", "category": "policy", "enabled": True},
    {"id": "bis-speeches", "label": "Bank for International Settlements · Central bank speeches", "url": "https://www.bis.org/doclist/cbspeeches.rss", "category": "policy", "enabled": False},
    {"id": "boj-news", "label": "Bank of Japan · What's new", "url": "https://www.boj.or.jp/en/rss/whatsnew.xml", "category": "policy", "enabled": False},
    {"id": "fedreg-export", "label": "Federal Register · Export controls (BIS)", "url": "https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau", "category": "policy", "enabled": False},
    {"id": "calc-risk", "label": "Calculated Risk · US economy", "url": "https://calculatedrisk.substack.com/feed", "category": "macro", "enabled": False},
    {"id": "investinglive", "label": "InvestingLive · FX and rates squawk", "url": "https://investinglive.com/feed/news", "category": "macro", "enabled": False},
    {"id": "hls-corpgov", "label": "Harvard Law · Corporate governance and deal memos", "url": "https://corpgov.law.harvard.edu/feed/", "category": "deals", "enabled": False},
    {"id": "techmeme", "label": "Techmeme · Tech and AI", "url": "https://www.techmeme.com/feed.xml", "category": "general", "enabled": False},
    {"id": "coindesk", "label": "CoinDesk · Crypto", "url": "https://www.coindesk.com/arc/outboundfeeds/rss", "category": "general", "enabled": False},
    {"id": "gn-ratings", "label": "Analyst upgrades and downgrades (Google News)", "category": "markets", "enabled": True,
     "url": "https://news.google.com/rss/search?q=%28upgrade+OR+downgrade%29+%28analyst+OR+%22price+target%22%29+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"},
    {"id": "gn-reuters", "label": "Reuters markets (via Google News)", "category": "markets", "enabled": True,
     "url": "https://news.google.com/rss/search?q=site%3Areuters.com+markets+when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"},
]

# Headline themes. Weight is how much a theme lifts a headline in the ranking (user-editable).
THEMES = {
    "market_wrap": {"label": "Market moves", "weight": 1.0, "keywords": [
        "stocks", "wall street", "s&p 500", "nasdaq", "dow jones", "futures", "selloff", "sell-off", "rally",
        "plunge", "surge", "tumble", "soar", "correction", "bear market", "volatility", "vix", "record high"]},
    "fed_rates": {"label": "Fed & rates", "weight": 1.0, "keywords": [
        "fed", "fomc", "powell", "rate cut", "rate hike", "interest rate", "treasury yield", "yields", "inflation",
        "cpi", "pce", "payrolls", "jobs report", "jobless", "central bank", "ecb", "bank of japan", "boj", "bank of england"]},
    "deals": {"label": "Deals & M&A", "weight": 0.95, "keywords": [
        "acquire", "acquisition", "merger", "takeover", "buyout", "to buy", "deal", "bid for", "lbo", "spin-off",
        "spinoff", "divest", "activist", "stake"]},
    "ecm": {"label": "IPO & equity capital markets", "weight": 0.85, "keywords": [
        "ipo", "public offering", "follow-on", "secondary offering", "convertible", "listing", "direct listing", "spac"]},
    "credit_banks": {"label": "Credit & banks", "weight": 0.9, "keywords": [
        "bank", "credit", "default", "downgrade", "upgrade", "junk", "high-yield", "private credit", "loan",
        "bankruptcy", "chapter 11", "spreads", "liquidity", "stress test", "capital rule"]},
    "energy_commodities": {"label": "Energy & commodities", "weight": 0.85, "keywords": [
        "oil", "crude", "opec", "natural gas", "lng", "gold", "copper", "silver", "brent", "wti", "refiner"]},
    "geopolitics_trade": {"label": "Geopolitics & trade", "weight": 0.8, "keywords": [
        "tariff", "sanction", "war", "iran", "russia", "ukraine", "china", "trade deal", "export ban", "embargo",
        "middle east", "taiwan"]},
    "earnings": {"label": "Earnings & guidance", "weight": 0.75, "keywords": [
        "earnings", "quarterly results", "guidance", "forecast", "profit", "revenue", "outlook", "eps", "beats", "misses"]},
    "tech_ai": {"label": "Tech & AI", "weight": 0.6, "keywords": [
        "ai ", "artificial intelligence", "nvidia", "chip", "semiconductor", "data center", "openai", "cloud"]},
    "crypto": {"label": "Crypto", "weight": 0.4, "keywords": ["bitcoin", "crypto", "ether", "stablecoin", "etf flows"]},
}

# Publishers whose headlines get full ranking weight. Aggregator results (Google News) from anyone else are
# discounted so an obscure site cannot outrank a wire service. Matched as lower-case substrings.
TRUSTED_PUBLISHERS = [
    "reuters", "bloomberg", "wall street journal", "wsj", "financial times", "ft.com", "cnbc", "marketwatch",
    "barron", "associated press", "ap news", "federal reserve", "sec.gov", "european central bank", "ecb", "bbc",
    "economist", "yahoo finance", "axios", "oilprice", "forbes", "fortune", "business insider", "the globe and mail",
    "nikkei", "south china morning post", "investopedia", "morningstar", "pitchbook", "mergermarket",
    "seeking alpha", "politico", "white house", "bank for international settlements", "bank of japan",
    "calculated risk", "investinglive", "harvard law", "federal register", "pr newswire", "techmeme", "coindesk",
]

# Order the brief reads top to bottom. Every id can be hidden or moved in Settings.
SECTIONS = [
    {"id": "summary", "label": "Top of the morning (summary)", "enabled": True},
    {"id": "heartbeats", "label": "Market heartbeats", "enabled": True},
    {"id": "tone", "label": "Risk tone", "enabled": True},
    {"id": "reads", "label": "Market read", "enabled": True},
    {"id": "signals", "label": "What this usually means (sourced signals)", "enabled": True},
    {"id": "ratepath", "label": "Fed path (market-implied odds)", "enabled": True},
    {"id": "calendar", "label": "Catalyst calendar", "enabled": True},
    {"id": "headlines", "label": "Headlines", "enabled": True},
    {"id": "watchlist", "label": "Watchlist quotes", "enabled": True},
    {"id": "capital", "label": "Capital markets", "enabled": True},
    {"id": "dealflow", "label": "Deal flow (SEC filings, market-wide)", "enabled": True},
    {"id": "stockmovers", "label": "Stock movers", "enabled": True},
    {"id": "deals", "label": "Filings & issuer news", "enabled": True},
    {"id": "movers", "label": "Unusual moves", "enabled": True},
    {"id": "curve", "label": "Treasury curve", "enabled": True},
    {"id": "positioning", "label": "Positioning & global curves", "enabled": True},
    {"id": "sentiment", "label": "Breadth & sentiment", "enabled": True},
    {"id": "economy", "label": "US economy & liquidity", "enabled": True},
    {"id": "boards", "label": "Market boards", "enabled": True},
]

CALENDAR_COUNTRIES = ["USD", "EUR", "GBP", "JPY", "CNY", "CAD", "AUD", "CHF", "NZD"]

# One-click watchlists. Users can still add or remove any name afterwards.
WATCHLIST_PRESETS = {
    "big_banks": {"label": "Big US banks", "sector": "Financials / Global banks", "names": [
        ("JPMorgan Chase & Co.", "JPM", "client"), ("Goldman Sachs Group", "GS", "peer"),
        ("Morgan Stanley", "MS", "peer"), ("Bank of America", "BAC", "peer"), ("Citigroup", "C", "peer"),
        ("Wells Fargo & Company", "WFC", "peer")]},
    "mega_tech": {"label": "Mega-cap tech", "sector": "Technology", "names": [
        ("Microsoft Corporation", "MSFT", "watch"), ("Apple Inc.", "AAPL", "watch"),
        ("NVIDIA Corporation", "NVDA", "watch"), ("Alphabet Inc.", "GOOGL", "watch"),
        ("Amazon.com, Inc.", "AMZN", "watch"), ("Meta Platforms", "META", "watch")]},
    "energy": {"label": "Energy majors", "sector": "Energy", "names": [
        ("Exxon Mobil Corporation", "XOM", "watch"), ("Chevron Corporation", "CVX", "watch"),
        ("ConocoPhillips", "COP", "watch"), ("Schlumberger", "SLB", "watch"), ("Occidental Petroleum", "OXY", "watch")]},
    "industrials": {"label": "Industrials (Graco & peers)", "sector": "Industrial / Fluid Handling & Flow Control", "names": [
        ("Graco Inc.", "GGG", "client"), ("IDEX Corporation", "IEX", "peer"), ("Nordson Corporation", "NDSN", "peer"),
        ("Lincoln Electric Holdings", "LECO", "peer"), ("Dover Corporation", "DOV", "peer"),
        ("Ingersoll Rand", "IR", "peer"), ("Fortive Corporation", "FTV", "peer")]},
}

DEFAULT_MARKETS = ["us", "europe", "asia"]

# One-click desk setups. Each changes layout, emphasis and sources only; the watchlist is never touched.
# "sections" is the reading order; sections not listed are kept but hidden.
ROLE_PRESETS = {
    "rates_fx": {
        "label": "Rates & FX trader",
        "blurb": "Fed path, curve, funding and global FX first; economic data across all major currencies.",
        "sections": ["summary", "heartbeats", "ratepath", "signals", "calendar", "curve", "tone", "movers",
                     "reads", "boards", "headlines"],
        "groups_on": ["rates", "dollar_fx", "volatility", "commodities", "credit"],
        "symbols_on": ["AUDUSD=X", "CAD=X", "MXN=X", "^VIX3M"],
        "markets": ["us", "europe", "asia"],
        "calendar": {"countries": ["USD", "EUR", "GBP", "JPY", "CNY", "CAD", "AUD", "CHF"], "min_impact": "Medium",
                     "earnings": False, "auctions": True},
        "themes": {"fed_rates": 1.5, "geopolitics_trade": 1.1, "market_wrap": 1.0, "energy_commodities": 0.9,
                   "credit_banks": 0.8, "deals": 0.3, "ecm": 0.3, "earnings": 0.4},
        "feeds_on": ["bbg-economics", "investinglive", "bis-speeches", "boj-news", "ecb-press", "gn-rates-fx"],
    },
    "equity_trader": {
        "label": "Equity trader",
        "blurb": "Futures, unusual moves, watchlist pre-market and earnings first; sectors and analyst actions.",
        "sections": ["summary", "heartbeats", "tone", "movers", "watchlist", "calendar", "headlines", "signals",
                     "boards", "ratepath", "reads"],
        "groups_on": ["equity_futures", "us_equities", "sectors", "volatility", "global_equities"],
        "symbols_on": ["XLRE", "XLB", "XLC", "^VIX3M"],
        "markets": ["us", "europe", "asia"],
        "calendar": {"countries": ["USD"], "min_impact": "Medium", "earnings": True, "auctions": False},
        "themes": {"market_wrap": 1.4, "earnings": 1.3, "tech_ai": 1.0, "deals": 0.9, "fed_rates": 0.9, "ecm": 0.7},
        "feeds_on": ["sa-currents", "gn-ratings", "bbg-markets", "techmeme"],
    },
    "coverage_banker": {
        "label": "Coverage / M&A banker",
        "blurb": "Your companies' filings and deal news first, then M&A wires, financing conditions and the tape.",
        "sections": ["summary", "deals", "headlines", "watchlist", "capital", "signals", "heartbeats", "calendar",
                     "tone", "ratepath"],
        "groups_on": ["rates", "credit", "volatility", "equity_futures", "sectors"],
        "symbols_on": [],
        "markets": ["us", "europe"],
        "calendar": {"countries": ["USD"], "min_impact": "High", "earnings": True, "auctions": False},
        "themes": {"deals": 1.6, "credit_banks": 1.1, "ecm": 1.0, "earnings": 1.0, "market_wrap": 0.8, "fed_rates": 0.7},
        "feeds_on": ["ft-mna", "prn-mna", "gn-mna", "hls-corpgov", "gn-ratings"],
    },
    "ecm_dcm": {
        "label": "ECM / DCM banker",
        "blurb": "Issuance windows first: IPO calendar, credit spreads, volatility, rates and auctions.",
        "sections": ["summary", "capital", "signals", "heartbeats", "tone", "ratepath", "curve", "calendar",
                     "headlines", "watchlist", "deals", "boards"],
        "groups_on": ["rates", "credit", "volatility", "equity_futures", "us_equities"],
        "symbols_on": ["JNK", "EMB", "^VIX3M"],
        "markets": ["us", "europe"],
        "calendar": {"countries": ["USD", "EUR"], "min_impact": "Medium", "earnings": True, "auctions": True},
        "themes": {"ecm": 1.6, "credit_banks": 1.4, "deals": 1.0, "fed_rates": 1.0, "market_wrap": 0.9},
        "feeds_on": ["gn-ecm", "gn-credit", "ft-mna", "bbg-markets"],
    },
    "research_pm": {
        "label": "Research / portfolio manager",
        "blurb": "The full cross-asset picture with every interpretation layer switched on.",
        "sections": ["summary", "heartbeats", "tone", "signals", "reads", "ratepath", "calendar", "headlines",
                     "movers", "curve", "watchlist", "capital", "boards", "deals"],
        "groups_on": ["rates", "dollar_fx", "commodities", "equity_futures", "us_equities", "volatility",
                      "global_equities", "credit", "sectors", "crypto"],
        "symbols_on": ["^VIX3M"],
        "markets": ["us", "europe", "asia", "americas"],
        "calendar": {"countries": ["USD", "EUR", "GBP", "JPY", "CNY"], "min_impact": "Medium", "earnings": True, "auctions": True},
        "themes": {},
        "feeds_on": ["bbg-economics", "calc-risk", "bis-speeches"],
    },
}
