# Trading AI Agent

Autonomous trading agent that monitors stock prices in real time, analyzes financial news using a local LLM, and executes buy/sell orders via Alpaca paper trading.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      Tkinter Launcher                         │
│   [Run agent]  [Monitor ON/OFF]  [Open dashboard]            │
└────────────┬──────────────────────────┬──────────────────────┘
             │                          │
    ┌────────▼──────────┐    ┌──────────▼────────────────────┐
    │  News Pipeline     │    │   Price Monitor Loop           │
    │  (batch, manual)   │    │   async · configurable poll   │
    └────────┬───────────┘    └──────────┬────────────────────┘
             │                           │ change ≥ threshold%
             ▼                           ▼
    ┌──────────────────────────────────────────────────────┐
    │               ReAct Trading Agent                     │
    │  get_price · search_news · get_portfolio · place_order│
    │  Planner: ReAct  ·  Model: qwen2.5:7b (Ollama)       │
    └─────────────────────────┬────────────────────────────┘
                              │ orders
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
     Alpaca Paper API    SQLite DB        Gmail SMTP
     (trading + data)   (4 tables)       (HTML alerts)
```

### News pipeline (batch)

```
GNews API → Ingestion → Deduplication (SQLite) → Agent Framework (SimplePlanner)
          → LLMOutput { company, ticker, impact_level, impact_type, confidence }
          → Persistence
```

### Price monitor (continuous)

```
Alpaca batch snapshots → detect change ≥ threshold
  → ReAct agent: get_price → search_news → get_portfolio → (place_order)
  → Save trade to SQLite + send email notification
```

---

## Tech Stack

| Component | Technology |
|---|---|
| **LLM** | `qwen2.5:7b` via [Ollama](https://ollama.com/) (local, no GPU required) |
| **Agent Framework** | Custom local: `AgentBuilder` + `OllamaProvider` + `EventBus` + `ReActPlanner` |
| **Market data** | [Alpaca Markets](https://alpaca.markets/) — paper trading + Data API |
| **News** | GNews API (free tier) |
| **Database** | SQLite (stdlib) |
| **Alerts** | Gmail SMTP with App Password |
| **Desktop UI** | Tkinter |
| **Dashboard** | Streamlit (multi-page) |
| **Async HTTP** | `aiohttp` |

---

## Project Structure

```
Trading-AI-Agent/
├── src/
│   ├── ingestion.py       # NewsIngestionModule + Article dataclass
│   ├── storage.py         # StorageLayer — SQLite (4 tables)
│   ├── alpaca.py          # AlpacaClient async (prices, orders, assets)
│   └── alerting.py        # TradeAlerter — HTML trade email notifications
│
├── agents/
│   ├── pipeline.py        # Batch news pipeline (SimplePlanner)
│   ├── trader.py          # build_trading_agent() — ReAct agent builder
│   ├── monitor.py         # PriceMonitor + run_monitor()
│   ├── trading_agent.yaml # Config: analyzer + trader models
│   └── tools/
│       ├── price_tool.py      # get_price     → Alpaca bars
│       ├── news_tool.py       # search_news   → GNews
│       ├── portfolio_tool.py  # get_portfolio → Alpaca account/positions
│       └── order_tool.py      # place_order   → Alpaca + SQLite + email
│
├── ui/
│   ├── app.py                  # Main dashboard (metrics)
│   └── pages/
│       ├── 1_Articulos.py      # Analyzed articles + filters
│       ├── 2_Precios.py        # Price history per symbol
│       ├── 3_Trades.py         # Executed trades history
│       ├── 4_Portfolio.py      # Live positions (Alpaca)
│       └── 5_Watchlist.py      # Manage monitored symbols
│
├── tests/
│   ├── conftest.py             # Shared fixtures and skip markers
│   ├── test_storage.py         # 12 tests · no credentials needed
│   ├── test_monitor.py         # 9 tests  · mocked (no credentials needed)
│   ├── test_alerting.py        # 5 tests  · mock SMTP + optional real email
│   ├── test_tools.py           # 12 tests · validations + mocks + integration
│   ├── test_alpaca.py          # 9 tests  · require ALPACA_API_KEY
│   └── test_agent.py           # 3 tests  · require Alpaca + GNews + Ollama
│
├── main.py            # Tkinter launcher
├── pytest.ini         # asyncio_mode=auto, testpaths=tests
├── requirements.txt
└── .env.example
```

---

## Database Schema

```sql
-- Analyzed articles (batch pipeline)
articles         (url PK, content_hash UNIQUE, title, source, published_at,
                  company, ticker, impact_level, impact_type, confidence, reason,
                  alert_sent, created_at)

-- Price snapshots from the monitor
price_snapshots  (id, symbol, close, volume, bar_time, recorded_at)

-- Orders executed by the agent
trades           (id, symbol, side, qty, price, order_id, status,
                  trigger, reason, created_at)

-- Monitored symbols
watchlist        (symbol PK, company, threshold_pct, active)
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
# GNews
GNEWS_API_KEY=your_key

# Gmail (optional — only needed for trade alert emails)
GMAIL_USER=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
ALERT_RECIPIENT=your@gmail.com

# Alpaca paper trading
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Watchlist (optional — if unset, ALL active Alpaca US equity assets are used)
# WATCHLIST_SYMBOLS=AAPL,MSFT,NVDA,GOOGL,AMZN

# Ollama (optional — defaults to http://localhost:11434)
# OLLAMA_BASE_URL=http://localhost:11434
```

---

## Running

```bash
python main.py
```

### Launcher buttons

| Button | Action |
|---|---|
| **Run agent** | Batch pipeline: fetch news and analyze with LLM |
| **Monitor ON/OFF** | Start/stop continuous price monitoring |
| **Open dashboard** | Launch Streamlit dashboard in browser |
| **Clear log** | Clear the log area |

### Monitor configuration (`agents/trading_agent.yaml`)

```yaml
trading:
  max_qty_per_order: 10       # max shares per order
  poll_interval_seconds: 60   # polling interval in seconds
  paper_mode: true
```

The price change threshold per symbol is configured in the **Watchlist** dashboard page (default: 2%).

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

## Module Contracts

| Module | Input | Output |
|---|---|---|
| `NewsIngestionModule` | API key + keywords | `list[Article]` |
| `StorageLayer.check_and_mark` | `Article` | `DeduplicationResult { is_new, is_analyzed }` |
| `agents/pipeline.py` (batch) | article + prompt | `LLMOutput { company, ticker, impact_level, impact_type, confidence, reason }` |
| `GetPriceTool` | `symbol` | JSON with current price and recent bars |
| `SearchNewsTool` | `query` | JSON with recent articles |
| `GetPortfolioTool` | — | JSON with cash, equity, and open positions |
| `PlaceOrderTool` | `symbol, side, qty, reason` | JSON with `order_id` and status |
| `PriceMonitor` | watchlist + threshold | triggers `ReActAgent.run(prompt)` |
| `TradeAlerter` | order data | HTML email sent via SMTP |

---

## Design Principles

- **Independent modules**: each component has a defined input/output contract. GNews can be swapped for Bloomberg, Alpaca for IBKR, Gmail for Slack — without touching the rest.
- **Stateless LLM**: agent memory is cleared between articles (pipeline) and between price analyses (monitor).
- **Paper trading by default**: `ALPACA_BASE_URL=https://paper-api.alpaca.markets`. Switching to live trading requires explicitly changing the URL.
- **Order safety**: `PlaceOrderTool` validates quantity, verifies position before selling, and enforces a max shares-per-order limit.
- **No credentials in code**: everything via `.env`, never committed.
