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
WATCHING ──────────────────────────────────────────────┐
    │  (every analysis_interval_hours, default 4h)      │
    │  TechnicalAgent (medium-term) + EconomicsAgent    │
    │  TradingAgent evaluates → opportunity?            │
    ▼                                                   │
OPPORTUNITY_FOUND                                       │
    │  (every poll cycle)                               │
    │  TechnicalAgent (short-term) → entry timing?      │
    │  TradingAgent decides → open position?            │
    ▼                                    │ REJECT        │
POSITION_OPEN ◄──────────────────────────┘              │
    │  (every poll cycle)                               │
    │  price >= target_price → SELL → WATCHING ─────────┘
    │  price <= stop_loss    → SELL → WATCHING ─────────┘
```

### Agent roles (all use `simple` planner, text-in / JSON-out)

| Agent | File | Model role | Input | Output |
|---|---|---|---|---|
| `TechnicalAnalysisAgent` | `agents/technical_agent.py` | `technical` | OHLCV bars (pre-fetched by monitor) | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| `EconomicsAgent` | `agents/economics_agent.py` | `economics` | News articles (pre-fetched by monitor) | `{sentiment, market_context, key_events, confidence, summary}` |
| `TradingAgent` (orchestrator) | `agents/trading_agent.py` | `trader` | Sub-agent reports as context | `{decision, symbol, qty, reasoning}` |

The monitor pre-fetches all data (bars via Alpaca, news via GNews) and injects it into each agent's prompt. Agents are purely text analyzers — no tool loops.

### Module contracts

| Module | Input | Output |
|---|---|---|
| **News Ingestion** | API credentials + query params | `Article { source, title, description, content, url, published_at }` |
| **Deduplication** | `Article` | `DeduplicationResult { is_new: bool, article: Article }` |
| **TechnicalAgent** | OHLCV bars JSON in prompt | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| **EconomicsAgent** | News articles JSON in prompt | `{sentiment, market_context, key_events, confidence, summary}` |
| **TradingAgent** | Sub-agent reports in prompt | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty, reasoning}` |
| **Alerting** | Trade details | Email via Gmail SMTP |
| **Storage** | Various | SQLite persistence |

### Key design constraints

- Each agent is **independently replaceable** (swap model, planner, or provider in YAML).
- `SimplePlanner` embeds the system prompt in the user message — framework does not separate system/user prompts.
- All crypto symbols are filtered out from Alpaca assets and watchlist (`asset_class=us_equity`, `exchange != CRYPTO`).
- SQLite deduplication uses `url` as unique key for articles; `symbol` as primary key for `symbol_states`.
- `AgentLogger` writes to `logs/agent.log` and `logs/prompts.log` (rotating, configurable via `LOG_LEVEL`).

### SQLite tables

| Table | Purpose |
|---|---|
| `articles` | News articles + LLM analysis results |
| `price_snapshots` | Historical price data per symbol |
| `trades` | Executed orders (buy/sell) |
| `watchlist` | Active symbols to monitor |
| `symbol_states` | State machine state per symbol (WATCHING/OPPORTUNITY_FOUND/POSITION_OPEN) |
| `agent_reports` | Historical sub-agent reports per symbol |

## Tech Stack

- **LLM**: `qwen2.5:7b` via [Ollama](https://ollama.com/) (local inference, no GPU required)
- **Agent Framework**: `AgentBuilder` + `OllamaProvider` + `EventBus`. Config in `agents/trading_agent.yaml`.
- **Broker**: Alpaca (paper trading mode by default)
- **News source**: GNews free tier
- **Database**: SQLite (standard library)
- **Alerts**: Gmail SMTP with App Password
- **UI**: Tkinter (control panel) + Streamlit (dashboard)
- **Dependencies**: `agent-framework` (local), `aiohttp`, `requests`, `python-dotenv`, `streamlit`

## File map

```
agents/
  monitor.py          PriceMonitor — state machine loop
  technical_agent.py  TechnicalAnalysisAgent + prompt builders
  economics_agent.py  EconomicsAgent + prompt builders
  trading_agent.py    TradingAgent (orchestrator) + prompt builders
  trader.py           Legacy ReAct agent (kept for reference)
  pipeline.py         Legacy news pipeline (kept for reference)
  trading_agent.yaml  Model config + trading params
  tools/
    price_tool.py         GetPriceTool
    news_tool.py          SearchNewsTool
    portfolio_tool.py     GetPortfolioTool
    order_tool.py         PlaceOrderTool
    historical_bars_tool.py  GetHistoricalBarsTool (new)
src/
  alpaca.py    AlpacaClient (async REST)
  storage.py   StorageLayer (SQLite)
  logger.py    AgentLogger (structured logging)
  ingestion.py NewsIngestionModule (GNews)
  alerting.py  TradeAlerter (Gmail SMTP)
ui/
  app.py             Streamlit main page
  pages/
    1_Articulos.py   News articles table
    2_Precios.py     Price history chart
    3_Trades.py      Trade history + P&L
    4_Portfolio.py   Live portfolio (Alpaca)
    5_Watchlist.py   Watchlist management
    6_Estados.py     Symbol state machine + agent reports (new)
main.py      Tkinter UI + thread management
tests/       pytest suite
logs/        agent.log + prompts.log (auto-created)
```
