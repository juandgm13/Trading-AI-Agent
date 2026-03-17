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
- `GNEWS_API_KEY` – GNews API key
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
- **Analizar ahora**: forces an immediate full analysis of all WATCHING symbols
- **Abrir dashboard**: opens the Streamlit UI

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
    │  TradingOpportunityAgent → HOLD (watch) / REJECT  │
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
| `TechnicalWatchingAgent` | `agents/technical_agent.py` | `technical` | OHLCV daily bars (90d, pre-fetched) | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| `TechnicalEntryAgent` | `agents/technical_agent.py` | `technical_entry` | OHLCV 15min bars (5d, pre-fetched) | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| `MarketContextAgent` | `agents/economics_agent.py` | `economics` | General market news (GNews) | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| `CompanyAnalysisAgent` | `agents/economics_agent.py` | `economics` | Company-specific news (GNews) | `{sentiment, key_events, entry_risk, confidence, summary}` |
| `TradingOpportunityAgent` | `agents/trading_agent.py` | `trader_opportunity` | Technical + market context reports | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty: null, reasoning}` |
| `TradingEntryAgent` | `agents/trading_agent.py` | `trader_entry` | Technical + company reports | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty: <int or null>, reasoning}` |

The monitor pre-fetches all data (bars via Alpaca, news via GNews) and injects it into each agent's prompt. Agents are purely text analyzers — no tool loops.

**Prompt separation by state:**
- `TECHNICAL_WATCHING_PROMPT`: macro trend detection, support/resistance, no tight targets required.
- `TECHNICAL_ENTRY_PROMPT`: precise entry timing, tight target/stop mandatory for BUY signals.
- `TRADING_OPPORTUNITY_PROMPT`: "should we watch more closely?" — biased toward HOLD over REJECT.
- `TRADING_ENTRY_PROMPT`: "enter NOW?" — strict, all conditions must be met for OPEN_POSITION.

**Position sizing**: `TradingEntryAgent` recommends a qty (integer ≥ 1). The monitor applies a hard cap: `max_qty = max(1, int(MAX_POSITION_VALUE / current_price))`. Final qty = `min(agent_qty, max_qty)`. If the agent returns `null`, the monitor falls back to `max_qty`.

### Module contracts

| Module | Input | Output |
|---|---|---|
| **News Ingestion** | API credentials + query params | `Article { source, title, description, content, url, published_at }` |
| **Deduplication** | `Article` | `DeduplicationResult { is_new: bool, article: Article }` |
| **TechnicalWatchingAgent** | OHLCV daily bars JSON | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| **TechnicalEntryAgent** | OHLCV 15min bars JSON | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| **MarketContextAgent** | General market news JSON | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| **CompanyAnalysisAgent** | Company news JSON | `{sentiment, key_events, entry_risk, confidence, summary}` |
| **TradingOpportunityAgent** | Sub-agent reports in prompt | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty: null, reasoning}` |
| **TradingEntryAgent** | Sub-agent reports in prompt | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty: <int or null>, reasoning}` |
| **AlpacaClient.get_asset()** | Symbol string | `{symbol, name, exchange, marginable, shortable}` or `None` |
| **AlpacaClient.get_clock()** | — | `{is_open, next_open, next_close, timestamp}` |
| **ChartGenerator** | symbol, bars, timeframe, event_label | PNG path (saved to `charts/`) |
| **Alerting** | Trade details | Email via Gmail SMTP |
| **Storage** | Various | SQLite persistence |

### Key design constraints

- Each agent is **independently replaceable** (swap model, planner, or provider in YAML).
- `SimplePlanner` embeds the system prompt in the user message — framework does not separate system/user prompts.
- All crypto symbols are filtered out from Alpaca assets and watchlist (`asset_class=us_equity`, `exchange != CRYPTO`).
- SQLite deduplication uses `url` as unique key for articles; `symbol` as primary key for `symbol_states`.
- `AgentLogger` writes to `logs/agent.log` and `logs/prompts.log` (rotating, configurable via `LOG_LEVEL`).
- **Position value limit**: `MAX_POSITION_VALUE` (env) overrides `max_position_value` (yaml). Agent qty is capped to `max(1, int(MAX_POSITION_VALUE / price))`; `null` falls back to the cap.
- **Evidence charts**: PNG candlestick charts are generated via `mplfinance` at state transitions (opportunity detected, position opened) and stored in `charts/`. Path is persisted in `symbol_states.chart_path`.
- **Stop on symbol boundary**: `stop_event` is checked at the start of each symbol in `_check_all()`, not only at the top of the main loop.
- **Market hours standby**: at the start of each `_check_all()` cycle, the monitor calls `AlpacaClient.get_clock()`. If `is_open=False`, it logs the next open time and sleeps until then (min 60s, interruptible by `stop_event`). If the clock API fails, the cycle continues normally.
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
- **News source**: GNews free tier
- **Database**: SQLite (standard library)
- **Alerts**: Gmail SMTP with App Password
- **Charts**: `mplfinance` — candlestick PNG saved to `charts/`
- **UI**: Tkinter (control panel) + Streamlit (dashboard)
- **Dependencies**: `agent-framework` (local), `aiohttp`, `requests`, `python-dotenv`, `streamlit`, `mplfinance`

## File map

```
agents/
  monitor.py          PriceMonitor — state machine loop (4 specialized agents)
  technical_agent.py  TechnicalWatchingAgent + TechnicalEntryAgent + prompt builders
  economics_agent.py  MarketContextAgent + CompanyAnalysisAgent + prompt builders
  trading_agent.py    TradingOpportunityAgent + TradingEntryAgent + prompt builders
  trading_agent.yaml  Model config (5 roles) + trading params
  tools/
    price_tool.py            GetPriceTool
    news_tool.py             SearchNewsTool
    portfolio_tool.py        GetPortfolioTool
    order_tool.py            PlaceOrderTool
    historical_bars_tool.py  GetHistoricalBarsTool
src/
  alpaca.py          AlpacaClient (async REST) — get_assets returns exchange/marginable/shortable
  storage.py         StorageLayer (SQLite)
  logger.py          AgentLogger (structured logging)
  ingestion.py       NewsIngestionModule (GNews)
  alerting.py        TradeAlerter (Gmail SMTP)
  chart_generator.py generate_candlestick_chart (mplfinance PNG)
ui/
  app.py             Streamlit main page
  pages/
    1_Articulos.py   News articles table
    2_Precios.py     Price history chart
    3_Trades.py      Trade history + P&L
    4_Portfolio.py   Live portfolio (Alpaca)
    5_Watchlist.py   Watchlist management (shows exchange/marginable/shortable)
    6_Estados.py     Symbol states + agent reports + evidence charts
main.py      Tkinter UI + thread management
tests/       pytest suite (285 tests, ~93% coverage)
charts/      Evidence PNG charts (auto-created, gitignored)
logs/        agent.log + prompts.log (auto-created)
```

## trading_agent.yaml — model roles

| Role | Temperature | Purpose |
|---|---|---|
| `technical` | 0.1 | Medium-term daily bar analysis (WATCHING state) |
| `technical_entry` | 0.1 | Short-term 15min bar analysis (OPPORTUNITY_FOUND state) |
| `economics` | 0.2 | Market context + company news analysis |
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
