# Testing

## Test suite overview

| File | Tests | Requirements |
|---|---|---|
| `test_storage.py` | 32 | None (in-memory SQLite) |
| `test_monitor.py` | 72 | None (fully mocked) |
| `test_news_sources.py` | 35 | None (aiohttp + feedparser mocked) |
| `claude_agent/test_claude_fetch_news.py` | 15 | None (sources mocked) |
| `test_news_service.py` | 14 | None (aggregator + classifier mocked) |
| `test_response_parser.py` | ~22 | None |
| `test_alerting.py` | 5 | None (mock SMTP) |
| `test_tools.py` | 12 | Partial (some require Alpaca) |
| `test_alpaca.py` | 30 | Unit: none · Integration: `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` |
| `test_agent_builders.py` | 32 | None (AgentBuilder mocked) |
| `test_chart_generator.py` | 9 | None (mplfinance mocked) |
| `test_ollama_agents.py` | ~12 | Ollama running locally |

> **Note:** GNews integration tests (`@requires_gnews`) are permanently skipped — GNews has been replaced by Alpaca News API + RSS feeds. `test_ingestion.py` unit tests still run; integration tests are skipped via `_has_gnews() = False` in `conftest.py`.

Tests that require credentials are **automatically skipped** if the corresponding environment variables are not set.

## Running tests

```bash
# Fast tests — no credentials needed
pytest tests/test_storage.py tests/test_monitor.py tests/test_alerting.py \
       tests/test_news_sources.py tests/test_news_service.py \
       tests/test_agent_builders.py tests/test_chart_generator.py -v

# Integration tests — require Alpaca credentials
pytest tests/test_alpaca.py tests/test_tools.py -v

# Full integration — require Ollama running
pytest tests/test_ollama_agents.py -v

# Send a real test email (requires Gmail configured)
pytest tests/test_alerting.py::test_send_real_email -v

# Full suite with coverage
pytest -v --cov=. --cov-fail-under=60

# Full suite
pytest -v
```

## Test structure

### `test_storage.py`
Covers all `StorageLayer` methods using a temporary SQLite file (`tmp_db` fixture from `conftest.py`):
- Watchlist insert, upsert, deactivate — including `exchange`, `marginable`, `shortable` fields
- Price snapshots
- Trades
- Symbol states (WATCHING / OPPORTUNITY_FOUND / POSITION_OPEN transitions) — including `chart_path`
- Agent reports
- **News articles**: `save_article` (new/duplicate, with/without classification), `get_articles_by_scope` (by scope/ticker/exchange, excludes expired), `expire_old_articles`

### `test_news_sources.py`
Covers all classes in `src/news_sources.py` with mocked HTTP:
- `AlpacaNewsSource`: parse response, by-symbol params, HTTP errors, skip items without URL, bad dates, debug logging (short and long/truncated), error logging
- `RSSNewsSource`: parse feed via feedparser in executor, timeout, exceptions, skip entries without link, missing/bad dates, debug logging, logger paths
- `YahooSymbolSource`: URL template, parse feed, timeout, exception, skip no-link, bad/missing dates, debug logging
- `NewsAggregator`: dedup by URL, exclude stored_urls, logger paths, fetch_market_news combines all sources

### `claude_agent/test_claude_fetch_news.py`
Covers `claude_agent/fetch_news.py` (daily Claude report collector):
- Helpers: `load_config`, `lookback_hours` (Monday 72h), `clean_text` (HTML strip, truncation, parser fallback), `article_to_dict`, `filter_and_dedup` (date window, URL and normalized-title dedup), `CollectingLogger`
- `RSSNewsSource(feeds=...)` custom feeds filtered by group; default feeds unchanged
- `collect()`: grouping by feed, `feed_status` (`ok`/`sin_noticias_recientes`/`fallo`), per-symbol limits, Yahoo failures, full text (truncation, skipped domains, too-short pages), RSS failure
- `main()`: explicit and default output path, console summary

Run without the global coverage threshold: `pytest tests/claude_agent -q --no-cov`.

### `test_news_service.py`
Covers `NewsIngestionService` with mocked aggregator and classifier:
- `run_market_ingestion`: returns (fetched, classified), skips duplicate URLs, classifies only new articles, calls `expire_old_articles`, logs with logger
- `run_symbol_ingestion`: combines DB + fresh articles, exchange filter, debug logs
- `_classify_batch`: uses `asyncio.gather` for parallelism
- `_classify_article`: returns None on ParseError, returns None on generic exception, logs warnings/errors via logger

### `test_monitor.py`
Covers `PriceMonitor` state machine using fully mocked Alpaca, LLM agents, and `news_service` (AsyncMock):
- **WATCHING**: skips analysis when interval not due, triggers when due or force flag is set
- **OPPORTUNITY_FOUND**: runs short-term analysis + company analysis; opens position on OPEN_POSITION; returns to WATCHING on REJECT
- **POSITION_OPEN**: sells at target price, sells at stop-loss, holds between levels
- **Stop mid-loop**: `stop_event` set mid-cycle exits at the next symbol boundary
- **Market standby**: `_check_all` returns early when `get_clock()` returns `is_open=False`
- **Qty logic**: agent qty is used when ≤ max_qty; capped to max_qty when exceeded
- **Chart generation**, **alerter exceptions**, **loop** stop behaviour

### `test_agent_builders.py`
Covers all `build_*` factory functions and prompt builders with a mocked chainable `AgentBuilder`:
- `build_news_classifier_agent` — verifies `model_role("news_classifier")` and `.stateless()`
- All other agent builders (technical, technical_entry, economics, trading_opportunity, trading_entry)
- Prompt builders: symbol, price, bars JSON, and `max_position_value` appear in generated prompts

### `test_chart_generator.py`
Covers `generate_candlestick_chart` with `mplfinance` mocked. See previous description.

### `test_ollama_agents.py`
End-to-end tests against a live Ollama instance. Registers all 6 model roles (`technical`, `technical_entry`, `economics`, `news_classifier`, `trader_opportunity`, `trader_entry`). Includes 2 tests for `NewsClassifierAgent` (company article, macro article). Skipped when Ollama is not reachable.

### `test_alerting.py`
Covers `TradeAlerter` with a mock SMTP server. Optional real email test via `--email` flag.

### `conftest.py`
Provides:
- `tmp_db` — temporary `StorageLayer` backed by a temp file, auto-cleaned after each test
- `requires_alpaca` — skip marker if `ALPACA_API_KEY` is not set
- `requires_gnews` — always-skip (GNews replaced by Alpaca News + RSS)
- `requires_gmail` — skip marker if `GMAIL_USER` / `GMAIL_APP_PASSWORD` are not set
- `requires_ollama` — skip marker if Ollama is not reachable
