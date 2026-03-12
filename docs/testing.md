# Testing

## Test suite overview

| File | Tests | Requirements |
|---|---|---|
| `test_storage.py` | 20 | None (in-memory SQLite) |
| `test_monitor.py` | 13 | None (fully mocked) |
| `test_alerting.py` | 5 | None (mock SMTP) |
| `test_tools.py` | 12 | Partial (some require Alpaca) |
| `test_alpaca.py` | 9 | `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` |
| `test_agent.py` | 3 | Alpaca + GNews + Ollama running |

Tests that require credentials are **automatically skipped** if the corresponding environment variables are not set.

## Running tests

```bash
# Fast tests — no credentials needed
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

## Test structure

### `test_storage.py`
Covers all `StorageLayer` methods using a temporary SQLite file (`tmp_db` fixture from `conftest.py`):
- Watchlist insert, upsert, deactivate
- Price snapshots
- Trades
- Symbol states (WATCHING / OPPORTUNITY_FOUND / POSITION_OPEN transitions)
- Agent reports

### `test_monitor.py`
Covers `PriceMonitor` state machine using fully mocked Alpaca, LLM agents, and `SearchNewsTool`:
- WATCHING: skips analysis when interval not due, triggers when due or force flag is set
- OPPORTUNITY_FOUND: runs short-term analysis + company analysis; opens position on OPEN_POSITION; returns to WATCHING on REJECT
- POSITION_OPEN: sells at target price, sells at stop-loss, holds between levels
- Loop: `monitor.stop()` exits cleanly, `_check_all` batches snapshot calls

### `test_alerting.py`
Covers `TradeAlerter` with a mock SMTP server. Optional real email test via `--email` flag.

### `conftest.py`
Provides:
- `tmp_db` — temporary `StorageLayer` backed by a temp file, auto-cleaned after each test
- `skip_without_alpaca` — marker to skip if `ALPACA_API_KEY` is not set
- `skip_without_gnews` — marker to skip if `GNEWS_API_KEY` is not set
