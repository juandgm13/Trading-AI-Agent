# Trading AI Agent

Autonomous trading agent that monitors stock prices in real time using a **multi-agent state machine**, analyzes market data and news with local LLMs via Ollama, and executes paper trades through Alpaca.

## Documentation

| Doc | Content |
|---|---|
| [Architecture](docs/architecture.md) | State machine, agent roles, system diagram, module contracts |
| [Setup & Configuration](docs/setup.md) | Installation, environment variables, YAML config, logging |
| [Dashboard](docs/dashboard.md) | Streamlit pages and shared components |
| [Database](docs/database.md) | SQLite schema and StorageLayer API |
| [Testing](docs/testing.md) | Test suite overview and how to run tests |

---

## Tech Stack

| Component | Technology |
|---|---|
| **LLM** | `qwen2.5:7b` via [Ollama](https://ollama.com/) (local, no GPU required) |
| **Agent Framework** | Custom: `AgentBuilder` + `OllamaProvider` + `EventBus` + `SimplePlanner` |
| **Market data + trading** | [Alpaca Markets](https://alpaca.markets/) — paper + live |
| **News** | GNews API (free tier) |
| **Database** | SQLite — 7 tables |
| **Alerts** | Gmail SMTP with App Password |
| **Desktop UI** | Tkinter |
| **Dashboard** | Streamlit (multi-page) |

---

## Quick Start

```bash
# 1. Install dependencies
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 2. Pull the LLM model
ollama pull qwen2.5:7b

# 3. Configure credentials
cp .env.example .env   # then fill in your keys

# 4. Run
python main.py
```

See [Setup & Configuration](docs/setup.md) for full details.

---

## Project Structure

```
Trading-AI-Agent/
├── agents/
│   ├── monitor.py              # PriceMonitor — state machine loop
│   ├── technical_agent.py      # TechnicalAnalysisAgent
│   ├── economics_agent.py      # MarketContextAgent + CompanyAnalysisAgent
│   ├── trading_agent.py        # TradingAgent (orchestrator)
│   ├── trading_agent.yaml      # Model roles + trading params
│   └── tools/                  # Price, news, portfolio, order, historical bars
├── src/
│   ├── alpaca.py               # AlpacaClient (async REST)
│   ├── storage.py              # StorageLayer (SQLite)
│   ├── logger.py               # AgentLogger
│   ├── ingestion.py            # NewsIngestionModule
│   └── alerting.py             # TradeAlerter (Gmail SMTP)
├── ui/
│   ├── app.py                  # Summary dashboard
│   ├── components.py           # Shared components (paper/live banner)
│   └── pages/                  # Artículos, Precios, Trades, Portfolio, Watchlist, Estados
├── docs/                       # Detailed documentation
├── tests/                      # pytest suite (35 tests)
├── main.py                     # Tkinter launcher
└── .env.example
```

---

## Design Principles

- **State machine per symbol** — each symbol moves independently through `WATCHING → OPPORTUNITY_FOUND → POSITION_OPEN`.
- **Shared market context** — macro news is analyzed once every 4h and reused across all symbols.
- **Independent modules** — swap GNews → Bloomberg, Alpaca → IBKR, or Gmail → Slack without touching the rest.
- **No crypto** — assets filtered to `us_equity` only.
- **Paper trading by default** — switching to live requires only changing `ALPACA_BASE_URL` in `.env`. The dashboard shows the active mode on every page.
- **No credentials in code** — everything via `.env`, never committed.
