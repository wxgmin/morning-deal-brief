# Morning Deal Brief

A morning briefing for traders and banking coverage teams. The market snapshot includes cross-asset prices, economic catalysts, headlines, credit and IPO activity. The issuer brief adds SEC filings, news and prior-session moves for the watchlist. Optional delivery sends the edition to Slack, email, or both.

## Run locally

Requires Python 3.11 or newer. Create a virtual environment, activate it, and install the dependencies.

~~~text
python -m venv .venv
python -m pip install -r requirements.txt
python -m pytest -q
python run.py --dry-run
python run.py --backfill 14
python app.py
~~~

On Windows, you can use `.venv\Scripts\python.exe` in place of `python` after creating the environment. On macOS and Linux use `.venv/bin/python`. The dashboard listens at [http://127.0.0.1:5000](http://127.0.0.1:5000). The checkout includes a small, sourced replay so the dashboard works offline on first launch.

From the dashboard, open **Preferences** to search for companies, add a ticker manually, remove names, and assign a focus company or peers. Change the regions, included instruments, heartbeat pins, feeds, calendar and section order from the same page. **Save & collect** applies the changes immediately to a fresh edition. Archived editions retain the data and watchlist used when they were collected. The CLI and scheduler read the same profile at the start of each collection.

The profile defaults to `var/profile.json` (next to the archive directory). Set `PROFILE_FILE` to share an explicit profile path with a service account. `config/coverage.yaml` seeds the default watchlist and supplies issuer ranking weights. An explicit `COVERAGE_FILE` takes precedence for issuer filings, news and closes, while the profile still drives the market snapshot. Set a real contact in `EDGAR_IDENTITY` before scheduling production runs. The watchlist accepts exchange-qualified Yahoo tickers. SEC and issuer session coverage are US-oriented, and international single-name quotes use the market snapshot.

## How the morning brief is organized

**Before the open** brings the day's priority market moves, releases, headlines and financing signals to the top of the edition. Each heartbeat can show a linked, timestamped possible catalyst followed by a conditional read for investors or financing teams. If the collected reporting gives no clear catalyst, the brief says so.

| Desk check | Evidence in the brief |
| --- | --- |
| Four market heartbeats | US 10-year Treasury yield, ICE US Dollar Index, WTI crude futures and gold futures open the default brief. Each shows its own timestamp and reference change. Daily Treasury par yields and the 2s10s spread are shown separately. Treasury takes indicative closing quotes around 3:30 p.m. ET. [US Treasury](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics) · [ICE Dollar Index](https://www.ice.com/forex/usdx) |
| Overnight risk and sector leadership | US equity futures, major regional equity markets, FX crosses, volatility and sectors show where moves are concentrated. The rule-based read labels market relationships as tendencies, and observations may come from different trading sessions. [CME equity futures](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.html) · [Cboe VIX](https://www.cboe.com/tradable-products/vix/) |
| What can move the next session | The catalyst calendar defaults to a 14-day U.S. view. Choose a window of up to 30 days and add regions in Preferences. U.S. schedules draw on BLS, BEA, Census, the Federal Reserve, EIA, ISM and targeted FRED release calendars. The euro area, UK and Canada have official statistics and central-bank schedules. The remaining supported currencies use a supplementary weekly vendor feed. Forecasts are shown where the vendor supplies them. |
| Financing conditions and deal activity | Daily investment-grade and high-yield option-adjusted spreads come from ICE BofA via FRED. Bond ETFs are quoted separately. Nasdaq’s IPO calendar marks expected dates as estimates. Attributed financing, equity issuance and M&A headlines supplement watchlist disclosures. [Investment-grade OAS](https://fred.stlouisfed.org/series/BAMLC0A0CM) · [High-yield OAS](https://fred.stlouisfed.org/series/BAMLH0A0HYM2) · [Nasdaq IPO calendar](https://www.nasdaq.com/market-activity/ipos) · [J.P. Morgan capital markets](https://jpmorgan.com/solutions/cib/investment-banking/capital-markets) |
| Issuer developments | Watchlist quotes include extended-hours prints when available. The issuer brief prioritizes primary disclosures and keeps attribution, dates and terms in the evidence. [SEC EDGAR submissions](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) |

The market snapshot is collected on demand or by the scheduler. Free quotes can be delayed or reflect a prior close. IPO dates are provisional, and public headlines only cover reported deals. Source times and coverage status appear in the brief. The app is a briefing and research tool.

**Calendar coverage** lists each source and its status. An unavailable schedule can use overlapping dates fetched within the previous two days, marked with their last check time. Official calendars can change and publish different distances into the future. Keep the source link for checking a critical release; the tracker’s calendar does not include a licensed stream of actuals and consensus.

## Optional ChatGPT plan

On the loopback dashboard, open **Preferences → ChatGPT → Continue with ChatGPT** and approve plan usage. After sign-in, **Collect brief** gathers the evidence, runs one editorial request using an available account model, archives the result, and opens the edition. The same operating-system user’s scheduled collections also use the connection. You can sign out or review usage from Preferences. The built-in desk read works without a connection and takes over if usage or inference is unavailable. [OpenAI Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source) · [ChatGPT usage settings](https://chatgpt.com/settings/usage)

ChatGPT chooses from attributed public evidence and approved conditional interpretations. It does not write figures or assert what caused a move. The request does not send account details, archive contents or watchlist configuration. OAuth credentials are encrypted on disk using a key in the OS credential store. The issuer evidence-selection provider configured by `LLM_PROVIDER` remains independent.

## Evidence and timing

- **Published excerpts** retain original source wording, with a link to the source. An 8-K can surface multiple disclosures, including financing, personnel changes, bankruptcy, impairment and financial-statement non-reliance. For specified high-risk disclosures, the brief keeps the section together so adjacent qualifications stay with the reported figures. Sections that exceed the excerpt limit can fall back to the filing record. Exhibit 99.1 can supply earnings or guidance and a separate buyback. A referenced earnings exhibit that cannot be retrieved marks SEC coverage partial. News excerpts are publisher-attributed headlines. Analyst follow-ups are approved questions or actions associated with the event.
- **Model selection is optional.** When enabled, a model can select an excerpt and a follow-up by index. Its response never supplies publication text or figures. Invalid responses or a provider error switch the rest of the edition to deterministic selection. Keep `LLM_PROVIDER=none` for offline or restricted environments. For custom endpoints and model availability, set `LLM_MODEL` explicitly.
- **The reporting window** includes source items with reliable timestamps after the previous scheduled weekday cutoff and through the current cutoff. Filings use SEC acceptance time. After a missed weekday, live runs with a prior finalized edition recover up to five missed reporting windows. The brief marks the wider window and any older gap. Recovery uses the latest market session and whatever news the feeds still retain. The dashboard and downloads label editions collected before cutoff as previews. Dates use the configured IANA time zone, including DST.
- **Market figures** use the previous XNYS session, including holidays and early closes. Daily return uses adjusted closes. Return and volume z-scores compare against the preceding 60 sessions, excluding the scored day. The archive keeps the current inputs and baseline bars. A gap or insufficient history leaves the affected statistic blank. The dashboard’s peer comparison includes peers with the client’s provider, currency, and return basis.
- **Replay** uses the same window with historical filing and market data. Runs are deterministic and use the provider’s historical series as served at replay time, which may include revised prices or adjustments. RSS is excluded from replays. Replay never sends delivery or replaces a live edition.
- **Health and archive** appear per source and issuer or feed. A failed feed or a missing symbol is visible even on a quiet day. An edition with no available sources is archived as unavailable and exits with an error. `var/archive/YYYY-MM-DD/events.json` is the current manifest. The matching HTML and Markdown are under `runs/<revision>/`. Each completed edition is published via one atomic manifest update. The dashboard can export the full evidence JSON. Earlier bundled examples under `archive/` use the original schema.

## Market and news sources

Yahoo’s chart endpoint works without a key and is the default for the bundled US coverage list. For a scheduled desk, configure an entitled and licensed provider, preferably Alpaca SIP with credentialed historical bars (`MARKET_PROVIDER=alpaca`, `ALPACA_FEED=sip`). Set `MARKET_FALLBACK` to the alternate provider if its terms and basis are acceptable. The fallback can supply a more complete row for the same session when the primary baseline has gaps. Source and adjustment basis appear on each row. IEX is one venue and its volume is venue-specific. Yahoo returns use its total-return adjusted close. Alpaca uses split adjustment. The pipeline never substitutes an older session for a missing bar. See [Alpaca market data plans](https://docs.alpaca.markets/us/docs/about-market-data-api), [Alpaca historical bars](https://docs.alpaca.markets/us/reference/stockbars), and the [NYSE calendar](https://www.nyse.com/trade/hours-calendars).

Configure official issuer feeds as `type: press` with a `ticker`. General `news` feeds require recognizable coverage names in their headlines. Use `type: sector` for a feed deliberately scoped to the sector. The sample Google News RSS URLs are convenient for the demo and may change. For recurring institutional distribution, verify provider coverage, entitlement, redistribution rights, and archive retention for quotes and bars. SEC submissions are retrieved from the official endpoint with a paced, identified client. See the [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [SEC access guidelines](https://www.sec.gov/about/developer-resources).

### Expected failure cases

| Situation | Pipeline behavior |
| --- | --- |
| DNS errors, rate limiting, truncated documents, invalid payloads | Bounded requests, retries for transient reads, and visible source health. A missing filing body falls back to the filing record. |
| New listing, halted name, missing venue data, incomplete baseline | Missing session or unavailable statistics are visible. Overlapping histories stay within one provider for each calculation. |
| Provider revisions, ticker changes, cross-listings, symbols reused by another issuer | Keep the coverage configuration and CIKs current. Archived inputs preserve the dated calculation. Historical market data cannot recover a ticker-to-issuer mapping the operator never captured. |
| Wrong category, generic news, syndication, repeat stories | Item-level filing rules, headline attribution, conservative deduplication, client material-event priority, and source links. Review high-stakes classifications and thin coverage in the dashboard. |
| Untrusted or unauthenticated web request | Dashboard binds to loopback. Its collection action requires a same-site session token and JSON. Use SSH tunneling if needed. Keep the configuration and archive paths writable only by trusted operators. |
| Late or missed run | The next interval can retry during the morning window. The next live edition can recover recent missed filings. Historical replay can fill older archive gaps. |

## Scheduling and delivery

Set `DELIVERY=slack`, `email`, or `both` with the appropriate secrets. `run.py --run` attempts delivery for today's live edition after the configured cutoff. The dashboard collection button always archives only. `REQUIRE_COMPLETE=true` gates delivery on issuer coverage when issuers are configured, or market snapshot coverage on a market-only edition. Snapshot issues are disclosed in either case. By default, an explicitly marked partial brief can be delivered while there is usable source coverage.

Schedule the local virtual-environment Python to call `run.py --scheduled` every two minutes. The command exits immediately outside the weekday window from `BRIEFING_TIME` to 08:00 in `TIMEZONE`, even if the scheduler's host uses a different time zone. A cross-process lock prevents overlapping writers. Use one scheduler per archive and capture nonzero exits in your monitoring.

- **Windows Task Scheduler**. Use the virtual environment's `python.exe` as Program, the absolute `run.py` path and `--scheduled` as arguments, and the project directory as Start in. Repeat every two minutes indefinitely. Set overlapping starts to be ignored and give the account access to the project and runtime archive.
- **Linux cron**. From a dedicated service account, use `*/2 * * * * cd /opt/morning-deal-brief && .venv/bin/python run.py --scheduled`. A systemd timer can run the same command.
- **n8n**. The optional workflow runs that same entry point. It requires Python, the venv, and the project installed inside the process or custom container at `/opt/morning-deal-brief`. Mount the writable archive separately. The host-scheduler setup avoids this dependency.

After the first send attempt, the archived revision is frozen. Slack is one post per edition. SMTP records acceptance per recipient. `run.py --retry-delivery YYYY-MM-DD` retries explicitly rejected or previously unattempted destinations on a saved live edition. If a request was submitted and the acknowledgement was lost, the receipt stays pending and unattended retries skip that destination to avoid duplication. Check the inbox or channel and transport logs before resolving a pending receipt in `delivery.json`. SMTP acceptance means the server accepted the message for that recipient. See [Slack webhook responses](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/) and [Python SMTP recipient refusal handling](https://docs.python.org/3/library/smtplib.html).

For source uncertainty, keep the publisher and evidence link visible. A quoted filing or headline is source attribution. The system cannot independently certify what the publisher asserted, infer causality from market moves, or guarantee delivery if the host, data vendors, or transport are down.
