# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Trading AI Agent is a Python-based algorithmic trading system that monitors stock prices, analyzes them using a multi-agent LLM pipeline (via Ollama), maintains a state machine per symbol, and executes paper trades through Alpaca. Runs on Windows.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Configuration via `.env` file (never commit this):
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` – Alpaca paper trading credentials
- `ALPACA_BASE_URL` – defaults to `https://paper-api.alpaca.markets`
- `GMAIL_USER` / `GMAIL_APP_PASSWORD` – Gmail SMTP credentials
- `OLLAMA_BASE_URL` – defaults to `http://localhost:11434`
- `WATCHLIST_SYMBOLS` – comma-separated symbols (e.g. `AAPL,MSFT`); if empty, loads all Alpaca US equities
- `MAX_POSITION_VALUE` – max USD per open position (default: `500`)
- `LOG_LEVEL` – `DEBUG | INFO | WARNING | ERROR` (default: `INFO`)

## Running

```bash
python main.py
```

The Tkinter UI provides:
- **Monitor ON/OFF**: starts/stops the price monitor loop
- **Analyze now**: forces an immediate full analysis of all WATCHING symbols
- **Analyze news**: runs market news ingestion (fetch + classify) independently of the monitor
- **Open dashboard**: opens the Streamlit UI

## Architecture

### Multi-agent state machine

Each symbol has an independent state machine managed by `PriceMonitor`:

```
Global market context (every analysis_interval_hours, default 4h)
  MarketContextAgent fetches general market news → holistic sentiment report (persisted in DB)

WATCHING ──────────────────────────────────────────────┐
    │  (every analysis_interval_hours, default 4h)      │
    │  TechnicalWatchingAgent (medium-term daily bars)  │
    │  + global market context (read from DB)           │
    │  TradingOpportunityAgent → OPPORTUNITY_FOUND / HOLD / REJECT │
    ▼                                                   │
OPPORTUNITY_FOUND                                       │
    │  (every poll cycle)                               │
    │  TechnicalEntryAgent (short-term 15min bars)      │
    │  + CompanyAnalysisAgent (company-specific news)   │
    │  TradingEntryAgent → OPEN_POSITION / HOLD / REJECT│
    ▼                                    │ REJECT        │
POSITION_OPEN ◄──────────────────────────┘              │
    │  (every poll cycle)                               │
    │  price >= target_price → SELL → WATCHING ─────────┘
    │  price <= stop_loss    → SELL → WATCHING ─────────┘
```

**Stop behaviour**: when the stop signal is received, the monitor finishes
processing the current symbol and then exits — it does not abandon mid-cycle.

### Agent roles (all use `simple` planner, text-in / JSON-out)

| Agent | File | Model role | Input | Output |
|---|---|---|---|---|
| `TechnicalWatchingAgent` | `agents/technical_agent.py` | `technical` | OHLCV daily bars (90d, pre-fetched) | `{trend, signal, key_supports: [], key_resistances: [], confidence, summary}` |
| `TechnicalEntryAgent` | `agents/technical_agent.py` | `technical_entry` | OHLCV 15min bars (5d, pre-fetched) + optional medium-term context | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| `MarketContextAgent` | `agents/economics_agent.py` | `economics` | Classified market news (from DB) | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| `CompanyAnalysisAgent` | `agents/economics_agent.py` | `economics` | Classified company news (from DB) | `{sentiment, key_events, entry_risk, confidence, summary}` |
| `NewsClassifierAgent` | `agents/economics_agent.py` | `news_classifier` | Raw article (title/description/content) | `{scope, ticker, exchange, impact, reasoning}` |
| `TradingOpportunityAgent` | `agents/trading_agent.py` | `trader_opportunity` | Technical + market context reports | `{decision: OPPORTUNITY_FOUND\|HOLD\|REJECT, symbol, reasoning}` |
| `TradingEntryAgent` | `agents/trading_agent.py` | `trader_entry` | Technical + company reports | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty: <int or null>, reasoning}` |

The monitor pre-fetches bars via Alpaca and reads pre-classified news from the DB (populated by `NewsIngestionService`). Agents are purely text analyzers — no tool loops.

**Prompt separation by state:**
- `TECHNICAL_WATCHING_PROMPT`: macro trend detection, support/resistance, no tight targets required.
- `TECHNICAL_ENTRY_PROMPT`: precise entry timing, tight target/stop mandatory for BUY signals.
- `TRADING_OPPORTUNITY_PROMPT`: "should we watch more closely?" — biased toward HOLD over REJECT.
- `TRADING_ENTRY_PROMPT`: "enter NOW?" — strict, all conditions must be met for OPEN_POSITION.

**Position sizing**: `TradingEntryAgent` recommends a qty (integer ≥ 1). The monitor applies a hard cap: `max_qty = max(1, int(MAX_POSITION_VALUE / current_price))`. Final qty = `min(agent_qty, max_qty)`. If the agent returns `null`, the monitor falls back to `max_qty`.

### Module contracts

| Module | Input | Output |
|---|---|---|
| **NewsAggregator** | Alpaca API creds | `list[Article]` (from Alpaca News + RSS + Yahoo by symbol) |
| **NewsClassifierAgent** | `Article` (title/description/content) | `{scope, ticker, exchange, impact, reasoning}` |
| **NewsIngestionService** | aggregator + classifier + storage | `(fetched, classified)` or `list[dict]` (symbol articles) |
| **TechnicalWatchingAgent** | OHLCV daily bars JSON | `{trend, signal, key_supports: [], key_resistances: [], confidence, summary}` |
| **TechnicalEntryAgent** | OHLCV 15min bars JSON + optional medium-term context | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| **MarketContextAgent** | Classified market news JSON (from DB) | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| **CompanyAnalysisAgent** | Classified company news JSON (from DB) | `{sentiment, key_events, entry_risk, confidence, summary}` |
| **TradingOpportunityAgent** | Sub-agent reports in prompt | `{decision: OPPORTUNITY_FOUND\|HOLD\|REJECT, symbol, reasoning}` |
| **TradingEntryAgent** | Sub-agent reports in prompt | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty: <int or null>, reasoning}` |
| **AlpacaClient.get_asset()** | Symbol string | `{symbol, name, exchange, marginable, shortable}` or `None` |
| **AlpacaClient.get_clock()** | — | `{is_open, next_open, next_close, timestamp}` |
| **AlpacaClient.place_order()** | symbol, qty, side, [take_profit_price, stop_loss_price] | Market or bracket order; returns leg IDs if bracket |
| **AlpacaClient.cancel_order()** | order_id | Cancels a specific order; returns True/False |
| **AlpacaClient.get_order()** | order_id | Order status + `legs: [{id, status, type, side}]` for bracket orders |
| **ChartGenerator** | symbol, bars, timeframe, event_label | PNG path (saved to `charts/`) |
| **Alerting** | Trade details | Email via Gmail SMTP |
| **Storage** | Various | SQLite persistence |

### Key design constraints

- Each agent is **independently replaceable** (swap model, planner, or provider in YAML).
- `SimplePlanner` embeds the system prompt in the user message — framework does not separate system/user prompts.
- All crypto symbols are filtered out from Alpaca assets and watchlist (`asset_class=us_equity`, `exchange != CRYPTO`).
- SQLite deduplication uses `url` as unique key for articles; `content_hash` (sha256 of title+date) for cross-source dedup. `symbol` as primary key for `symbol_states`.
- **News pipeline**: `NewsAggregator` (Alpaca News + RSS + Yahoo by symbol) → **two-pass** `NewsClassifierAgent`: (1) title+description only; (2) if scope≠`irrelevant`, fetch full article text and reclassify with `article_summary` → persisted with retention (HIGH=7d, MEDIUM=3d, LOW=1d). Article scopes: `company`, `market`, `exchange`, `irrelevant`.
- **Bracket orders**: When `target_price` and `stop_loss` are both set, the monitor places a bracket order to Alpaca. `bracket_order_id` is stored in `symbol_states`. In `POSITION_OPEN`, the monitor first checks if a bracket leg was auto-executed by Alpaca; if not, it falls back to price-comparison logic (cancels remaining legs before manual close).
- `AgentLogger` writes to `logs/agent.log` and `logs/prompts.log` (rotating, configurable via `LOG_LEVEL`).
- **Position value limit**: `MAX_POSITION_VALUE` (env) overrides `max_position_value` (yaml). Agent qty is capped to `max(1, int(MAX_POSITION_VALUE / price))`; `null` falls back to the cap.
- **Evidence charts**: PNG candlestick charts are generated via `mplfinance` at state transitions (opportunity detected, position opened) and stored in `charts/`. Path is persisted in `symbol_states.chart_path`.
- **Stop on symbol boundary**: `stop_event` is checked at the start of each symbol in `_check_all()`, not only at the top of the main loop.
- **Market hours standby**: at the start of each `_check_all()` cycle, the monitor calls `AlpacaClient.get_clock()`. If `is_open=False`, it logs the next open time and sleeps until then (min 60s, interruptible by `stop_event`). If the clock API fails, the cycle continues normally.
- **Claude daily report (`claude_agent/`)**: independent of the local agent; no Alpaca/Gmail/Ollama. `RSSNewsSource(feeds=[(source, url, group)])` overrides default feeds. See `claude_agent/README.md`.
- **Watchlist enrichment from ENV**: when `WATCHLIST_SYMBOLS` is set, each symbol is enriched via `AlpacaClient.get_asset()` to populate `name`, `exchange`, `marginable`, and `shortable`. Falls back to a minimal entry if the API returns `None`.

### SQLite tables

| Table | Purpose |
|---|---|
| `articles` | News articles + LLM analysis results |
| `price_snapshots` | Historical price data per symbol |
| `trades` | Executed orders (buy/sell) |
| `watchlist` | Active symbols (`symbol`, `company`, `threshold_pct`, `exchange`, `marginable`, `shortable`) |
| `symbol_states` | State machine state per symbol — includes `chart_path` for evidence PNG |
| `agent_reports` | Historical sub-agent reports per symbol |
| `market_context` | Global market context reports (holistic sentiment, updated every 4h) |

## Tech Stack

- **LLM**: `qwen2.5:7b` via [Ollama](https://ollama.com/) (local inference, no GPU required)
- **Agent Framework**: `AgentBuilder` + `OllamaProvider` + `EventBus`. Config in `agents/trading_agent.yaml`.
- **Broker**: Alpaca (paper trading mode by default)
- **News sources**: Alpaca News API + RSS feeds (feedparser) + Yahoo Finance RSS by symbol
- **Database**: SQLite (standard library)
- **Alerts**: Gmail SMTP with App Password
- **Charts**: `mplfinance` — candlestick PNG saved to `charts/`
- **UI**: Tkinter (control panel) + Streamlit (dashboard)
- **Dependencies**: `agent-framework` (local), `aiohttp`, `feedparser`, `requests`, `python-dotenv`, `streamlit`, `mplfinance`

## File map

```
agents/
  monitor.py          PriceMonitor — state machine loop (6 specialized agents + NewsIngestionService)
  technical_agent.py  TechnicalWatchingAgent + TechnicalEntryAgent + prompt builders
  economics_agent.py  MarketContextAgent + CompanyAnalysisAgent + NewsClassifierAgent + prompt builders
  trading_agent.py    TradingOpportunityAgent + TradingEntryAgent + prompt builders
  trading_agent.yaml  Model config (6 roles) + trading params
  tools/
    price_tool.py            GetPriceTool
    news_tool.py             SearchNewsTool (LEGADO — no usado en flujo principal)
    portfolio_tool.py        GetPortfolioTool
    order_tool.py            PlaceOrderTool
    historical_bars_tool.py  GetHistoricalBarsTool
src/
  alpaca.py          AlpacaClient (async REST) — get_assets returns exchange/marginable/shortable
  storage.py         StorageLayer (SQLite)
  logger.py          AgentLogger (structured logging)
  ingestion.py       Article dataclass + NewsIngestionModule (LEGADO — GNews, no usado en flujo principal)
  news_sources.py    AlpacaNewsSource + RSSNewsSource + YahooSymbolSource + NewsAggregator
  news_service.py    NewsIngestionService (fetch + classify + persist)
  response_parser.py JSON validators including validate_news_classification()
  alerting.py        TradeAlerter (Gmail SMTP)
  chart_generator.py generate_candlestick_chart (mplfinance PNG)
ui/
  app.py             Streamlit main page
  pages/
    1_Articulos.py   News articles table (scope/impact/expires_at + filters)
    2_Precios.py     Price history chart
    3_Trades.py      Trade history + P&L
    4_Portfolio.py   Live portfolio (Alpaca)
    5_Watchlist.py   Watchlist management (shows exchange/marginable/shortable)
    6_Estados.py     Symbol states + agent reports + evidence charts
claude_agent/   Daily pre-market news report run by a Claude Code scheduled task (cloud, public sources only)
  fetch_news.py      Public RSS + Yahoo-by-symbol collection → data/news_YYYY-MM-DD.json (reuses src/news_sources.py)
  config.yaml        Feeds (espana/europa/eeuu/macro) + watchlist + lookback window
  sources.md         Editable list of pages to web-fetch, daily web searches, user interests
  inputs/            User-provided context files read on each run
  daily_prompt.md    Instructions Claude follows on each scheduled run
  report_template.md Fixed structure of the Spanish report
  reports/           Generated reports (YYYY-MM-DD.md, committed)
main.py      Tkinter UI + thread management (Monitor ON/OFF, Analyze now, Analyze news)
tests/       pytest suite (~427 tests, 100% coverage)
charts/      Evidence PNG charts (auto-created, gitignored)
logs/        agent.log + prompts.log (auto-created)
```

## trading_agent.yaml — model roles

| Role | Temperature | Purpose |
|---|---|---|
| `technical` | 0.1 | Medium-term daily bar analysis (WATCHING state) |
| `technical_entry` | 0.1 | Short-term 15min bar analysis (OPPORTUNITY_FOUND state) |
| `economics` | 0.2 | Market context + company news analysis |
| `news_classifier` | 0.1 | Classify article scope/impact (background pipeline) |
| `trader_opportunity` | 0.3 | Should we watch more closely? (higher temp → more HOLD) |
| `trader_entry` | 0.1 | Enter NOW? (lower temp → stricter OPEN_POSITION) |

## Documentation rule

**After every code modification, update `docs/` accordingly.** The files to keep in sync are:

| File | What to update |
|---|---|
| `docs/architecture.md` | Agent contracts, state machine, design constraints, module contracts |
| `docs/setup.md` | Configuration options, position sizing, logging examples |
| `docs/database.md` | Schema changes, new columns, StorageLayer methods |
| `docs/testing.md` | Test counts per file, new test categories |
| `docs/dashboard.md` | New dashboard pages or UI changes |
