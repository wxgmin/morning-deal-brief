# Morning Deal Brief

Morning Deal Brief gives banking coverage teams one morning view of markets, financing activity and the companies they follow. It brings together market moves, the economic calendar, attributed news and SEC filings, with links and timestamps so you can check the evidence before a client call.

## Get started on Windows

1. [Download MorningDealBrief.exe](https://github.com/wxgmin/morning-deal-brief/releases/latest/download/MorningDealBrief.exe). The download is for 64 bit Windows PCs with Intel or AMD processors and includes Python. The executable is unsigned. If your firm blocks it, share the [release page](https://github.com/wxgmin/morning-deal-brief/releases/latest) with IT for approval.
2. Double click **MorningDealBrief.exe**. Your browser opens to a sample edition so you can explore the dashboard immediately. Keep the launcher window open while you use the app.
3. Open **Preferences**. Choose a coverage preset or add your own tickers. Set a focus company, select peers, and pick the regions and market indicators you want to follow.
4. Click **Save & collect**. The app fetches a fresh edition over the internet and opens it when ready. On later mornings, click **Collect brief** to refresh the dashboard.

The dashboard runs on your own computer. It saves preferences and editions in `%LOCALAPPDATA%\Morning Deal Brief` so you can replace the executable when an update is available. You can collect a brief without an AI account or a market data API key.

## What you get each morning

1. **What moved.** Scan Treasury yields, equity futures, the dollar, oil and gold. See how the session compares across regions and sectors, then check credit spreads, IPOs and reported deals.
2. **What could matter next.** Review upcoming economic releases and central bank events. The desk read explains what a move can typically imply for investors or financing teams and labels possible catalysts as such. An optional ChatGPT connection helps select which evidence and interpretations to highlight.
3. **What changed for your names.** See recent SEC filings, attributed headlines and prior session price and volume for your watchlist. Focus company events get priority. Follow the evidence links to read the original filing, headline or market source.
4. **What you can revisit or share.** Open past editions in **Archive** or download the brief and its evidence. Check **Sources** for missing coverage and look at quote timestamps to see when prices were last updated.

Public quotes can be delayed, and some releases, headlines and deals will be missing from free sources. Each edition shows the data it was able to collect. Treat interpretations as context for further research.

## Optional desk setup

**ChatGPT.** Open **Preferences**, then **ChatGPT**, and choose **Continue with ChatGPT** if your account and firm allow it. The built in desk read is available by default.

**SEC contact, morning scheduling and delivery.** Set a contact for automated SEC requests and, if desired, schedule collection before the open and send the brief to Slack or email. See the [operator guide](docs/OPERATIONS.md#configure-the-windows-download) for the Windows executable, configuration and delivery setup.

**macOS, Linux and source builds.** Follow the [operator guide](docs/OPERATIONS.md) to run from Python or rebuild the Windows download.
