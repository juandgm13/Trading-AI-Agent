# Testing

## Test suite overview

| File | Tests | Requirements |
|---|---|---|
| `test_storage.py` | 20 | None (in-memory SQLite) |
| `test_monitor.py` | 30 | None (fully mocked) |
| `test_alerting.py` | 5 | None (mock SMTP) |
| `test_tools.py` | 12 | Partial (some require Alpaca) |
| `test_alpaca.py` | 10 | `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` |
| `test_agent_builders.py` | 32 | None (AgentBuilder mocked) |
| `test_chart_generator.py` | 9 | None (mplfinance mocked) |
| `test_ollama_agents.py` | ~10 | Ollama running locally |

> **Note:** `test_agent.py` was removed (replaced by `test_agent_builders.py` and `test_ollama_agents.py`).

Tests that require credentials are **automatically skipped** if the corresponding environment variables are not set.

## Running tests

```bash
# Fast tests — no credentials needed
pytest tests/test_storage.py tests/test_monitor.py tests/test_alerting.py \
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
- Watchlist insert, upsert, deactivate — including new `exchange`, `marginable`, `shortable` fields
- Price snapshots
- Trades
- Symbol states (WATCHING / OPPORTUNITY_FOUND / POSITION_OPEN transitions) — including `chart_path`
- Agent reports

### `test_monitor.py`
Covers `PriceMonitor` state machine using fully mocked Alpaca, LLM agents, and `SearchNewsTool`. Updated for the 6-agent constructor and all new features:
- **WATCHING**: skips analysis when interval not due, triggers when due or force flag is set
- **OPPORTUNITY_FOUND**: runs short-term analysis + company analysis; opens position on OPEN_POSITION; returns to WATCHING on REJECT
- **POSITION_OPEN**: sells at target price, sells at stop-loss, holds between levels
- **F3 stop mid-loop**: `stop_event` set mid-cycle exits at the next symbol boundary
- **F4 position sizing**: qty is calculated as `max(1, int(max_position_value / price))`, LLM qty is ignored
- **F5 chart generation**: `_generate_chart` returns path on success, None on empty bars, None and logs on exception
- **Alerter exceptions**: buy/sell alerter exceptions are swallowed and do not interrupt the trade flow
- **Loop**: `monitor.stop()` exits cleanly; `_check_all` batches snapshot calls

### `test_agent_builders.py`
Covers all `build_*` factory functions and prompt builders with a mocked chainable `AgentBuilder`:
- `build_technical_watching_agent` — verifies `model_role("technical")` and `.stateless()`
- `build_technical_entry_agent` — verifies `model_role("technical_entry")` and `.stateless()`
- `build_market_context_agent` — verifies `model_role("economics")` and `.stateless()`
- `build_company_analysis_agent` — verifies `model_role("economics")`
- `build_economics_agent` — verifies agent is returned
- `build_trading_opportunity_agent` — verifies `model_role("trader_opportunity")`
- `build_trading_entry_agent` — verifies `model_role("trader_entry")` and `.stateless()`
- Prompt builders: symbol, price, bars JSON, and `max_position_value` appear in generated prompts

### `test_chart_generator.py`
Covers `generate_candlestick_chart` with `mplfinance` mocked at module level (`patch("src.chart_generator.mpf")`):
- `mpf.plot` called with `type="candle"` and `volume=True`
- Output directory is created if it does not exist
- Filename contains symbol and event label and ends with `.png`
- `ValueError` raised on empty bars list
- `ValueError` raised when all bar values are NaN (all-null bars)
- Returned path is absolute
- Title contains symbol and event label
- `savefig` argument matches the returned path
- Integration test (real PNG): skipped unless `mplfinance` is installed

### `test_ollama_agents.py`
End-to-end tests against a live Ollama instance. Registers all 5 model roles (`technical`, `technical_entry`, `economics`, `trader_opportunity`, `trader_entry`) in the fixture. Skipped when Ollama is not reachable.

### `test_alerting.py`
Covers `TradeAlerter` with a mock SMTP server. Optional real email test via `--email` flag.

### `conftest.py`
Provides:
- `tmp_db` — temporary `StorageLayer` backed by a temp file, auto-cleaned after each test
- `skip_without_alpaca` — marker to skip if `ALPACA_API_KEY` is not set
- `skip_without_gnews` — marker to skip if `GNEWS_API_KEY` is not set
