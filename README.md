# Trading AI Agent

Autonomous trading agent that monitors stock prices in real time using a **multi-agent state machine**, analyzes market data and news with local LLMs via Ollama, and executes paper trades through Alpaca.

---

## Architecture

### Multi-agent state machine

Each symbol has an independent state machine managed by `PriceMonitor`:

```
WATCHING ──────────────────────────────────────────────┐
    │  (every analysis_interval_hours, default: 4h)    │
    │  TechnicalAgent (90-day daily bars)               │
    │  EconomicsAgent (news + market context)           │
    │  TradingAgent evaluates → opportunity?            │
    ▼                                                   │
OPPORTUNITY_FOUND                                       │
    │  (every poll cycle, default: 60s)                │
    │  TechnicalAgent (5-day 15-min bars)               │
    │  TradingAgent decides → open position?            │
    ▼                                    │ REJECT        │
POSITION_OPEN ◄──────────────────────────┘              │
    │  (every poll cycle)                               │
    │  price >= target_price → SELL → WATCHING ─────────┘
    │  price <= stop_loss    → SELL → WATCHING ─────────┘
```

### Agent roles (all: `simple` planner · `qwen2.5:7b` · text-in / JSON-out)

| Agent | File | Output |
|---|---|---|
| `TechnicalAnalysisAgent` | `agents/technical_agent.py` | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| `EconomicsAgent` | `agents/economics_agent.py` | `{sentiment, market_context, key_events, confidence, summary}` |
| `TradingAgent` (orchestrator) | `agents/trading_agent.py` | `{decision: OPEN_POSITION\|HOLD\|REJECT, symbol, qty, reasoning}` |

The monitor pre-fetches all data (OHLCV bars via Alpaca, news via GNews) and injects it into each agent's prompt. Agents are pure text analyzers — no tool loops.

### System diagram

```
┌──────────────────────────────────────────────────────┐
│                   Tkinter Launcher                    │
│   [Monitor ON/OFF]  [Analizar ahora]  [Dashboard]    │
└─────────────────────────┬────────────────────────────┘
                          │
                ┌─────────▼──────────┐
                │   PriceMonitor     │
                │  async · 60s poll  │
                └──┬──────┬──────┬───┘
                   │      │      │
             WATCHING  OPPORT. POSITION
               ▼  4h   ▼ 60s    ▼ 60s
        ┌──────────────────────────────┐
        │      Sub-agent pipeline      │
        │  TechnicalAgent              │
        │  EconomicsAgent              │
        │  TradingAgent (orchestrator) │
        └──────────────┬───────────────┘
                       │ OPEN_POSITION
              ┌────────▼────────┐
              │   Alpaca API    │
              │  (paper trade)  │
              └────────┬────────┘
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    SQLite DB      Gmail SMTP    logs/
    (6 tables)    (HTML alerts)  agent.log
                                prompts.log
```

---

## Tech Stack

| Component | Technology |
|---|---|
| **LLM** | `qwen2.5:7b` via [Ollama](https://ollama.com/) (local, no GPU required) |
| **Agent Framework** | Custom local: `AgentBuilder` + `OllamaProvider` + `EventBus` + `SimplePlanner` |
| **Market data + trading** | [Alpaca Markets](https://alpaca.markets/) — paper trading + Data API |
| **News** | GNews API (free tier) |
| **Database** | SQLite (stdlib) — 6 tables |
| **Logging** | `AgentLogger` — rotating files, configurable level, Tkinter callback |
| **Alerts** | Gmail SMTP with App Password |
| **Desktop UI** | Tkinter |
| **Dashboard** | Streamlit (multi-page) |
| **Async HTTP** | `aiohttp` |

---

## Project Structure

```
Trading-AI-Agent/
├── src/
│   ├── alpaca.py          # AlpacaClient async (prices, orders, assets) — crypto filtered
│   ├── storage.py         # StorageLayer — SQLite (6 tables)
│   ├── logger.py          # AgentLogger — structured logging (agent.log + prompts.log)
│   ├── ingestion.py       # NewsIngestionModule + Article dataclass
│   └── alerting.py        # TradeAlerter — HTML trade email notifications
│
├── agents/
│   ├── monitor.py         # PriceMonitor — state machine loop
│   ├── technical_agent.py # TechnicalAnalysisAgent + prompt builders
│   ├── economics_agent.py # EconomicsAgent + prompt builders
│   ├── trading_agent.py   # TradingAgent (orchestrator) + prompt builders
│   ├── trading_agent.yaml # Config: 4 model roles + trading params
│   ├── pipeline.py        # Legacy news pipeline (SimplePlanner)
│   ├── trader.py          # Legacy ReAct agent
│   └── tools/
│       ├── price_tool.py             # get_price          → Alpaca bars
│       ├── news_tool.py              # search_news        → GNews
│       ├── portfolio_tool.py         # get_portfolio       → Alpaca account/positions
│       ├── order_tool.py             # place_order        → Alpaca + SQLite + email
│       └── historical_bars_tool.py   # get_historical_bars → Alpaca OHLCV
│
├── ui/
│   ├── app.py                  # Main dashboard (metrics)
│   └── pages/
│       ├── 1_Articulos.py      # Analyzed articles + filters
│       ├── 2_Precios.py        # Price history per symbol
│       ├── 3_Trades.py         # Executed trades + P&L column
│       ├── 4_Portfolio.py      # Live positions + target/stop-loss
│       ├── 5_Watchlist.py      # Manage monitored symbols
│       └── 6_Estados.py        # Symbol state machine + agent reports
│
├── tests/
│   ├── conftest.py             # Shared fixtures and skip markers
│   ├── test_storage.py         # 20 tests · no credentials needed
│   ├── test_monitor.py         # 12 tests · mocked (no credentials needed)
│   ├── test_alerting.py        # 5 tests  · mock SMTP + optional real email
│   ├── test_tools.py           # 12 tests · validations + mocks + integration
│   ├── test_alpaca.py          # 9 tests  · require ALPACA_API_KEY
│   └── test_agent.py           # 3 tests  · require Alpaca + GNews + Ollama
│
├── logs/                  # Auto-created: agent.log + prompts.log
├── main.py                # Tkinter launcher
├── pytest.ini             # asyncio_mode=auto, testpaths=tests
├── requirements.txt
└── .env.example
```

---

## Database Schema

```sql
-- Analyzed articles (legacy news pipeline)
articles         (url PK, content_hash UNIQUE, title, source, published_at,
                  company, ticker, impact_level, impact_type, confidence,
                  reason, alert_sent, created_at)

-- Price snapshots from the monitor
price_snapshots  (id, symbol, close, volume, bar_time, recorded_at)

-- Orders executed by the agent
trades           (id, symbol, side, qty, price, order_id, status,
                  trigger, reason, created_at)

-- Monitored symbols
watchlist        (symbol PK, company, threshold_pct, active)

-- State machine per symbol (NEW)
symbol_states    (symbol PK, state, last_analysis_at, analysis_summary,
                  entry_signal_at, target_price, stop_loss,
                  position_qty, position_open_at, updated_at)

-- Sub-agent report history (NEW)
agent_reports    (id, symbol, agent_name, report, created_at)
```

---

## Setup

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) installed and running
- [Alpaca Markets](https://alpaca.markets/) account (free paper trading)
- [GNews](https://gnews.io/) API key (free tier)
- Gmail with [App Password](https://support.google.com/accounts/answer/185833) (optional, for trade alerts)
- [Agent-Framework](https://github.com/juandavidlozano/Agent-Framework) repo cloned at `../Agent-Framework`

### Python environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### LLM model

```bash
ollama pull qwen2.5:7b
```

### Environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
# Alpaca paper trading
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# GNews
GNEWS_API_KEY=your_key

# Gmail (optional — only needed for trade alert emails)
GMAIL_USER=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
ALERT_RECIPIENT=your@gmail.com

# Watchlist (optional — if unset, ALL active Alpaca US equity assets are used)
# WATCHLIST_SYMBOLS=AAPL,MSFT,NVDA,GOOGL,AMZN

# Ollama (optional — defaults to http://localhost:11434)
# OLLAMA_BASE_URL=http://localhost:11434

# Log verbosity: DEBUG | INFO | WARNING | ERROR (default: INFO)
# LOG_LEVEL=INFO
```

---

## Running

```bash
python main.py
```

### Launcher buttons

| Button | Action |
|---|---|
| **Monitor ON/OFF** | Start/stop the continuous price monitoring loop |
| **Analizar ahora** | Force immediate full analysis of all WATCHING symbols (only active while monitor is running) |
| **Open dashboard** | Launch Streamlit dashboard in browser |
| **Clear log** | Clear the log area |

### Monitor configuration (`agents/trading_agent.yaml`)

```yaml
trading:
  analysis_interval_hours: 4   # how often to run full analysis (WATCHING state)
  max_qty_per_order: 10        # max shares per order
  poll_interval_seconds: 60    # polling interval in seconds
  paper_mode: true
```

Model roles can be pointed to different Ollama models independently:

```yaml
models:
  technical:   # TechnicalAnalysisAgent
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 1024
  economics:   # EconomicsAgent
    model: qwen2.5:7b
    temperature: 0.2
    max_tokens: 768
  trader:      # TradingAgent (orchestrator)
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 512
```

### Logging

Logs are written to `logs/` (auto-created):
- `logs/agent.log` — all messages at configured `LOG_LEVEL`
- `logs/prompts.log` — every LLM prompt + response (always written regardless of level)

```
[INFO] [monitor] Ciclo inicio — 42 símbolos a monitorizar
[INFO] [monitor] Ciclo fin — 1 alertas: AAPL → OPPORTUNITY_FOUND
[DEBUG] [monitor] MSFT: $320.50 | estado: WATCHING
```

---

## Tests

```bash
# Fast tests — no credentials needed (use mocks)
pytest tests/test_storage.py tests/test_monitor.py tests/test_alerting.py -v

# Integration tests — require Alpaca credentials
pytest tests/test_alpaca.py tests/test_tools.py -v

# Full integration — require Alpaca + GNews + Ollama running
pytest tests/test_agent.py -v

# Send a real test email (requires Gmail configured)
pytest tests/test_alerting.py::test_send_real_email -v

# Full suite
pytest -v
```

Tests that require credentials are **automatically skipped** if the corresponding environment variables are not set.

---

## Design Principles

- **State machine per symbol**: each symbol moves independently through WATCHING → OPPORTUNITY_FOUND → POSITION_OPEN, preventing conflicting signals across symbols.
- **Independent modules**: each component has a defined input/output contract. GNews → Bloomberg, Alpaca → IBKR, Gmail → Slack — swap without touching the rest.
- **No crypto**: Alpaca assets are filtered to `us_equity` only (double-filtered: `asset_class` + `exchange != CRYPTO`).
- **Structured logging**: all errors include stack traces (`exc_info=True`). Silent `except: pass` blocks are eliminated.
- **Paper trading by default**: `ALPACA_BASE_URL=https://paper-api.alpaca.markets`. Switching to live requires explicitly changing the URL.
- **Order safety**: `PlaceOrderTool` validates quantity, verifies position before selling, and enforces a max shares-per-order limit.
- **No credentials in code**: everything via `.env`, never committed.
- **Future-ready**: the state machine + `agent_reports` table accumulates enough history for backtesting. Adding a `CryptoMonitor` or new agent role requires only a new YAML entry.
