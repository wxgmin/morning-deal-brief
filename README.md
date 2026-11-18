# Morning Deal Brief

An automated morning briefing for a banking coverage team. It collects SEC EDGAR filings,
press/news RSS, and daily price/volume for a configurable coverage list, tags and ranks events
by banking relevance, writes a grounded "why it matters" line via an LLM, and delivers a one-page
brief to Slack and/or email before 8am — with a **zero-unsourced-claims** guarantee and a
two-week demo archive.

## What it does

1. **Configurable coverage list** — `config/coverage.yaml` (client, peers, sector, tickers, feeds, weights).
2. **Data collection** — EDGAR (8-K, 10-Q, 10-K, S-4, SC 13D/13G, DEF 14A, 425) via `edgartools`;
   press/news RSS via `feedparser`; daily price/volume via Yahoo Finance's chart API (the same
   data `yfinance` wraps, fetched directly with `requests` — yfinance's curl backend timed out in
   this environment, so the fetch is done directly for reliability).
3. **Event detection** — tags each item: M&A, earnings, guidance change, financing, management change,
   activist involvement, rating change, or a >2σ price move.
4. **Ranking** — scores by relevance (client > peer > sector) × event-type weight × magnitude × confidence.
5. **LLM judgment** — OpenAI writes the 2-line summary + "why it matters", grounded **only** in retrieved text.
6. **Output** — top 3–5 items, each with summary, "why it matters for us", and a source link; then a
   price/volume table for the full coverage list.

## Directory layout

```
morning-deal-brief/
├── config/coverage.yaml     # coverage list + weights (edit me)
├── src/                     # pipeline modules
│   ├── collectors/          # edgar.py, rss.py, market.py
│   ├── events/              # detect.py, classify.py
│   ├── dedupe.py rank.py llm.py guardrails.py brief.py deliver.py archive.py
│   └── main.py              # orchestrator + CLI
├── tests/                   # unit + offline integration tests (no network)
├── archive/                 # daily briefs (demo output is committed here)
├── n8n/morning-deal-brief.json
└── scripts/seed_coverage.py
```

## Setup

```powershell
cd C:\Users\Waiz\morning-deal-brief
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the values (`.env` is gitignored, never committed):

| Variable | Purpose | Required for |
| --- | --- | --- |
| `LLM_PROVIDER` | `auto` \| `openai` \| `anthropic` \| `gemini` \| `openrouter` \| `local` \| `none` | choosing the LLM |
| `LLM_API_KEY` | Generic key for the selected provider | any API-backed provider |
| `LLM_BASE_URL` | Custom endpoint (local/Ollama/OpenRouter/proxy) | `local` / `openrouter` |
| `LLM_MODEL` / `LLM_CLASSIFY_MODEL` | Override the judgment / classification model | optional |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | Provider-specific keys (alternative to `LLM_API_KEY`) | the matching provider |
| `DELIVERY` | `slack` / `email` / `both` / `none` | delivery |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook | Slack delivery |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `SMTP_FROM` `EMAIL_RECIPIENTS` | Email (SMTP) | email delivery |
| `EDGAR_IDENTITY` | SEC-required User-Agent (`Your Name you@example.com`) | EDGAR collection |

### Switching LLM providers

The LLM layer is provider-agnostic: set `LLM_PROVIDER` and the matching key and the
rest of the pipeline is unchanged. `auto` (default) picks the first available of
Anthropic → Gemini → OpenAI → none.

- **OpenAI** — `LLM_PROVIDER=openai` + `OPENAI_API_KEY`
- **Anthropic Claude** — `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`
- **Gemini** — `LLM_PROVIDER=gemini` + `GEMINI_API_KEY` (or `LLM_API_KEY`)
- **OpenRouter** — `LLM_PROVIDER=openrouter` + `LLM_API_KEY`
- **Local (Ollama/LM Studio)** — `LLM_PROVIDER=local` + `LLM_BASE_URL=http://localhost:11434/v1` + `LLM_MODEL=...`
- **None** — `LLM_PROVIDER=none`

> Without any key the pipeline still runs end-to-end using a deterministic,
> metadata-only judgment (no invented figures) — useful for testing and the demo
> archive. Add a key/provider to unlock the LLM "why it matters" quality. A ChatGPT
> consumer account cannot be used directly; use one of the API-backed providers above.

## Running

```powershell
# Live run (collect + archive; no delivery)
.venv\Scripts\python.exe run.py --dry-run

# Live run + deliver (per DELIVERY in .env)
.venv\Scripts\python.exe run.py --run

# Backfill the last 14 weekdays into archive/ (no delivery)
.venv\Scripts\python.exe run.py --backfill 14

# Backfill ending on a specific date
.venv\Scripts\python.exe run.py --backfill 14 --end-date 2026-11-14
```

Each run writes `archive/YYYY-MM-DD/{brief.md, brief.html, events.json}`.

## Web dashboard

Run a local dashboard to browse archived briefs and view the coverage list:

```powershell
.venv\Scripts\python.exe app.py
```

Then open <http://127.0.0.1:5000>. It shows the latest brief, a date list to browse
past briefs, the coverage list, and a "Run now" button (archive-only, no delivery).

## Scheduling (n8n)

1. Import `n8n/morning-deal-brief.json` into your n8n instance (Import from file).
2. In the **Run Morning Deal Brief** (Execute Command) node, confirm the venv Python path matches
   your machine.
3. The workflow fires weekdays at 7:45 AM (cron `45 7 * * 1-5`). Change the cron in the
   **Weekdays 7:45 AM** node to taste.

> **n8n in Docker caveat:** an Execute Command node runs *inside* the n8n container, so it cannot
> call the host's `python.exe` by default. If your n8n runs in Docker, either (a) bind-mount this
> project into the container and use a Python-capable image, or (b) use Windows Task Scheduler
> instead (recommended on a single Windows host):
>
> ```powershell
> schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 07:45 /TN "MorningDealBrief" `
>   /TR "C:\Users\Waiz\morning-deal-brief\.venv\Scripts\python.exe C:\Users\Waiz\morning-deal-brief\run.py --run"
> ```

## Guardrails

- **Every item links its source.** Each brief item carries a `source_url`; in HTML the link is the anchor.
- **No figures not in the source.** `src/guardrails.py` verifies every currency/percentage figure in a
  summary traces to the retrieved source text (verbatim check on self-declared figures + an independent
  magnitude backstop). Any item with an ungrounded figure is re-prompted once, then dropped — never shipped.
- **Deduplication.** `src/dedupe.py` collapses the same story across outlets (fuzzy headline match),
  keeping the richest source and recording `also_reported_by` links.

## Testing

```powershell
.venv\Scripts\python.exe -m pytest
```

Tests are offline and deterministic (canned data, no API key): dedupe, event detection, ranking, the
grounding guardrail, and a full pipeline render.

## Demo (two-week archive)

`--backfill 14` reconstructs 14 weekday briefs. EDGAR (date-range queries) and yfinance (historical
prices) backfill fully; news RSS is live-only, so backfilled briefs are filing- and price-driven — which
are the highest-signal items for a coverage team anyway. Run `--run` live to add RSS enrichment.
