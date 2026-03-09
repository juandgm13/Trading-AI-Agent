"""
Tests del PriceMonitor.
Usan mocks para simular Alpaca y el trading agent — no requieren credenciales.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_monitor(tmp_db, on_log=None):
    """Helper: crea un PriceMonitor con mocks."""
    from agents.monitor import PriceMonitor

    mock_alpaca = MagicMock()
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value="HOLD — mixed signals")
    mock_agent._context = MagicMock()
    mock_agent._context.memory = MagicMock()
    mock_agent._context.memory.clear = AsyncMock()

    return PriceMonitor(mock_alpaca, tmp_db, mock_agent, on_log=on_log), mock_alpaca, mock_agent


@pytest.mark.asyncio
async def test_monitor_registers_baseline_on_first_run(tmp_db):
    """Primera vez que se ve un símbolo: guarda precio base y no dispara el agente."""
    monitor, mock_alpaca, mock_agent = _make_monitor(tmp_db)

    tmp_db.upsert_watchlist("AAPL", "Apple", 2.0)
    snap = {"close": 175.0, "volume": 1_000_000, "timestamp": "2024-01-01T10:00:00Z"}

    await monitor._process_symbol("AAPL", snap, threshold=2.0)

    assert tmp_db.get_last_price("AAPL") == pytest.approx(175.0)
    mock_agent.run.assert_not_called()  # primera vez → no analiza


@pytest.mark.asyncio
async def test_monitor_no_trigger_below_threshold(tmp_db):
    """Cambio menor al threshold no dispara el agente."""
    monitor, mock_alpaca, mock_agent = _make_monitor(tmp_db)

    # Precio base
    tmp_db.save_price_snapshot("AAPL", 175.0, None, "t1")
    snap = {"close": 175.5, "volume": None, "timestamp": "t2"}  # +0.28%

    await monitor._process_symbol("AAPL", snap, threshold=2.0)

    mock_agent.run.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_triggers_above_threshold(tmp_db):
    """Cambio mayor al threshold dispara el agente."""
    monitor, mock_alpaca, mock_agent = _make_monitor(tmp_db)

    tmp_db.save_price_snapshot("AAPL", 175.0, None, "t1")
    snap = {"close": 180.0, "volume": None, "timestamp": "t2"}  # +2.86%

    await monitor._process_symbol("AAPL", snap, threshold=2.0)

    mock_agent.run.assert_awaited_once()
    prompt_arg = mock_agent.run.call_args[0][0]
    assert "AAPL" in prompt_arg
    assert "2.8" in prompt_arg or "2.9" in prompt_arg  # change_pct


@pytest.mark.asyncio
async def test_monitor_trigger_on_drop(tmp_db):
    """Caída significativa también dispara el agente."""
    monitor, mock_alpaca, mock_agent = _make_monitor(tmp_db)

    tmp_db.save_price_snapshot("MSFT", 320.0, None, "t1")
    snap = {"close": 310.0, "volume": None, "timestamp": "t2"}  # -3.125%

    await monitor._process_symbol("MSFT", snap, threshold=2.0)

    mock_agent.run.assert_awaited_once()
    prompt_arg = mock_agent.run.call_args[0][0]
    assert "dropped" in prompt_arg.lower()


@pytest.mark.asyncio
async def test_monitor_saves_snapshot_on_every_check(tmp_db):
    """Cada ciclo de check guarda un nuevo snapshot, independientemente del threshold."""
    monitor, _, _ = _make_monitor(tmp_db)

    tmp_db.save_price_snapshot("NVDA", 500.0, None, "t0")

    for price in [501.0, 502.0, 503.0]:
        snap = {"close": price, "volume": 100, "timestamp": "t"}
        await monitor._process_symbol("NVDA", snap, threshold=10.0)

    history = tmp_db.get_price_history("NVDA", limit=10)
    assert len(history) == 4  # baseline + 3 nuevos


@pytest.mark.asyncio
async def test_monitor_logs_price_updates(tmp_db):
    """El monitor emite mensajes de log con el precio y la dirección."""
    logs = []
    monitor, _, _ = _make_monitor(tmp_db, on_log=logs.append)

    tmp_db.save_price_snapshot("AAPL", 175.0, None, "t1")
    snap = {"close": 176.0, "volume": None, "timestamp": "t2"}

    await monitor._process_symbol("AAPL", snap, threshold=5.0)

    assert any("AAPL" in msg and "176" in msg for msg in logs)
    assert any("▲" in msg for msg in logs)


@pytest.mark.asyncio
async def test_monitor_stop_exits_loop(tmp_db):
    """monitor.stop() detiene el loop antes del siguiente ciclo."""
    monitor, mock_alpaca, _ = _make_monitor(tmp_db)
    mock_alpaca.get_snapshots = AsyncMock(return_value={})

    stop = asyncio.Event()

    async def stop_after_one_cycle():
        await asyncio.sleep(0.05)
        monitor.stop()
        stop.set()

    await asyncio.gather(
        monitor.run(poll_interval_seconds=1, stop_event=stop),
        stop_after_one_cycle(),
    )

    assert not monitor._running


@pytest.mark.asyncio
async def test_check_all_uses_batch_snapshots(tmp_db):
    """_check_all llama a get_snapshots una vez con todos los símbolos del batch."""
    monitor, mock_alpaca, _ = _make_monitor(tmp_db)

    for sym in ["AAPL", "MSFT", "NVDA"]:
        tmp_db.upsert_watchlist(sym, "", 2.0)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
        "MSFT": {"close": 320.0, "volume": 2000, "timestamp": "t"},
        "NVDA": {"close": 500.0, "volume": 3000, "timestamp": "t"},
    })

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    # Debe haberse llamado una sola vez con los 3 símbolos
    mock_alpaca.get_snapshots.assert_awaited_once()
    symbols_arg = mock_alpaca.get_snapshots.call_args[0][0]
    assert set(symbols_arg) == {"AAPL", "MSFT", "NVDA"}
