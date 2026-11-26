# Roadmap: the free Wall Street morning desk

Based on four research passes run 2026-11-24 (free data sources, free news feeds, a sourced signal rulebook,
and a gap analysis against Bloomberg, Koyfin, TradingView, Finviz, OpenBB and sell-side morning notes).
Every source listed as available was verified with a live request from this machine.

## Where a free tool can win, and where it cannot

**Can win:** one personalised, cited morning page that combines what Bloomberg spreads across TOP, ECO, WEI,
WIRP, CRVF and MA, plus plain-English "what this usually means" lines tied to published research.

**Cannot match (no free source):** licensed real-time prices (Yahoo is ~10–15 min delayed), survey consensus
for economic data (only the unofficial Forex Factory forecast), CDS/CDX, analyst rating feeds, earnings-call
transcripts at scale (Motley Fool only), fund flows, league tables, deal order books, chat. The provider layer
stays pluggable so a desk can add licensed feeds.

## Done in this pass

- **Sourced signal engine** (`src/signals.py`, `src/knowledge/signal_rules.json`): 47 researched rules and
  12 regimes. A rule fires only when live data meets its condition; figures come from the panels; thresholds
  the app chose are labelled APP_HEURISTIC; only re-verified citations are shown. Live today: VIX band,
  VIX/VIX3M inversion, breakeven move, IPO-window conditions; curve inversion, bull/bear steepener, 10Y shock,
  real-yield rise, dollar surge, yen carry unwind, oil shock, HY widening speed and four regimes are wired.
- **Funding and real yields** (`src/collectors/rates.py`): NY Fed EFFR/SOFR/TGCR/OBFR vs the target range;
  Treasury TIPS curve → 10Y/5Y real yields and breakevens with 1-day and 20-session changes.
- **VIX 3-month** added; HY/IG spreads now carry a 20-observation change.
- **18 new feeds** (Bloomberg and FT headlines, FT M&A, PR Newswire M&A, Seeking Alpha Market Currents,
  Politico Morning Money, White House presidential actions, BIS speeches, BoJ, export controls, Calculated Risk,
  InvestingLive, Harvard corporate-governance memos, Techmeme, CoinDesk, analyst-action and Reuters searches).
- **Reliability:** shared global (10) and per-host (4) connection limits, which fixed the earnings, IPO, auction
  and Fed-feed failures caused by bursts of ~30 parallel requests; one DNS retry for feeds.

## Phase 1: done (2026-11-24)

- **Top of the morning**: the first screen answers "what do I need to know?" in up to seven bullets, each assembled
  from panel figures and linked to its panel, beside the risk tone and a "since the last brief" table (`src/morning.py`).
- **Fed path**: market-implied odds and most likely range per 3-month window from the Atlanta Fed Market
  Probability Tracker, cached 12h (`src/collectors/rates_path.py`).
- **Economic surprises**: actual vs forecast with hotter/cooler/stronger/weaker read per indicator type, and a
  labelled "notable" rule (`src/surprise.py`). True standardised surprises need forecast-error history no free source has.
- **1-year context**: every quote carries its 1-year percentile, range and a range bar; extremes are called out.
- **Role setups**: five desk setups (rates/FX, equity, coverage/M&A, ECM/DCM, research/PM) plus starter
  watchlists, previewed on the form and only saved on Save (`profile.apply_role`, `/api/profile/apply`).
- **UI**: pinned section bar with scroll-spy, compact heartbeat cards with interpretation on demand, smart
  collapsing market boards ("biggest move" hint, auto-open when something is unusual), headline "show more",
  short desk names for signals, phone layout checked.

**Still open from Phase 1:** an AI-written narrative. The summary is deterministic today; an optional model may
rephrase it only if a checker confirms every figure matches the cited panel.

## Phase 2: done (2026-11-25)

- **AI explanations on every panel.** One model call returns the morning note plus a 1–2 sentence "AI read" for
  each panel (what the numbers mean together and their usual knock-on effects). Each panel read is checked on its
  own: it must cite facts from that panel, every figure must appear in the cited facts, and forecasts, advice and
  links are rejected. A failing panel is dropped; the others still publish. Live run: 12 of 12 panels passed.
- **Ask this brief.** A question box answers from the edition's facts with clickable citations, through the same
  checker; it says "the brief doesn't contain that" rather than guessing (`/api/ask`, `narrative.answer`).
- **Alerts.** Watchlist moves (regular, pre-market, after-hours), new signals, notable data surprises, watchlist
  names in SEC deal filings and user keywords; shown as a strip on the brief, editable in Preferences, and pushed to
  a phone via ntfy with `run.py --alerts` (each alert once per day) (`src/alerts.py`).
- **Positioning.** CFTC Commitments of Traders for 11 markets (S&P, Nasdaq, 2Y/10Y, dollar, yen, euro, VIX, WTI,
  gold, copper): net, weekly change and 3-year percentile, labelled by history ("least short in 3 years") rather
  than direction; cached 12h because the data is weekly (`collectors/positioning.py`).
- **Stock movers.** Biggest large-cap gainers, decliners and most active with relative volume and pre/post moves
  (`collectors/movers.py`).
- **Global curves.** JGB 10Y (MoF), euro-area AAA 10Y (ECB) and UK gilt 10Y (BoE), plus the WTI futures curve
  (front, second, twelfth month) with the sourced backwardation/contango rule R16 (`collectors/global_rates.py`).
- **More sourced signals.** Treasury volatility (MOVE vs its 1-year range, R10) and gold strength (R18).

## Phase 3: done (2026-11-25)

- **Deal monitor, market-wide.** Merger agreements (8-K Item 1.01 via EDGAR full-text search), IPO registrations
  (S-1/F-1), priced offerings (424B4), tender offers (SC TO-T, by target), activist 13D stakes (by target) and merger
  proxies (DEFM14A); every row links to the filing and watchlist names are highlighted and alerted
  (`collectors/dealflow.py`).
- **Insider trades (Form 4).** Open-market purchases and sales only (grants and exercises excluded), rolled up per
  company; three or more insiders buying is flagged and alerted as a cluster buy. First live run surfaced a 7-insider
  cluster buy at Cytosorbents (CEO, CFO, President, directors).

## Second critique pass (2026-11-25): closed

- **Earnings versus consensus**: reported EPS against the estimate with surprise % and beat/miss, including last
  night's after-close reporters (previous-day calendar); results feed the summary, alerts facts and the AI layer.
- **History charts**: every quote keeps a weekly-sampled 1-year series; clicking a sparkline opens a labelled chart.
- **Signal coverage**: 8 more researched rules wired (Fed repricing R09, copper/gold R17, bitcoin R19, IG spreads
  R25, credit-vs-equity divergence R27, positioning extremes R32, defensive rotation R34, regional banks R35):
  25 of 47 rules plus 4 regimes. Where the app's inputs differ from the research (1-year instead of 5-year
  percentiles, no 60-day correlation, Atlanta Fed instead of CME), the signal's threshold note says so.
- **Ordinals**: one helper now renders 1st/2nd/3rd/22nd everywhere (the app had printed "1th", "92th").
- **Speed**: running the two model calls in parallel was tried and reverted; one run lost the morning note, and
  reliability beats 15 seconds. CFTC data is cached for 12 hours instead.

## Third pass (2026-11-25): closed

- **Market breadth, computed.** % of S&P 500 stocks above their 50- and 200-day averages, advancers/decliners,
  new 52-week highs/lows and the 10-day advancing-share EMA, from all 503 constituents' daily closes (cached 6h).
- **Sentiment.** CNN Fear & Greed and its seven components, including the put/call ratio.
- **Three more rules**: breadth washout/narrow participation (R28), breadth thrust (R29), Fear & Greed extremes
  (R33). Coverage is now **28 of 47 rules plus 4 regimes**.
- **Faster alert loop.** `run.py --alerts` uses a quick mode that skips once-a-day sources and all model calls:
  14 seconds instead of ~60.
- **AI reliability.** The growing note (64 facts, 13 panels) was hitting the ChatGPT client's 38-second stream cap
  and silently falling back. Panel reads are now one sentence each and the cap is 75 seconds; the note and all 13
  panel reads now return in about 32 seconds.

## Fourth pass (2026-11-25): closed

- **US economy & liquidity panel** from FRED/ALFRED, keyless (one series per request, no User-Agent): jobless
  claims with 4-week average vs 12-month low, unemployment, the real-time Sahm indicator, the Kim-Wright term
  premium, bank reserves, overnight reverse repo, IORB, the 2-year yield, and HY/IG spread percentiles over ~3 years.
- **Eight more rules**: curve dis-inversion (R03), term-premium selloff (R08), money-market liquidity (R12),
  HY spreads vs history (R23), IG spreads vs history (R25), small caps vs rates (R36), hawkish data reaction
  (R38), jobless claims (R41) and the Sahm rule (R42). Coverage is now **36 of 47 rules plus 4 regimes**.
  First live run: R12 fired (SOFR 3.92% above IORB 3.90%).

## Fifth pass (2026-11-25): delivery parity

- **Email, Markdown and Slack now carry everything the dashboard shows.** The Markdown/email brief gained the deal
  flow, stock movers, positioning, breadth & sentiment and economy panels, and every panel's checked AI read sits
  under its heading (14 in the latest edition). Slack now leads with the AI morning note and alerts, then deal-flow
  highlights (including insider cluster buys), economy and sentiment lines; its block cap rose from 12 to 24.

- **Interactive charts.** The chart popup now switches between 1M, 6M, 1Y and 5Y through a validated, read-only
  `/api/chart` endpoint. A live check caught yield charts reporting "+9,593%" for a move from 0.03% to 3%; yield
  changes are now shown in basis points ("+413bp").

## Critique: what is still weak, and what was done about it

Done in this pass after reviewing the build critically:
- Two summaries ("Before the open" and the AI note) said the same things. The desk read now collapses behind
  "Show the desk read" whenever a checked AI note exists.
- The first live run dropped the positioning panel read for advice-like wording; the prompt now forbids
  instructions, predictions and recommendations explicitly, and the next run kept all 12 panels.
- "New signal" alerts flooded when the previous edition predated the signal engine. Signals are now compared only
  when the prior edition carried them, and new signals collapse into one alert.
- Positioning labels said "crowded long" for a net-short position at a 3-year high. They now describe history.
- MOVE and gold had data but no interpretation; both rules are now wired.
- Insider buying was on the "still weak" list; it is now built, with cluster detection, because it was cheap and
  is one of the few free signals bankers actually watch.

Still weak (honest list, in priority order):
1. **A full brief takes about 60 seconds** (two AI calls, EDGAR issuer pipeline, ~70 one-year quote histories). Fine
   for the 7:45 run, too slow for an on-demand refresh. Next: an intraday "markets only" refresh that reuses cached
   daily sources (curve, positioning, rate path, deal flow) and re-pulls only quotes, movers and headlines.
2. **Economic consensus is unofficial.** Forecasts come from Forex Factory; actuals are official. Licensed survey
   consensus (Bloomberg, Econoday, Trading Economics) is the only real fix, and the provider layer can take one.
3. **Charts have no intraday view.** Click-to-expand charts switch between 1M, 6M, 1Y and 5Y (fetched on demand,
   cached 10 minutes, yields shown in basis points); intraday charts would need a real-time feed.
4. **Signal coverage is 38 of 47 researched rules plus 4 regimes.** Every remaining rule needs data that is
   proprietary or blocked, not code: ISM PMI and new orders (R39, R40) and Conference Board expectations and LEI
   (R43, R44) are licensed; leveraged-loan prices (R47) are paid; AAII sentiment (R31) sits behind a bot wall;
   2-year put/call history (R30) is no longer served to scripts; a standardised surprise (R37) needs forecast-error
   history; auction tails (R11) need when-issued yields. The excess bond premium (R45, Fed research file) and
   credit ETFs vs Treasuries (R26, re-sourced to the BIS and NBER after its product-page citations were found not
   to support the claim) were wired in the final pass.
5. **Earnings depth.** EPS actual-vs-estimate is built; revenue consensus, guidance and call transcripts have no
   free structured source (Motley Fool transcripts exist as articles only).
6. **Unofficial endpoints** (Yahoo, Nasdaq, Forex Factory, Google News) can break without notice and are not
   licensed for redistribution. Government sources are the durable backbone; keep the pluggable providers.
7. **The AI checker verifies figures, not judgement.** A sentence can pass every number check and still stress the
   wrong thing. Mitigations: facts are ordered by importance, effects must be phrased as tendencies, and every
   claim links to its panel so a reader can verify it in one click.

## Rules for every addition

1. Every figure traces to a named source and as-of time; unofficial endpoints are labelled.
2. Interpretation is a sourced historical tendency, never a forecast; app-chosen thresholds are labelled.
3. A failing source degrades one panel and is shown in source health; it never blocks the brief.
4. Headlines are shown as headline + link only; no republishing of paywalled text.
