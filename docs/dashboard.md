# Dashboard

The Streamlit dashboard is launched from the Tkinter launcher via **Open dashboard**, or directly with:

```bash
streamlit run ui/app.py
```

All pages display a banner indicating whether the system is operating in **paper** or **live** mode (read from `ALPACA_BASE_URL`).

## Pages

### Summary (`ui/app.py`)
Main overview page:
- **Global market context** — latest `MarketContextAgent` report: sentiment, risk appetite, confidence, dominant themes, macro risks, and summary paragraph.
- **Symbol states** — metrics for how many symbols are in WATCHING / OPPORTUNITY_FOUND / POSITION_OPEN, table of open positions with target/stop-loss, list of symbols under active opportunity monitoring.
- **Recent trades** — last 10 executed orders.

### 1 · Artículos (`ui/pages/1_Articulos.py`)
Table of news articles ingested by the legacy news pipeline, with filters by symbol and impact level.

### 2 · Precios (`ui/pages/2_Precios.py`)
Price history chart for a selected symbol, sourced from `price_snapshots`.

### 3 · Trades (`ui/pages/3_Trades.py`)
Full trade history with a calculated P&L column (matched buy→sell pairs per symbol), filterable by symbol.

### 4 · Portfolio (`ui/pages/4_Portfolio.py`)
Live Alpaca account data: equity, cash, and open positions with target/stop-loss values pulled from `symbol_states`.

### 5 · Watchlist (`ui/pages/5_Watchlist.py`)
View and manage the list of monitored symbols. Add new symbols or deactivate existing ones.

### 6 · Estados (`ui/pages/6_Estados.py`)
- State machine table for all symbols (state, last analysis, target, stop-loss, position details).
- Per-symbol agent report history: browse the full JSON output of each sub-agent call.

## Shared components (`ui/components.py`)

`trading_mode_banner()` — renders a blue info banner for paper mode or a red warning banner for live mode. Called at the top of every page.
