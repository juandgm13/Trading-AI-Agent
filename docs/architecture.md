# Architecture

## Multi-agent state machine

Each symbol has an independent state machine managed by `PriceMonitor`. Additionally, a global market context report is refreshed every `analysis_interval_hours` and shared across all symbols.

```mermaid
stateDiagram-v2
    direction LR

    [*] --> WATCHING

    WATCHING --> OPPORTUNITY_FOUND : BUY signal + favorable market context
    WATCHING --> WATCHING : HOLD / REJECT or interval not due

    OPPORTUNITY_FOUND --> OPENING : Entry OK — BUY order placed
    OPPORTUNITY_FOUND --> WATCHING : REJECT

    OPENING --> POSITION_OPEN : order filled
    OPENING --> WATCHING : order cancelled / expired

    POSITION_OPEN --> CLOSING : price >= target_price or <= stop_loss — SELL order placed

    CLOSING --> WATCHING : order filled — P&L calculated, email sent
    CLOSING --> POSITION_OPEN : order cancelled — will retry
```

### Analysis triggers

| State | Trigger | Agents involved |
|---|---|---|
| `WATCHING` | Every `analysis_interval_hours` (default 4h) or force event | **TechnicalWatchingAgent** (90-day bars) + market context from DB → **TradingOpportunityAgent** |
| `OPPORTUNITY_FOUND` | Every poll cycle (default 60s) | **TechnicalEntryAgent** (5-day 15-min bars) + CompanyAnalysisAgent → **TradingEntryAgent** |
| `OPENING` | Every poll cycle | `AlpacaClient.get_order()` to check buy order fill |
| `POSITION_OPEN` | Every poll cycle | 1. Check bracket order legs via `AlpacaClient.get_order()` (auto-exit by Alpaca); 2. Fallback: price check (cancel bracket legs, place market close) |
| `CLOSING` | Every poll cycle | `AlpacaClient.get_order()` to check sell order fill → P&L calculation |
| Global | Every `analysis_interval_hours` | MarketContextAgent (general market news → DB) |

---

## System diagram

```mermaid
flowchart TD
    UI["Tkinter Launcher<br/>Monitor ON/OFF · Analizar ahora · Dashboard"]
    PM["PriceMonitor<br/>async loop · 60s poll"]
    TWA["TechnicalWatchingAgent<br/>WATCHING state · 90-day bars"]
    TEA["TechnicalEntryAgent<br/>OPPORTUNITY_FOUND · 15-min bars"]
    MC["MarketContextAgent<br/>every 4h · global"]
    CA["CompanyAnalysisAgent<br/>on OPPORTUNITY_FOUND"]
    TOA["TradingOpportunityAgent<br/>WATCHING → OPPORTUNITY_FOUND"]
    TENT["TradingEntryAgent<br/>OPPORTUNITY_FOUND → POSITION_OPEN"]
    ALP["Alpaca API<br/>paper / live"]
    DB[("SQLite<br/>7 tables")]
    EMAIL["Gmail SMTP<br/>HTML alerts"]
    LOGS["logs/<br/>agent.log · prompts.log"]
    CHARTS["charts/<br/>PNG evidence charts"]
    DASH["Streamlit Dashboard"]

    UI -->|start/stop| PM
    PM --> TWA
    PM --> TEA
    PM --> MC
    PM --> CA
    TWA --> TOA
    MC --> TOA
    TEA --> TENT
    CA --> TENT
    TOA -->|OPPORTUNITY_FOUND| CHARTS
    TENT -->|OPEN_POSITION| ALP
    TENT -->|OPEN_POSITION| CHARTS
    ALP --> DB
    ALP --> EMAIL
    PM --> DB
    PM --> LOGS
    DB --> DASH
```

---

## Agent diagrams

### TechnicalWatchingAgent / TechnicalEntryAgent

Two separate agent instances with different system prompts and model temperatures. Both analyze OHLCV bars, but with different focuses:

- **TechnicalWatchingAgent** (`model_role: technical`, temp 0.1) — macro trend, support/resistance zones, medium-term setup. Used in `WATCHING` state with 90 daily bars.
- **TechnicalEntryAgent** (`model_role: technical_entry`, temp 0.1) — precise entry timing, mandatory target/stop for BUY. Used in `OPPORTUNITY_FOUND` with 96 × 15-min bars.

```mermaid
flowchart TB
    subgraph WATCH["TechnicalWatchingAgent (WATCHING · 90 daily bars)"]
        direction TB
        WI["OHLCV daily bars + current price"]
        WA["model: technical · qwen2.5:7b"]
        WO["trend · signal · key_supports[] · key_resistances[] · confidence · summary"]
        WI --> WA --> WO
    end

    subgraph ENTRY["TechnicalEntryAgent (OPPORTUNITY_FOUND · 96 × 15-min bars)"]
        direction TB
        EI["OHLCV 15-min bars + current price + medium-term context (optional)"]
        EA["model: technical_entry · qwen2.5:7b"]
        EO["trend · signal · target_price · stop_loss · confidence · summary"]
        EI --> EA --> EO
    end
```

---

### MarketContextAgent

Produces a holistic macro view of the global market once every `analysis_interval_hours`. The report is persisted in the `market_context` table and read by all symbols during their `WATCHING` analysis — no redundant API calls.

```mermaid
flowchart TB
    subgraph IN["📥 Input · text"]
        I1["General market news — Alpaca News API + RSS feeds<br/>(Investing.com, CNBC, Yahoo Finance, MarketWatch, FT, Fed Reserve…)"]
    end

    AG["MarketContextAgent<br/>model: economics · qwen2.5:7b"]

    subgraph OUT["📤 Output · JSON"]
        O1["market_sentiment: BULLISH · BEARISH · NEUTRAL"]
        --- O2["risk_appetite: RISK_ON · RISK_OFF · NEUTRAL"]
        --- O3["dominant_themes: list of 2–5 themes"]
        --- O4["macro_risks: list of 1–4 risks"]
        --- O5["confidence (0–100)"]
        --- O6["summary"]
    end

    IN --> AG --> OUT
```

---

### CompanyAnalysisAgent

Fetches company-specific news when a symbol reaches `OPPORTUNITY_FOUND` and assesses the risk of opening a position right now. Result is passed directly to `TradingEntryAgent` as context.

```mermaid
flowchart TB
    subgraph IN["📥 Input · text"]
        I1["Company news — Yahoo Finance RSS + Alpaca News (by symbol)<br/>pre-classified and persisted in DB"]
        --- I2["Symbol"]
    end

    AG["CompanyAnalysisAgent<br/>model: economics · qwen2.5:7b"]

    subgraph OUT["📤 Output · JSON"]
        O1["sentiment: POSITIVE · NEGATIVE · NEUTRAL"]
        --- O2["key_events: list of 1–4 events"]
        --- O3["entry_risk: LOW · MEDIUM · HIGH"]
        --- O4["confidence (0–100)"]
        --- O5["summary"]
    end

    IN --> AG --> OUT
```

---

### TradingOpportunityAgent / TradingEntryAgent

Two separate orchestrator agents, each with a distinct system prompt and temperature:

- **TradingOpportunityAgent** (`model_role: trader_opportunity`, temp 0.3) — "Is this worth watching more closely?" Biased toward `HOLD` when signals conflict. Runs in `WATCHING` state.
- **TradingEntryAgent** (`model_role: trader_entry`, temp 0.1) — "Should we enter NOW?" Strict: all conditions (BUY signal, RISK_ON/NEUTRAL market, LOW/MEDIUM entry risk, confidence ≥ 60) must be met. Runs in `OPPORTUNITY_FOUND` state.

> **Position sizing**: `TradingEntryAgent` recommends a `qty` (integer ≥ 1). The monitor applies a hard cap: `max_qty = max(1, int(max_position_value / price))`. Final qty = `min(agent_qty, max_qty)`. If the agent returns `null`, the monitor falls back to `max_qty`.

```mermaid
flowchart TB
    subgraph OPP["TradingOpportunityAgent (WATCHING → OPPORTUNITY_FOUND)"]
        direction TB
        OI["TechnicalWatchingAgent report + MarketContext + current price"]
        OA["model: trader_opportunity · qwen2.5:7b"]
        OO["decision: OPPORTUNITY_FOUND · HOLD · REJECT\nsymbol · reasoning"]
        OI --> OA --> OO
    end

    subgraph ENT["TradingEntryAgent (OPPORTUNITY_FOUND → OPENING)"]
        direction TB
        EI["TechnicalEntryAgent report + CompanyAnalysis + current price + max_position_value"]
        EA["model: trader_entry · qwen2.5:7b"]
        EO["decision: OPEN_POSITION · OPEN_SHORT · HOLD · REJECT\nsymbol · qty (int or null) · reasoning"]
        EI --> EA --> EO
    end
```

**Decision rules (TradingOpportunityAgent):**
- `OPPORTUNITY_FOUND` — trend is BULLISH, signal is BUY, confidence ≥ 60, market RISK_ON or NEUTRAL.
- `HOLD` — signals promising but not confirmed; confidence moderate (40-60); preferred over REJECT.
- `REJECT` — trend BEARISH, confidence < 40, or market strongly RISK_OFF.

**Decision rules (TradingEntryAgent):**
- `OPEN_POSITION` — technical signal is BUY, market is RISK_ON or NEUTRAL, company entry_risk is LOW or MEDIUM, confidence ≥ 70.
- `OPEN_SHORT` — only available when `allow_short_selling=true` and the watchlist symbol has `shortable=1`. Signal is BEARISH, confidence ≥ 70, entry_risk is LOW or MEDIUM. Uses a SELL order to open; a BUY order to close. P&L = `(entry_price - exit_price) × qty`.
- `HOLD` — signals conflict, confidence is low, or market is RISK_OFF but not severely.
- `REJECT` — signals clearly unfavorable, entry_risk is HIGH, or market is strongly BEARISH.

---

## Evidence charts

At each state transition that involves a positive decision, the monitor generates a candlestick PNG via `src/chart_generator.py` (using `mplfinance`):

| Event | Chart timeframe | Stored in |
|---|---|---|
| `WATCHING → OPPORTUNITY_FOUND` | Daily (90-bar window) | `symbol_states.chart_path` + `charts/` dir |
| `OPPORTUNITY_FOUND → POSITION_OPEN` | 15-min (96-bar window) | `symbol_states.chart_path` + `charts/` dir |

The chart path is logged as `file:///absolute/path.png` and displayed in the **Estados** dashboard page.

---

## Module contracts

| Module | Input | Output |
|---|---|---|
| **NewsClassifierAgent** | `Article` (title, description) | `{scope, ticker, exchange, impact, reasoning}` (first pass) |
| **NewsClassifierAgent (full)** | `Article` (title, description, full_content) | `{scope, ticker, exchange, impact, reasoning, article_summary}` (second pass) |
| **NewsIngestionService** | `NewsAggregator` + classifier agents + `StorageLayer` | `(fetched, classified)` / `list[dict]` |
| **NewsAggregator** | Alpaca API key/secret | `list[Article]` (market news or symbol-specific) |
| **TechnicalWatchingAgent** | 90 daily OHLCV bars JSON | `{trend, signal, key_supports: [float], key_resistances: [float], confidence, summary}` |
| **TechnicalEntryAgent** | 96 × 15-min OHLCV bars JSON + optional medium-term context | `{trend, signal, target_price, stop_loss, confidence, summary}` |
| **MarketContextAgent** | Classified market news JSON from DB | `{market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary}` |
| **CompanyAnalysisAgent** | Classified company news JSON from DB | `{sentiment, key_events, entry_risk, confidence, summary}` |
| **TradingOpportunityAgent** | Technical + market reports | `{decision: OPPORTUNITY_FOUND\|HOLD\|REJECT, symbol, reasoning}` |
| **TradingEntryAgent** | Technical + company reports | `{decision: OPEN_POSITION\|OPEN_SHORT\|HOLD\|REJECT, symbol, qty: <int or null>, reasoning}` |
| **AlpacaClient.get_asset()** | Symbol string | `{symbol, name, exchange, marginable, shortable}` or `None` |
| **AlpacaClient.get_clock()** | — | `{is_open, next_open, next_close, timestamp}` |
| **AlpacaClient.place_order()** | symbol, qty, side, [take_profit_price, stop_loss_price] | Market or bracket order; returns `{order_id, take_profit_order_id?, stop_loss_order_id?}` |
| **AlpacaClient.cancel_order()** | order_id | Cancels a specific order; returns True/False |
| **AlpacaClient.get_order()** | order_id | Order status + `legs: [{id, status, type, side}]` for bracket orders |
| **AlpacaClient** | Credentials + params | Prices, orders, assets, clock (async REST) |
| **StorageLayer** | Various | SQLite persistence (7 tables) |
| **TradeAlerter** | Trade details | HTML email via Gmail SMTP |
| **ChartGenerator** | Symbol, OHLCV bars, timeframe | Absolute path to PNG file |

## Key design constraints

- Each agent is independently replaceable (swap model, planner, or provider in YAML).
- `SimplePlanner` embeds the system prompt in the user message.
- All crypto symbols are filtered out (`asset_class=us_equity`, `exchange != CRYPTO`).
- SQLite deduplication for articles: primary key `url`; also `content_hash` (sha256 of title + date) for cross-source dedup. `symbol` as primary key for `symbol_states`.
- **News pipeline**: `NewsAggregator` (Alpaca News API + RSS feeds + Yahoo Finance by symbol) → **two-pass** `NewsClassifierAgent`: (1) quick classification with title+description; (2) if scope ≠ `irrelevant`, fetch full article text and reclassify with `article_summary` → persisted in `articles` table with retention (HIGH=7d, MEDIUM=3d, LOW=1d). Agents read pre-classified articles from DB. **Article content limit**: `fetch_article_content()` fetches up to 10 000 characters of full article text; truncated articles emit a `WARNING` log: `Article truncated: <url> (<original> → 10000 chars)`.
- **Article scopes**: `company` (ticker-specific), `market` (general macro), `exchange` (NYSE/NASDAQ-level), `irrelevant` (filtered out by agents).
- **Bracket orders**: When opening a position with both `target_price` and `stop_loss`, the monitor places a bracket order (`order_class=bracket`) with attached take-profit (limit) and stop-loss (stop) legs. `bracket_order_id` is persisted in `symbol_states`. In `POSITION_OPEN`, the monitor first checks whether Alpaca auto-executed a leg; if not, it falls back to its own price-comparison logic (cancelling bracket legs before placing a manual close order).
- **Manual news ingestion**: the "Analyze news" button in Tkinter runs `NewsIngestionService.run_market_ingestion()` independently of the monitor loop.
- `AgentLogger` writes to `logs/agent.log` and `logs/prompts.log` (rotating, configurable via `LOG_LEVEL`).
- **Position sizing**: `TradingEntryAgent` recommends a qty; the monitor caps it to `max(1, int(MAX_POSITION_VALUE / price))`. Fallback to the cap if the agent returns `null`.
- Stop signal is checked at the beginning of each symbol iteration (not only at the `while` loop boundary), so the monitor halts within one symbol's analysis time.
- **Market hours standby**: `_check_all()` calls `get_clock()` at the start of every cycle. If the market is closed, the monitor still runs `_maybe_update_market_context()` and processes all WATCHING symbols (long-term daily analysis — data available regardless of session). States that require an open market (`OPPORTUNITY_FOUND`, `POSITION_OPEN`, `OPENING`, `CLOSING`) are skipped via `_process_symbol(market_is_open=False)`. After completing WATCHING analysis the monitor sleeps until `next_open` (interruptible). If `get_clock()` fails, the cycle continues with `market_is_open=True`.
- **Force analysis**: when `force_event` is set (via the "Analyze now" button in Tkinter), `_force_analysis=True` causes `_check_all()` to skip the market-hours check entirely. All WATCHING symbols are analysed regardless of market status. The flag is reset by `run()` at cycle end. The event is signaled with `loop.call_soon_threadsafe(force_event.set)` to ensure thread-safety across the Tkinter/asyncio boundary. Both `_wait_or_stop()` and `_interruptible_sleep()` accept a `force_event` parameter and wake early when it is set, so "Analyze now" interrupts both the poll-interval sleep and the market-closed standby sleep immediately.
- **Trade fees**: the `fees` column in `trades` is populated from the `commission` field returned by `GET /v2/orders/{id}`. Alpaca returns `"0"` for paper trading and the actual regulatory fee for live trading. No local fee calculation is performed.
- **Watchlist enrichment**: when `WATCHLIST_SYMBOLS` is set, each symbol is enriched via `get_asset()` (name, exchange, marginable, shortable). Falls back to minimal entry on API failure.
- **Short selling**: disabled by default. Enable with `ALLOW_SHORT_SELLING=true` (env var) or `allow_short_selling: true` in `trading_agent.yaml`. Only applies to symbols with `shortable=1` in the watchlist. The `TradingEntryAgent` prompt includes the `OPEN_SHORT` option only when both conditions are met. Position side (`long`/`short`) is stored in `symbol_states.position_side` and used for reversed target/stop logic and P&L calculation.
- Prompt separation by state: `WATCHING` uses macro-focused prompts; `OPPORTUNITY_FOUND` uses timing-focused prompts with stricter conditions.

## Daily pre-market report (Claude Code scheduled task)

Independent pipeline under `claude_agent/` that runs in Anthropic's cloud (no credentials available):

```
Scheduled task (Mon–Fri 07:47 Europe/Madrid, fresh session)
  └─ Claude follows claude_agent/daily_prompt.md
       fetch_news.py ──► RSSNewsSource(feeds=config) + YahooSymbolSource(watchlist)
                         → filter by lookback (24h, 72h on Mondays) + dedup URL/title
                         → optional full text (fetch_article_content, skip blocked domains)
                         → claude_agent/data/news_YYYY-MM-DD.json
       fetch_prices.py ─► Yahoo /v8/finance/chart (2y daily) per watchlist symbol
                         → last bar patched from chart meta (regularMarketPrice/Time) when the daily bar is late or null
                         → today's bar dropped while the session is open (price = last close)
                         → still >1 business day behind → retry range=5d and merge; else stale=true + issue
                         → compute_indicators() (pandas) → claude_agent/data/prices_YYYY-MM-DD.json
       fetch_crypto.py ─► Coinbase /products (every online, tradable pair, any quote → universe ~400 coins)
                         × Coinpaprika /v1/tickers (rank, price, market cap, 24h/7d/30d/1y) → universe + screener
                         + Coinbase /v2/exchange-rates (USD→EUR) + alternative.me Fear & Greed + Coinpaprika /v1/global
                         + Coinbase daily candles (2×300, EUR pair else USD, ≤5 concurrent) for core watchlist
                           + top auto_top_n + portfolio coins + --charts → compute_indicators(calendar="cripto")
                         → claude_agent/data/crypto_YYYY-MM-DD.json
       portfolio.py ───► inputs/cartera.csv (user movements) → weighted average cost positions
                         + Yahoo last price + EURxxx=X FX → claude_agent/data/portfolio_YYYY-MM-DD.json
       web fetch / web search of ALL sources.md lines (no time limit) + files in inputs/
       Claude reads the previous report (front matter + sections 2, 6–8) and cartera.csv: keeps actions/levels
         unless something changed (and says what), never re-recommends a purchase already made
       Claude decides 3–5 stock ideas (Spain, Europe, US) + 1–3 crypto ideas, each with a direct action;
         every non-index watchlist stock (e.g. AIR.PA) gets a verdict (full idea or "Resto de la watchlist" table);
         report length up to ~6,000 words
         (Comprar / Comprar más / Mantener / Reducir / Vender / Esperar / Evitar) + why, size, horizon,
         invalidation level and confidence; crypto: any Coinbase coin, size by market-cap rank, within crypto.max_portfolio_pct (25 %)
         → fetch_prices.py / fetch_crypto.py --charts … → docs/informes/charts/DATE/*.png
       Claude picks the lesson of the day (learning_log.md, appends a row)
       Claude writes claude_agent/reports/YYYY-MM-DD.md (report_template.md: YAML front matter + Markdown)
       build_site.py ──► all reports + learning_log.md → markdown (tables, toc, nl2br) + post-processing
                         (chart paths, action badges, ± colours, table scroll) → docs/ static site
                         (index.html, informes/DATE.html, archivo.html, aprende.html, historial.html, assets/, .nojekyll)
       commit + push → GitHub Pages serves docs/ from branch Trading-Agent_Claude
```

| Module | Input | Output |
|---|---|---|
| `fetch_news.collect(cfg, now, full_content)` | `config.yaml` dict | `{generated_at, lookback_hours, counts, feed_status[], issues[], market{espana,europa,eeuu,macro}, symbols{SYM: {name, group, articles[]}}}` |
| `fetch_prices.collect(cfg, now, chart_symbols, charts_dir)` | `config.yaml` dict | `{generated_at, source, symbols{SYM: {name, group, last_date, price, change_pct{1d,1w,1m,3m,1y}, sma{20,50,200}, price_vs_sma_pct, trend, sma_cross_last_20d, rsi14, rsi_zone, macd{line,signal,histogram,momentum}, volatility_20d_annual_pct, high_52w, low_52w, dist_from_*_52w_pct, supports[], resistances[], volume_vs_avg20, price_source ("bars" \| "meta"), stale, recent_bars[60], chart?}}, issues[]}` |
| `fetch_prices.drop_unclosed_bar(df, now)` / `business_days_lag(df, now)` | OHLCV DataFrame with `attrs{exchange_tz, regular_end, price_source}` | Drops today's bar if its regular session has not ended / business days (Mon–Fri) between the last bar and today in the exchange time zone |
| `fetch_crypto.collect(cfg, now, chart_symbols, charts_dir)` | `config.yaml` dict | `{generated_at, exchange, max_portfolio_pct, usd_eur, coinbase_count \| null, fear_greed{value, classification, clasificacion, date, yesterday, week_ago} \| null, market{market_cap_usd, market_cap_change_24h_pct, volume_24h_usd, bitcoin_dominance_pct, market_cap_ath_usd, market_cap_ath_date, dist_from_ath_pct} \| null, screener{top_market_cap[], gainers_7d[], losers_7d[], gainers_30d[], losers_30d[] (top 100, no stablecoins), breadth_24h, breadth_7d} \| null, symbols{CODE: {name, rank, reasons[], product, currency, coinbase_quotes, coinbase_available, price_eur, …same indicators as fetch_prices…, recent_bars[60], chart?}}, universe[{symbol, name, rank, coinbase_quotes, price_usd, price_eur, market_cap_usd, volume_24h_usd, change_pct{24h,7d,30d,1y}, dist_from_ath_pct, stablecoin}], universe_without_data[], issues[]}` |
| `build_site.build(cfg, reports_dir, docs_dir, learning_log, now)` | reports + `site{title, base_url}` | Writes the site (incl. `historial.html`: asset × report table, assets matched by `asset_key()` = ticker in parentheses, crypto pairs collapsed to the coin; action changes highlighted; index shows "antes: X" / "nueva" vs the previous report); returns `{reports, pages[], latest, url, issues{slug: [...]}}` (missing/invalid front matter → published anyway, header inferred) |
| `portfolio.collect(cfg, csv_path, now)` | `config.yaml` dict + CSV path | `{exists, movements, last_movement, base_currency, aportacion_mensual{importe, moneda, dia, proxima_fecha, dias_hasta} | null, totals{valor_eur (investments + cash), invertido_eur, liquidez_eur, coste_eur, pnl_no_realizado_eur, pnl_no_realizado_pct (investments only), pnl_realizado_eur}, por_tipo_pct, crypto{valor_eur, peso_pct, tope_pct, excede_tope, margen_hasta_tope_eur}, liquidez{valor_eur, peso_pct}, positions[{activo, nombre, tipo, broker, moneda, cantidad, precio_medio, precio_actual, valor, coste, pnl, pnl_pct, valor_eur, coste_eur, pnl_eur, realizado, dividendos, desde, peso_pct}], closed_positions[], fx, issues[]}` (`{exists: false}` without CSV) |
| `fetch_prices.compute_indicators(df, calendar="bolsa")` | OHLCV DataFrame | Indicator dict (`calendar="cripto"` uses 7/30/90/365-bar periods and √365 volatility; deterministic; trend = alcista if price > SMA50 > SMA200 and SMA50 rising, bajista if inverse, else lateral) |
| `RSSNewsSource(logger, feeds)` | optional `[(source, url, group)]` | when `feeds` is given it replaces the default feeds; `fetch(feed_groups)` keeps feeds whose group matches |

Crypto/portfolio constraints: recommendations may only *buy* coins in the Coinbase universe (any quote); duplicated
Coinpaprika symbols resolve to the best rank; prices < 1 keep 4–6 significant digits; crypto price is
the current (unclosed) daily candle; `cartera.csv` is user-maintained and never modified by the run; P&L of
foreign-currency positions uses today's FX; broker names other than Coinbase are masked as "otro bróker"
(the site is public);  (no historical FX per purchase); crypto pairs Yahoo lacks are priced
from the Coinbase ticker.

`feed_status` values: `ok`, `sin_noticias_recientes` (feed responded, nothing in window), `fallo` (no entries / blocked).
