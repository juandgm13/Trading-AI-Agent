# Database Schema

SQLite database (`trading_agent.db`), managed by `StorageLayer` in `src/storage.py`.

## Tables

### `articles`
News articles ingested via Alpaca News API + RSS feeds, classified by `NewsClassifierAgent`.

```sql
articles (
    url               TEXT PRIMARY KEY,
    content_hash      TEXT UNIQUE,       -- sha256(title.lower() + "|" + date_iso)
    title             TEXT,
    source            TEXT,
    description       TEXT,
    feed_group        TEXT,              -- alpaca | stocks | macro | yahoo_symbol
    published_at      TEXT,
    -- Classification fields (populated by NewsClassifierAgent)
    scope             TEXT,             -- company | market | exchange | irrelevant
    ticker            TEXT,             -- e.g. "AAPL" (when scope=company)
    exchange_affected TEXT,             -- e.g. "NASDAQ" (when scope=exchange)
    impact            TEXT,             -- HIGH | MEDIUM | LOW | null (null when scope=irrelevant)
    reasoning         TEXT,             -- classifier justification
    expires_at        TEXT,             -- ISO datetime; HIGH=7d, MEDIUM=3d, LOW/null=1d
    article_summary   TEXT,             -- 2-3 sentence summary from second-pass classification (may be NULL)
    -- Legacy columns (kept for backwards compatibility)
    company           TEXT,
    impact_level      TEXT,
    impact_type       TEXT,
    confidence        INTEGER,
    reason            TEXT,
    alert_sent        INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL
)
```

**Indexes:**
- `idx_articles_scope ON articles(scope, ticker, exchange_affected)` — fast lookup by scope/ticker/exchange
- `idx_articles_expires ON articles(expires_at)` — fast expiry cleanup

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
    price       REAL,            -- fill price (actual filled_avg_price for confirmed orders)
    order_id    TEXT,
    status      TEXT,
    trigger     TEXT,
    reason      TEXT,
    pnl         REAL,            -- net P&L for sell trades (sell_price - buy_price) * qty
    fees        REAL,            -- fees paid (0.0 for Alpaca paper trading)
    created_at  TEXT NOT NULL
)
```

### `watchlist`
Symbols actively monitored by `PriceMonitor`. Includes Alpaca asset metadata populated at startup.

```sql
watchlist (
    symbol          TEXT PRIMARY KEY,
    company         TEXT DEFAULT '',
    threshold_pct   REAL DEFAULT 2.0,
    active          INTEGER DEFAULT 1,
    exchange        TEXT DEFAULT '',        -- e.g. NASDAQ, NYSE
    marginable      INTEGER DEFAULT 0,      -- 1 = yes
    shortable       INTEGER DEFAULT 0       -- 1 = yes
)
```

### `symbol_states`
State machine state per symbol. Primary key is `symbol`.

```sql
symbol_states (
    symbol              TEXT PRIMARY KEY,
    state               TEXT DEFAULT 'WATCHING',  -- WATCHING | OPPORTUNITY_FOUND | OPENING | POSITION_OPEN | CLOSING
    last_analysis_at    TEXT,
    analysis_summary    TEXT,   -- during CLOSING, temporarily stores the close reason
    entry_signal_at     TEXT,
    target_price        REAL,
    stop_loss           REAL,
    position_qty        REAL,
    position_open_at    TEXT,
    chart_path          TEXT,   -- absolute path to last evidence PNG (may be NULL)
    pending_order_id    TEXT,   -- Alpaca order ID while in OPENING or CLOSING state
    position_side       TEXT DEFAULT 'long',  -- 'long' | 'short'
    bracket_order_id    TEXT,   -- Parent bracket order ID (persists in POSITION_OPEN for auto-exit detection)
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
| `upsert_watchlist(symbol, company, threshold_pct, exchange, marginable, shortable)` | Create or update watchlist entry with Alpaca metadata |
| `get_watchlist()` | Get all active watchlist entries including metadata |
| `upsert_symbol_state(symbol, **kwargs)` | Create or update state for a symbol (accepts `chart_path`, `pending_order_id`) |
| `get_symbol_state(symbol)` | Get current state row for a symbol |
| `get_all_symbol_states()` | Get all symbol states |
| `save_agent_report(symbol, agent_name, report)` | Persist a sub-agent JSON report |
| `get_agent_reports(symbol, limit)` | Retrieve report history for a symbol |
| `save_market_context(report, news_count)` | Persist a new global market context report |
| `get_latest_market_context()` | Get the most recent market context row |
| `get_market_context_history(limit)` | Get history of market context reports |
| `save_article(article, classification=None) -> bool` | Insert article; calculates expires_at from impact; returns True if new |
| `get_articles_by_scope(scope, ticker=None, exchange=None, limit=20) -> list[dict]` | Fetch non-expired articles by scope/ticker/exchange, ordered by recency |
| `expire_old_articles() -> int` | Delete articles where expires_at < now; returns count deleted |
| `save_trade(..., pnl, fees)` | Record an executed order (pnl/fees optional, for sell trades) |
| `get_trades(limit)` | Retrieve trade history |
| `get_last_buy_trade(symbol)` | Get the most recent buy trade for a symbol (used for long P&L calculation) |
| `get_last_sell_trade(symbol)` | Get the most recent sell trade for a symbol (used for short P&L calculation) |
| `save_price_snapshot(symbol, close, volume, bar_time)` | Record a price snapshot |
| `get_last_price(symbol)` | Get the most recent close price for a symbol |

## Notes on schema migrations

Schema migrations are applied automatically at startup via the `_migrations` list in `StorageLayer._ensure_schema()`. Each migration is executed once (try/except to skip if the column already exists — SQLite does not support `IF NOT EXISTS` in `ALTER TABLE`).

**Migrations applied:**

```sql
-- watchlist
ALTER TABLE watchlist ADD COLUMN exchange TEXT DEFAULT '';
ALTER TABLE watchlist ADD COLUMN marginable INTEGER DEFAULT 0;
ALTER TABLE watchlist ADD COLUMN shortable INTEGER DEFAULT 0;
-- symbol_states
ALTER TABLE symbol_states ADD COLUMN chart_path TEXT;
ALTER TABLE symbol_states ADD COLUMN pending_order_id TEXT;
ALTER TABLE symbol_states ADD COLUMN position_side TEXT DEFAULT 'long';
-- trades
ALTER TABLE trades ADD COLUMN pnl REAL;
ALTER TABLE trades ADD COLUMN fees REAL;   -- populated from Alpaca commission field
-- articles (news refactor)
ALTER TABLE articles ADD COLUMN description TEXT;
ALTER TABLE articles ADD COLUMN feed_group TEXT;
ALTER TABLE articles ADD COLUMN scope TEXT;
ALTER TABLE articles ADD COLUMN exchange_affected TEXT;
ALTER TABLE articles ADD COLUMN impact TEXT;
ALTER TABLE articles ADD COLUMN reasoning TEXT;
ALTER TABLE articles ADD COLUMN expires_at TEXT;
-- New columns
ALTER TABLE agent_reports ADD COLUMN chart_path TEXT;
ALTER TABLE symbol_states ADD COLUMN bracket_order_id TEXT;  -- bracket order ID for auto-exit detection
ALTER TABLE articles ADD COLUMN article_summary TEXT;        -- second-pass classification summary
```
