# Database Schema

SQLite database (`trading_agent.db`), managed by `StorageLayer` in `src/storage.py`.

## Tables

### `articles`
Legacy news articles from the old news pipeline.

```sql
articles (
    url             TEXT PRIMARY KEY,
    content_hash    TEXT UNIQUE,
    title           TEXT,
    source          TEXT,
    published_at    TEXT,
    company         TEXT,
    ticker          TEXT,
    impact_level    TEXT,   -- HIGH | MEDIUM | LOW | null
    impact_type     TEXT,
    confidence      INTEGER,
    reason          TEXT,
    alert_sent      INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL
)
```

### `price_snapshots`
Historical price snapshots captured each poll cycle.

```sql
price_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    close       REAL NOT NULL,
    volume      REAL,
    bar_time    TEXT,
    recorded_at TEXT NOT NULL
)
```

### `trades`
Orders executed by the agent (buy and sell).

```sql
trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,   -- buy | sell
    qty         REAL NOT NULL,
    price       REAL,
    order_id    TEXT,
    status      TEXT,
    trigger     TEXT,
    reason      TEXT,
    created_at  TEXT NOT NULL
)
```

### `watchlist`
Symbols actively monitored by `PriceMonitor`.

```sql
watchlist (
    symbol          TEXT PRIMARY KEY,
    company         TEXT DEFAULT '',
    threshold_pct   REAL DEFAULT 2.0,
    active          INTEGER DEFAULT 1
)
```

### `symbol_states`
State machine state per symbol. Primary key is `symbol`.

```sql
symbol_states (
    symbol              TEXT PRIMARY KEY,
    state               TEXT DEFAULT 'WATCHING',  -- WATCHING | OPPORTUNITY_FOUND | POSITION_OPEN
    last_analysis_at    TEXT,
    analysis_summary    TEXT,
    entry_signal_at     TEXT,
    target_price        REAL,
    stop_loss           REAL,
    position_qty        REAL,
    position_open_at    TEXT,
    updated_at          TEXT NOT NULL
)
```

### `agent_reports`
Historical sub-agent reports per symbol (JSON blobs).

```sql
agent_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    agent_name  TEXT NOT NULL,  -- technical_medium | technical_short | company_analysis | trading_opportunity_eval | trading_entry_eval
    report      TEXT NOT NULL,  -- JSON string
    created_at  TEXT NOT NULL
)
```

### `market_context`
Global holistic market context reports, updated every `analysis_interval_hours`.

```sql
market_context (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report      TEXT NOT NULL,    -- JSON: {market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}
    news_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
)
```

## Key StorageLayer methods

| Method | Description |
|---|---|
| `upsert_symbol_state(symbol, **kwargs)` | Create or update state for a symbol |
| `get_symbol_state(symbol)` | Get current state row for a symbol |
| `get_all_symbol_states()` | Get all symbol states |
| `save_agent_report(symbol, agent_name, report)` | Persist a sub-agent JSON report |
| `get_agent_reports(symbol, limit)` | Retrieve report history for a symbol |
| `save_market_context(report, news_count)` | Persist a new global market context report |
| `get_latest_market_context()` | Get the most recent market context row |
| `get_market_context_history(limit)` | Get history of market context reports |
| `save_trade(...)` | Record an executed order |
| `get_trades(limit)` | Retrieve trade history |
| `save_price_snapshot(symbol, close, volume, bar_time)` | Record a price snapshot |
| `get_last_price(symbol)` | Get the most recent close price for a symbol |
