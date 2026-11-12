# Setup & Configuration

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com/) installed and running
- [Alpaca Markets](https://alpaca.markets/) account (free paper trading)
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

# Short selling — enable OPEN_SHORT decisions for shortable symbols (default: disabled)
# ALLOW_SHORT_SELLING=false
```

## Running

```bash
python main.py
```

### Launcher buttons

| Button | Action |
|---|---|
| **Monitor ON/OFF** | Start/stop the continuous price monitoring loop |
| **Analyze now** | Force immediate full analysis of all WATCHING symbols (bypasses market-hours check) |
| **Analyze news** | Run market news ingestion (fetch + classify) independently of the monitor |
| **Open dashboard** | Launch Streamlit dashboard in browser |
| **Clear log** | Clear the log area |
| **Reset DB + Logs** | Delete all database data and truncate log files |

## Monitor configuration (`agents/trading_agent.yaml`)

```yaml
trading:
  analysis_interval_hours: 4    # how often to run full analysis (WATCHING state)
  max_position_value: 500       # max dollar value per position (replaces max_qty_per_order)
  poll_interval_seconds: 60     # polling interval in seconds
  allow_short_selling: false    # enable OPEN_SHORT decisions for shortable symbols
```

> **Short selling**: when enabled (`ALLOW_SHORT_SELLING=true` env var or `allow_short_selling: true` in yaml), the `TradingEntryAgent` may return `OPEN_SHORT` for symbols that have `shortable=1` in the watchlist. A SELL order opens the short; a BUY order closes it. Target/stop logic is reversed (target below entry, stop above). P&L = `(entry_price - exit_price) × qty`. Disabled by default — Alpaca paper trading supports short selling.

> **Position sizing**: `TradingEntryAgent` recommends a qty (integer ≥ 1). The monitor caps it to `max(1, int(max_position_value / current_price))`. If the agent returns `null`, the cap is used as fallback.

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
  news_classifier:    # NewsClassifierAgent — scope/impact classification
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 256
  trader_opportunity: # TradingOpportunityAgent — "watch more closely?"
    model: qwen2.5:7b
    temperature: 0.3
    max_tokens: 512
  trader_entry:       # TradingEntryAgent — "enter NOW?"
    model: qwen2.5:7b
    temperature: 0.1
    max_tokens: 512
```

## Watchlist initialization

When `WATCHLIST_SYMBOLS` is set in `.env`, each symbol is enriched by calling `GET /v2/assets/{symbol}` to populate `name`, `exchange`, `marginable`, and `shortable` in the database. If the API returns `404` or the symbol is non-tradable, a minimal entry (symbol only) is inserted as fallback.

If `WATCHLIST_SYMBOLS` is **not** set, the full list of active Alpaca US equity assets is loaded via `GET /v2/assets`.

## Market hours standby

At the start of every poll cycle, the monitor calls `GET /v2/clock`. If the market is closed:
1. Logs `"Mercado cerrado — standby hasta <next_open>"`
2. Sleeps until `next_open` (minimum 60 s, respecting `stop_event`)
3. Skips the rest of the cycle — no LLM calls, no Alpaca data requests

If the clock API is unreachable, the cycle continues normally (non-blocking).

## Evidence charts

When a positive state transition occurs, a candlestick PNG is generated automatically in `charts/` (created on first use):

| Event | File pattern |
|---|---|
| `WATCHING → OPPORTUNITY_FOUND` | `charts/{SYMBOL}_opportunity_{timestamp}.png` |
| `OPPORTUNITY_FOUND → POSITION_OPEN` | `charts/{SYMBOL}_entry_{timestamp}.png` |

Charts are also displayed in the **Estados** dashboard page when the file still exists on disk.

## Tkinter log panel

The log panel in the Tkinter UI prefixes every non-empty message with a `[HH:MM:SS]` timestamp:

```
[14:32:01] [monitor] Starting...
[14:32:05] [monitor] Cycle start — 3 symbols to monitor
[14:33:10] [monitor] AAPL: opportunity detected → OPPORTUNITY_FOUND
```

Bare newlines (used as separators by the logging framework) are passed through without a timestamp to avoid double-stamping.

## Logging

Logs are written to `logs/` (auto-created):

| File | Content |
|---|---|
| `logs/agent.log` | All messages at the configured `LOG_LEVEL` (rotating, max 5 MB × 3) |
| `logs/prompts.jsonl` | Every LLM prompt + raw response in JSON Lines format (always written, regardless of level, max 10 MB × 3) |

`prompts.jsonl` stores one JSON object per line:

```json
{"timestamp": "2026-03-28T10:00:00.123", "agent": "technical", "system_prompt": "...", "prompt": "...", "response": "..."}
```

The **Prompt Logs** page in the Streamlit dashboard (`7_Prompts`) reads this file and displays each entry in a collapsible expander, with filters by agent and a limit on the number of entries shown.

Example output in `agent.log`:

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

## Daily pre-market report (`claude_agent/`)

Runs as a Claude Code scheduled task in the cloud — **no `.env` needed** (no Alpaca, Gmail or Ollama).

```bash
pip install -r claude_agent/requirements.txt   # aiohttp, feedparser, PyYAML, pandas, mplfinance, pillow>=11 (+ pytest)
python claude_agent/fetch_news.py               # → claude_agent/data/news_YYYY-MM-DD.json
python claude_agent/fetch_prices.py             # → claude_agent/data/prices_YYYY-MM-DD.json
python claude_agent/fetch_prices.py --charts SAN.MC,AAPL   # + PNG in docs/informes/charts/YYYY-MM-DD/
python claude_agent/fetch_crypto.py             # → claude_agent/data/crypto_YYYY-MM-DD.json (all Coinbase coins, screener, Fear & Greed)
python claude_agent/fetch_crypto.py --charts BTC,PEPE   # any Coinbase coin
python claude_agent/portfolio.py                # → claude_agent/data/portfolio_YYYY-MM-DD.json (from inputs/cartera.csv)
python claude_agent/build_site.py               # → docs/ static site (GitHub Pages)
```

**GitHub Pages**: Settings → Pages → Deploy from a branch → `Trading-Agent_Claude` / `docs`. The site is plain
HTML (`docs/.nojekyll`, no Jekyll build); the technical `docs/*.md` files are not rendered as pages.

> Use `python -m pip install …` so packages land in the same interpreter that runs the scripts. If no stable
> `mplfinance>=0.12` is published for your Python, install the latest pre-release: `python -m pip install --pre mplfinance`.

`claude_agent/config.yaml` options:

| Key | Default | Meaning |
|---|---|---|
| `timezone` | `Europe/Madrid` | Used for the report date and Monday detection |
| `lookback_hours` / `monday_lookback_hours` | `24` / `72` | News window |
| `max_articles_per_feed` / `max_articles_per_symbol` | `15` / `5` | Limits |
| `summary_max_chars` | `400` | RSS summary truncation (HTML stripped) |
| `full_content_top_n` / `full_content_max_chars` | `6` / `2500` | Full text for the newest Spain + US articles |
| `full_content_skip_domains` | `[investing.com]` | Domains whose article pages block automated fetch |
| `feeds.{espana,europa,eeuu,macro,cripto}` | see file | `{name, url}` RSS feeds (`cripto`: CoinDesk, Cointelegraph, Decrypt) |
| `watchlist.<group>` | see file | `{symbol, name}` for Yahoo Finance RSS news **and** price history/indicators (`.MC` = Madrid) |
| `crypto.exchange` | `coinbase` | Universe = every coin with an online, tradable Coinbase pair (any quote: USD, EUR, USDC…) |
| `crypto.max_portfolio_pct` | `25` | Max crypto weight of the total portfolio (user decision); the report recommends reducing above it |
| `crypto.watchlist` | BTC, ETH, SOL, XRP | Core coin codes: always get detailed technicals (Coinbase candles, 365-day calendar) and Yahoo news (`CODE-USD`) |
| `crypto.auto_top_n` / `crypto.screener_top` | `15` / `20` | Largest Coinbase coins (no stablecoins) with detailed technicals / rows of the market-cap radar |
| `site.title` / `site.base_url` | `Informe pre-apertura` / `https://juandgm13.github.io/Trading-AI-Agent/` | Site title and public URL (used in the daily notification) |
| `portfolio.file` / `portfolio.base_currency` | `inputs/cartera.csv` / `EUR` | User movements CSV (relative to `claude_agent/`) and valuation currency |
| `portfolio.aportacion_mensual` | `{importe: 200, moneda: EUR, dia: 1}` | Monthly contribution; `portfolio.py` returns the next date and the report proposes how to allocate it (not added to cash automatically) |

`inputs/cartera.csv` columns: `fecha,activo,nombre,tipo,operacion,cantidad,precio,moneda,comision,broker,notas`
(`tipo` = accion/etf/fondo/cripto/liquidez/otro — `liquidez` = available cash such as `EUR` or `USDC`, price 1, never priced
on Yahoo and outside the crypto cap; `operacion` = compra/venta/dividendo; `,` or `;` separator, decimal comma
or point). Positions use weighted average cost; see `claude_agent/inputs/README.md`.

Editable inputs for Claude: `claude_agent/sources.md` (pages to web-fetch, daily searches, interests) and
`claude_agent/inputs/` (any personal context files), `claude_agent/learning_log.md` (didactic syllabus). Schedule: `CRON_TZ=Europe/Madrid 47 7 * * 1-5`.
