# Setup & Configuration

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) installed and running
- [Alpaca Markets](https://alpaca.markets/) account (free paper trading)
- [GNews](https://gnews.io/) API key (free tier)
- Gmail with [App Password](https://support.google.com/accounts/answer/185833) (optional, for trade alerts)
- `agent-framework` package installed (local dependency)

## Python environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## LLM model

```bash
ollama pull qwen2.5:7b
```

## Environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
# Alpaca — use paper-api for paper trading, api for live
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

# Trading — maximum dollar value per position (default: $500)
# MAX_POSITION_VALUE=500
```

## Running

```bash
python main.py
```

### Launcher buttons

| Button | Action |
|---|---|
| **Monitor ON/OFF** | Start/stop the continuous price monitoring loop |
| **Analizar ahora** | Force immediate full analysis of all WATCHING symbols (only while monitor is running) |
| **Open dashboard** | Launch Streamlit dashboard in browser |
| **Clear log** | Clear the log area |

## Monitor configuration (`agents/trading_agent.yaml`)

```yaml
trading:
  analysis_interval_hours: 4    # how often to run full analysis (WATCHING state)
  max_position_value: 500       # max dollar value per position (replaces max_qty_per_order)
  poll_interval_seconds: 60     # polling interval in seconds
  paper_mode: true
```

> **Position sizing**: `qty = max(1, int(max_position_value / current_price))`. The LLM does not decide quantity — it always returns `qty: null`.

Model roles can be pointed to different Ollama models independently:

```yaml
models:
  technical:          # TechnicalWatchingAgent — macro trend analysis
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 1024
  technical_entry:    # TechnicalEntryAgent — precise entry timing
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 1024
  economics:          # MarketContextAgent + CompanyAnalysisAgent
    model: qwen2.5:7b
    temperature: 0.2
    max_tokens: 768
  trader_opportunity: # TradingOpportunityAgent — "watch more closely?"
    model: qwen2.5:7b
    temperature: 0.3
    max_tokens: 512
  trader_entry:       # TradingEntryAgent — "enter NOW?"
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 512
```

## Evidence charts

When a positive state transition occurs, a candlestick PNG is generated automatically in `charts/` (created on first use):

| Event | File pattern |
|---|---|
| `WATCHING → OPPORTUNITY_FOUND` | `charts/{SYMBOL}_opportunity_{timestamp}.png` |
| `OPPORTUNITY_FOUND → POSITION_OPEN` | `charts/{SYMBOL}_entry_{timestamp}.png` |

Charts are also displayed in the **Estados** dashboard page when the file still exists on disk.

## Logging

Logs are written to `logs/` (auto-created):

| File | Content |
|---|---|
| `logs/agent.log` | All messages at the configured `LOG_LEVEL` (rotating) |
| `logs/prompts.log` | Every LLM prompt + raw response (always written, regardless of level) |

Example output:

```
[INFO] [monitor] Ciclo inicio — 42 símbolos a monitorizar
[INFO] [monitor] Contexto global actualizado — sentiment: BULLISH | risk: RISK_ON
[INFO] [monitor] AAPL: oportunidad detectada → OPPORTUNITY_FOUND
[INFO] [monitor] AAPL: gráfico de evidencia → file:///C:/…/charts/AAPL_opportunity_20250116_143022.png
[DEBUG] [monitor] MSFT: $320.50 | estado: WATCHING
```

## Paper vs live trading

The dashboard displays a prominent banner on every page indicating the active mode:

- 🧪 **Modo paper trading** — `ALPACA_BASE_URL` contains `paper-api`
- ⚠️ **MODO REAL** — any other URL

To switch to live trading, change `ALPACA_BASE_URL=https://api.alpaca.markets` in your `.env`.
