"""
Tests del PriceMonitor — arquitectura OrchestratorAgent.
Todo el procesamiento de símbolos se delega al OrchestratorAgent mockeado.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.logger import AgentLogger


def _make_logger():
    return AgentLogger(level="ERROR")


def _make_monitor(tmp_db, on_log=None):
    """Helper: crea PriceMonitor con OrchestratorAgent mockeado."""
    from agents.monitor import PriceMonitor
    from agents.orchestrator_agent import TaskListManager

    mock_alpaca = MagicMock()
    mock_alpaca.get_bars = AsyncMock(return_value=[
        {"timestamp": "t", "open": 175.0, "high": 176.0, "low": 174.0, "close": 175.0, "volume": 1000}
    ])
    mock_alpaca.place_order = AsyncMock(return_value={"order_id": "ord-test", "status": "submitted"})
    mock_alpaca.get_order = AsyncMock(return_value=None)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True, "next_open": None, "next_close": None, "timestamp": None,
    })

    task_manager = TaskListManager()
    mock_orchestrator = MagicMock()
    mock_orchestrator.run = AsyncMock(return_value="done")

    logger = _make_logger()

    monitor = PriceMonitor(
        alpaca=mock_alpaca,
        storage=tmp_db,
        orchestrator_agent=mock_orchestrator,
        task_manager=task_manager,
        logger=logger,
        max_position_value=500.0,
        analysis_interval_hours=4.0,
    )
    return monitor, mock_alpaca, task_manager, mock_orchestrator


# ── _check_all — snapshots de precio ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_uses_batch_snapshots(tmp_db):
    """_check_all llama a get_snapshots una vez con todos los símbolos del batch."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)

    for sym in ["AAPL", "MSFT", "NVDA"]:
        tmp_db.upsert_watchlist(sym, "", 2.0)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
        "MSFT": {"close": 320.0, "volume": 2000, "timestamp": "t"},
        "NVDA": {"close": 500.0, "volume": 3000, "timestamp": "t"},
    })

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    mock_alpaca.get_snapshots.assert_awaited_once()
    symbols_arg = mock_alpaca.get_snapshots.call_args[0][0]
    assert set(symbols_arg) == {"AAPL", "MSFT", "NVDA"}


@pytest.mark.asyncio
async def test_check_all_saves_price_snapshots(tmp_db):
    """_check_all guarda snapshots de precio para cada símbolo con precio disponible."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    assert tmp_db.get_last_price("AAPL") == pytest.approx(175.0)


@pytest.mark.asyncio
async def test_check_all_logs_when_batch_raises(tmp_db):
    """Si get_snapshots lanza excepción, el ciclo continúa sin romper."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(side_effect=RuntimeError("API error"))

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)  # no debe lanzar excepción


@pytest.mark.asyncio
async def test_check_all_skips_symbol_without_snapshot(tmp_db):
    """Un símbolo sin precio no se pasa al orchestrator."""
    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={})  # sin datos

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    mock_orchestrator.run.assert_not_awaited()


# ── _check_all — dispatch al OrchestratorAgent ───────────────────────────────

@pytest.mark.asyncio
async def test_check_all_calls_orchestrator_when_symbols_have_price(tmp_db):
    """_check_all llama a orchestrator.run cuando hay símbolos con precio disponible."""
    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    await monitor._check_all(tmp_db.get_watchlist())

    mock_orchestrator.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_all_passes_task_with_prices_to_orchestrator(tmp_db):
    """La tarea pasada al orchestrator incluye el símbolo y el precio."""
    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.50, "volume": 1000, "timestamp": "t"},
    })

    await monitor._check_all(tmp_db.get_watchlist())

    task_arg = mock_orchestrator.run.call_args[0][0]
    assert "AAPL" in task_arg
    assert "175.50" in task_arg


@pytest.mark.asyncio
async def test_check_all_does_not_call_orchestrator_when_stop_event_set(tmp_db):
    """Si stop_event está activo, el orchestrator no se llama."""
    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    stop = asyncio.Event()
    stop.set()

    await monitor._check_all(tmp_db.get_watchlist(), stop_event=stop)

    mock_orchestrator.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_does_not_call_orchestrator_when_running_false(tmp_db):
    """Si _running=False, el orchestrator no se llama."""
    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })
    monitor._running = False

    await monitor._check_all(tmp_db.get_watchlist())

    mock_orchestrator.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_orchestrator_exception_is_caught(tmp_db):
    """Si orchestrator.run lanza excepción, _check_all no propaga el error."""
    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })
    mock_orchestrator.run = AsyncMock(side_effect=RuntimeError("planner error"))

    await monitor._check_all(tmp_db.get_watchlist())  # no debe propagar la excepción


@pytest.mark.asyncio
async def test_check_all_resets_task_manager_before_orchestrator(tmp_db):
    """task_manager.reset() se llama con los símbolos correctos antes de orchestrator.run."""
    monitor, mock_alpaca, task_manager, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_watchlist("MSFT", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
        "MSFT": {"close": 300.0, "volume": 2000, "timestamp": "t"},
    })

    await monitor._check_all(tmp_db.get_watchlist())

    state = task_manager.state_str()
    assert "AAPL" in state
    assert "MSFT" in state


# ── _check_all — standby de mercado ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_standby_when_market_closed(tmp_db):
    """Si get_clock devuelve is_open=False, get_snapshots SÍ se llama."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.cancel_all_orders = AsyncMock()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    original = monitor_mod._interruptible_sleep
    async def _instant_sleep(secs, stop_event, force_event=None): pass
    monitor_mod._interruptible_sleep = _instant_sleep
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    mock_alpaca.get_snapshots.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_all_continues_when_get_clock_fails(tmp_db):
    """Si get_clock lanza excepción, _check_all continúa el ciclo normalmente."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(side_effect=RuntimeError("clock API error"))
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    await monitor._check_all(tmp_db.get_watchlist())

    mock_alpaca.get_snapshots.assert_awaited_once()


def _patch_sleep(monitor_mod):
    async def _instant_sleep(secs, stop_event, force_event=None): pass
    original = monitor_mod._interruptible_sleep
    monitor_mod._interruptible_sleep = _instant_sleep
    return original


@pytest.mark.asyncio
async def test_check_all_standby_logs_local_time(tmp_db):
    """El mensaje INFO de mercado cerrado muestra hora en formato HH:MM, no ISO UTC."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.cancel_all_orders = AsyncMock()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    log_messages = []
    monitor._logger = MagicMock()
    monitor._logger.info = MagicMock(side_effect=lambda msg, **kw: log_messages.append(msg))
    monitor._logger.debug = MagicMock()
    monitor._logger.warning = MagicMock()

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    apertura_msgs = [m for m in log_messages if "apertura" in m]
    assert apertura_msgs, "No se encontró mensaje de apertura"
    assert ":" in apertura_msgs[0]
    assert "2099" not in apertura_msgs[0]


@pytest.mark.asyncio
async def test_check_all_standby_logs_debug_wait(tmp_db):
    """El debug log de espera contiene la duración en segundos."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.cancel_all_orders = AsyncMock()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    debug_messages = []
    monitor._logger = MagicMock()
    monitor._logger.info = MagicMock()
    monitor._logger.debug = MagicMock(side_effect=lambda msg, **kw: debug_messages.append(msg))
    monitor._logger.warning = MagicMock()

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    espera_msgs = [m for m in debug_messages if "esperando" in m]
    assert espera_msgs, "No se encontró mensaje de debug de espera"
    assert "s" in espera_msgs[0] or "min" in espera_msgs[0]


@pytest.mark.asyncio
async def test_check_all_standby_cancels_orders(tmp_db):
    """cancel_all_orders se llama cuando is_open=False."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.cancel_all_orders = AsyncMock()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    mock_alpaca.cancel_all_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_all_standby_cancel_exception_swallowed(tmp_db):
    """Si cancel_all_orders lanza, la excepción no se propaga y el ciclo continúa."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.cancel_all_orders = AsyncMock(side_effect=RuntimeError("cancel failed"))
    mock_alpaca.get_snapshots = AsyncMock(return_value={})
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    mock_alpaca.get_snapshots.assert_awaited_once()


# ── _check_all — cierre inminente ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_close_imminent_skips_watching(tmp_db):
    """Símbolo en WATCHING no se pasa al orchestrator cuando cierre < 5 min."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": soon,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING")

    await monitor._check_all(tmp_db.get_watchlist())

    mock_orchestrator.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_close_imminent_skips_opportunity(tmp_db):
    """Símbolo en OPPORTUNITY_FOUND no se pasa al orchestrator cuando cierre < 5 min."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": soon,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")

    await monitor._check_all(tmp_db.get_watchlist())

    mock_orchestrator.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_close_imminent_keeps_position_open(tmp_db):
    """Símbolo en POSITION_OPEN SÍ se pasa al orchestrator aunque cierre sea inminente."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, _, mock_orchestrator = _make_monitor(tmp_db)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": soon,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=200.0,
        stop_loss=160.0,
        position_qty=2.0,
        position_open_at=datetime.now(timezone.utc).isoformat(),
    )

    await monitor._check_all(tmp_db.get_watchlist())

    mock_orchestrator.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_all_close_imminent_saves_price_snapshot(tmp_db):
    """El snapshot de precio se guarda aunque el símbolo sea omitido por cierre inminente."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    soon = (datetime.now(timezone.utc) + timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": soon,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING")

    await monitor._check_all(tmp_db.get_watchlist())

    snapshots = tmp_db.get_price_history("AAPL", limit=1)
    assert snapshots, "Snapshot no guardado cuando análisis omitido por cierre inminente"
    assert snapshots[0]["close"] == pytest.approx(175.0)


# ── _check_all — market status callback ──────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_market_open_status_callback(tmp_db):
    """Cuando el mercado está abierto y hay callback, se invoca con is_open=True."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={})
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    callback_calls = []
    monitor._market_status_callback = lambda is_open, nxt: callback_calls.append((is_open, nxt))

    await monitor._check_all(tmp_db.get_watchlist())

    assert any(call == (True, None) for call in callback_calls)


@pytest.mark.asyncio
async def test_check_all_market_closed_no_next_open(tmp_db):
    """Mercado cerrado sin next_open → next_open_local = 'desconocida', wait_secs = 300."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": None,
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.cancel_all_orders = AsyncMock()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    log_messages = []
    monitor._logger = MagicMock()
    monitor._logger.info = MagicMock(side_effect=lambda msg, **kw: log_messages.append(msg))
    monitor._logger.debug = MagicMock()
    monitor._logger.warning = MagicMock()

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    apertura_msgs = [m for m in log_messages if "apertura" in m]
    assert apertura_msgs
    assert "desconocida" in apertura_msgs[0]


@pytest.mark.asyncio
async def test_market_closed_triggers_status_callback_with_false(tmp_db):
    """_market_status_callback(False, next_open) se llama cuando el mercado está cerrado."""
    from datetime import datetime, timezone, timedelta
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    next_open = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": next_open,
        "next_close": None,
        "timestamp": None,
    })
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    callback_calls = []
    monitor._market_status_callback = lambda is_open, nxt: callback_calls.append((is_open, nxt))

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    assert len(callback_calls) == 1
    assert callback_calls[0][0] is False


@pytest.mark.asyncio
async def test_normal_closed_market_fetches_snapshots_for_watching(tmp_db):
    """Sin force_analysis y mercado cerrado, get_snapshots SÍ se llama."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    monitor._force_analysis = False

    original = _patch_sleep(monitor_mod)
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    mock_alpaca.get_snapshots.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_imminent_invalid_next_close_does_not_crash(tmp_db):
    """Si next_close no es ISO-parseable, el ciclo continúa sin close_imminent."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": "NOT_A_DATE",
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={})
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    await monitor._check_all(tmp_db.get_watchlist())

    mock_alpaca.get_snapshots.assert_awaited_once()


@pytest.mark.asyncio
async def test_force_analysis_flag_reset_in_run(tmp_db):
    """_check_all no resetea _force_analysis — eso lo hace run() después."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True, "next_open": None, "next_close": None, "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={})
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    monitor._force_analysis = True

    await monitor._check_all(tmp_db.get_watchlist())

    assert monitor._force_analysis is True  # run() lo resetea, no _check_all


# ── run() — control del loop ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_breaks_immediately_if_stop_event_already_set(tmp_db):
    """Si stop_event ya está activo al entrar al loop, sale sin ciclo."""
    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_snapshots = AsyncMock(return_value={})

    stop = asyncio.Event()
    stop.set()

    await monitor.run(poll_interval_seconds=0, stop_event=stop)

    mock_alpaca.get_snapshots.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_processes_force_event(tmp_db):
    """Si force_event se activa durante run(), el loop continúa."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _ = _make_monitor(tmp_db)
    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    stop = asyncio.Event()
    force = asyncio.Event()

    async def trigger_and_stop():
        force.set()
        await asyncio.sleep(0.05)
        stop.set()

    monitor._force_event = force

    await asyncio.gather(
        monitor.run(poll_interval_seconds=0, stop_event=stop),
        trigger_and_stop(),
    )


# ── _wait_or_stop ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wait_or_stop_without_stop_event():
    """_wait_or_stop con stop_event=None usa asyncio.sleep."""
    from agents.monitor import _wait_or_stop
    await _wait_or_stop(None, 0)


@pytest.mark.asyncio
async def test_wait_or_stop_with_stop_event_times_out():
    """_wait_or_stop con stop_event no activo espera el timeout."""
    from agents.monitor import _wait_or_stop
    stop = asyncio.Event()
    await _wait_or_stop(stop, 0)


@pytest.mark.asyncio
async def test_wait_or_stop_with_force_event_interrupts():
    """_wait_or_stop retorna inmediatamente si force_event ya está seteado."""
    from agents.monitor import _wait_or_stop
    stop = asyncio.Event()
    force = asyncio.Event()
    force.set()
    await _wait_or_stop(stop, 60, force_event=force)


# ── _interruptible_sleep ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interruptible_sleep_without_stop_event():
    """_interruptible_sleep con stop_event=None duerme el tiempo indicado (0s en test)."""
    from agents.monitor import _interruptible_sleep
    await _interruptible_sleep(0, None)


@pytest.mark.asyncio
async def test_interruptible_sleep_with_already_set_stop_event():
    """_interruptible_sleep retorna inmediatamente si stop_event ya está seteado."""
    from agents.monitor import _interruptible_sleep
    stop = asyncio.Event()
    stop.set()
    await _interruptible_sleep(60, stop)


@pytest.mark.asyncio
async def test_interruptible_sleep_with_force_event_interrupts():
    """_interruptible_sleep retorna inmediatamente si force_event ya está seteado."""
    from agents.monitor import _interruptible_sleep
    stop = asyncio.Event()
    force = asyncio.Event()
    force.set()
    await _interruptible_sleep(60, stop, force_event=force)


# ── _init_watchlist_from_alpaca ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_watchlist_from_alpaca_populates_storage(tmp_db):
    """_init_watchlist_from_alpaca inserta activos de Alpaca en la BD."""
    from agents.monitor import _init_watchlist_from_alpaca

    mock_alpaca = MagicMock()
    mock_alpaca.get_assets = AsyncMock(return_value=[
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
    ])
    logger = _make_logger()

    await _init_watchlist_from_alpaca(mock_alpaca, tmp_db, logger)

    wl = tmp_db.get_watchlist()
    symbols = {r["symbol"] for r in wl}
    assert "AAPL" in symbols
    assert "MSFT" in symbols


@pytest.mark.asyncio
async def test_init_watchlist_from_alpaca_handles_exception(tmp_db):
    """Si get_assets falla, _init_watchlist_from_alpaca no propaga la excepción."""
    from agents.monitor import _init_watchlist_from_alpaca

    mock_alpaca = MagicMock()
    mock_alpaca.get_assets = AsyncMock(side_effect=RuntimeError("API error"))
    logger = _make_logger()

    await _init_watchlist_from_alpaca(mock_alpaca, tmp_db, logger)
    assert tmp_db.get_watchlist() == []


# ── _generate_chart ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_chart_returns_path_on_success(tmp_db, tmp_path):
    """_generate_chart retorna la ruta del PNG cuando chart_generator funciona."""
    import unittest.mock as mock_mod

    monitor, _, _, _ = _make_monitor(tmp_db)
    bars = [
        {"timestamp": "2025-01-02T00:00:00Z", "open": 170.0, "high": 175.0, "low": 169.0, "close": 173.0, "volume": 1_000_000},
    ]
    fake_path = str(tmp_path / "AAPL_opportunity_test.png")

    with mock_mod.patch("src.chart_generator.generate_candlestick_chart", return_value=fake_path):
        path = monitor._generate_chart("AAPL", bars, "1Day", "opportunity")

    assert path == fake_path


@pytest.mark.asyncio
async def test_generate_chart_returns_none_on_empty_bars(tmp_db):
    """_generate_chart retorna None si la lista de barras está vacía."""
    monitor, _, _, _ = _make_monitor(tmp_db)
    path = monitor._generate_chart("AAPL", [], "1Day", "opportunity")
    assert path is None


@pytest.mark.asyncio
async def test_generate_chart_returns_none_on_exception(tmp_db):
    """_generate_chart captura excepciones y devuelve None."""
    import unittest.mock as mock_mod

    monitor, _, _, _ = _make_monitor(tmp_db)
    bars = [{"timestamp": "2025-01-02T00:00:00Z", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}]

    with mock_mod.patch("src.chart_generator.generate_candlestick_chart", side_effect=RuntimeError("mpl error")):
        path = monitor._generate_chart("AAPL", bars, "1Day", "opportunity")

    assert path is None


# ── run_monitor — early returns ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_monitor_returns_early_without_alpaca_keys(monkeypatch):
    """run_monitor retorna sin hacer nada si faltan credenciales de Alpaca."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    from agents.monitor import run_monitor
    await run_monitor()


@pytest.mark.asyncio
async def test_run_monitor_returns_early_when_alpaca_unavailable(monkeypatch):
    """run_monitor retorna si AlpacaClient.is_available() falla."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    import agents.monitor as monitor_mod

    class _FakeAlpaca:
        def __init__(self, *a, **kw):
            pass

        async def is_available(self):
            return False, "connection refused"

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpaca)

    from agents.monitor import run_monitor
    await run_monitor()


# ── run_monitor — setup completo ──────────────────────────────────────────────

def _make_run_monitor_mocks(monkeypatch, watchlist=None, watchlist_symbols_env=None, allow_short=None):
    """Helper: parchea run_monitor para tests de setup completo."""
    import agents.monitor as monitor_mod

    class _FakeAlpaca:
        def __init__(self, *a, **kw):
            pass

        async def is_available(self):
            return True, None

        async def get_account(self):
            return {"equity": 10000.0, "cash": 5000.0}

        def is_crypto(self, sym):
            return False

        async def get_asset(self, sym):
            return {"symbol": sym, "name": f"{sym} Inc", "exchange": "NASDAQ", "marginable": True, "shortable": True}

        async def get_assets(self):
            return [{"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "marginable": True, "shortable": False}]

    mock_storage = MagicMock()
    mock_storage.get_watchlist.return_value = watchlist if watchlist is not None else [
        {"symbol": "AAPL", "company": "Apple", "shortable": 0, "exchange": "NASDAQ", "marginable": 1}
    ]

    mock_model_cfg = MagicMock()
    mock_model_cfg.model = "qwen2.5:7b"
    mock_config = MagicMock()
    mock_config.trading = {
        "analysis_interval_hours": 4,
        "max_position_value": 500,
        "poll_interval_seconds": 60,
        "allow_short_selling": False,
    }
    mock_config.models = {
        "technical": mock_model_cfg,
        "technical_entry": mock_model_cfg,
        "economics": mock_model_cfg,
        "news_classifier": mock_model_cfg,
        "trader_opportunity": mock_model_cfg,
        "trader_entry": mock_model_cfg,
        "orchestrator": mock_model_cfg,
    }

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    if watchlist_symbols_env is not None:
        monkeypatch.setenv("WATCHLIST_SYMBOLS", watchlist_symbols_env)
    else:
        monkeypatch.delenv("WATCHLIST_SYMBOLS", raising=False)
    if allow_short is not None:
        monkeypatch.setenv("ALLOW_SHORT_SELLING", allow_short)
    else:
        monkeypatch.delenv("ALLOW_SHORT_SELLING", raising=False)

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpaca)
    monkeypatch.setattr(monitor_mod, "StorageLayer", MagicMock(return_value=mock_storage))
    monkeypatch.setattr(monitor_mod, "load_from_yaml", lambda p: mock_config)
    monkeypatch.setattr(monitor_mod, "OllamaProvider", MagicMock())
    for fn in [
        "build_technical_watching_agent", "build_technical_entry_agent",
        "build_market_context_agent", "build_company_analysis_agent",
        "build_trading_opportunity_agent", "build_trading_entry_agent",
    ]:
        monkeypatch.setattr(monitor_mod, fn, _fake_agent_builder)

    return mock_storage


@pytest.mark.asyncio
async def test_run_monitor_full_setup_runs_and_exits(monkeypatch):
    """run_monitor completa el setup completo y el monitor sale por stop_event."""
    _make_run_monitor_mocks(monkeypatch)

    stop_event = asyncio.Event()
    stop_event.set()

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)


@pytest.mark.asyncio
async def test_run_monitor_no_gmail_logs_warning(monkeypatch):
    """run_monitor registra advertencia cuando GMAIL no está configurado."""
    _make_run_monitor_mocks(monkeypatch)

    log_messages = []

    def on_log(msg):
        log_messages.append(msg)

    stop_event = asyncio.Event()
    stop_event.set()

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event, on_log=on_log)

    assert any("GMAIL" in m or "alertas" in m.lower() for m in log_messages)


@pytest.mark.asyncio
async def test_run_monitor_watchlist_from_env_symbols(monkeypatch):
    """run_monitor inicializa watchlist desde WATCHLIST_SYMBOLS cuando la BD está vacía."""
    _make_run_monitor_mocks(monkeypatch, watchlist=[], watchlist_symbols_env="AAPL,MSFT")

    stop_event = asyncio.Event()
    stop_event.set()

    import agents.monitor as monitor_mod
    mock_storage = monitor_mod.StorageLayer.return_value

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)

    assert mock_storage.upsert_watchlist.called


@pytest.mark.asyncio
async def test_run_monitor_full_setup_with_short_selling_enabled(monkeypatch):
    """run_monitor registra que short selling está habilitado cuando ALLOW_SHORT_SELLING=true."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALLOW_SHORT_SELLING", "true")
    monkeypatch.delenv("WATCHLIST_SYMBOLS", raising=False)

    import agents.monitor as monitor_mod

    class _FakeAlpaca:
        def __init__(self, *a, **kw):
            pass

        async def is_available(self):
            return True, None

        async def get_account(self):
            return {"equity": 10000.0, "cash": 5000.0}

    mock_storage = MagicMock()
    mock_storage.get_watchlist.return_value = [
        {"symbol": "AAPL", "company": "Apple", "shortable": 1}
    ]
    mock_model_cfg = MagicMock()
    mock_model_cfg.model = "qwen2.5:7b"
    mock_config = MagicMock()
    mock_config.trading = {
        "analysis_interval_hours": 4,
        "max_position_value": 500,
        "poll_interval_seconds": 60,
        "allow_short_selling": False,
    }
    mock_config.models = {
        "technical": mock_model_cfg,
        "technical_entry": mock_model_cfg,
        "economics": mock_model_cfg,
        "news_classifier": mock_model_cfg,
        "trader_opportunity": mock_model_cfg,
        "trader_entry": mock_model_cfg,
        "orchestrator": mock_model_cfg,
    }

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    stop_event = asyncio.Event()
    stop_event.set()

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpaca)
    monkeypatch.setattr(monitor_mod, "StorageLayer", MagicMock(return_value=mock_storage))
    monkeypatch.setattr(monitor_mod, "load_from_yaml", lambda p: mock_config)
    monkeypatch.setattr(monitor_mod, "OllamaProvider", MagicMock())
    for fn in [
        "build_technical_watching_agent", "build_technical_entry_agent",
        "build_market_context_agent", "build_company_analysis_agent",
        "build_trading_opportunity_agent", "build_trading_entry_agent",
    ]:
        monkeypatch.setattr(monitor_mod, fn, _fake_agent_builder)

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)


@pytest.mark.asyncio
async def test_run_monitor_watchlist_from_env_crypto_excluded(monkeypatch):
    """run_monitor excluye símbolos crypto cuando WATCHLIST_SYMBOLS los incluye."""
    import agents.monitor as monitor_mod

    class _FakeAlpacaWithCrypto:
        def __init__(self, *a, **kw):
            pass

        async def is_available(self):
            return True, None

        async def get_account(self):
            return {"equity": 10000.0, "cash": 5000.0}

        def is_crypto(self, sym):
            return sym == "BTC"

        async def get_asset(self, sym):
            return {"symbol": sym, "name": f"{sym} Inc", "exchange": "NASDAQ", "marginable": True, "shortable": False}

    mock_storage = MagicMock()
    mock_storage.get_watchlist.return_value = []

    mock_model_cfg = MagicMock()
    mock_model_cfg.model = "qwen2.5:7b"
    mock_config = MagicMock()
    mock_config.trading = {"analysis_interval_hours": 4, "max_position_value": 500, "poll_interval_seconds": 60, "allow_short_selling": False}
    mock_config.models = {
        "technical": mock_model_cfg,
        "technical_entry": mock_model_cfg,
        "economics": mock_model_cfg,
        "news_classifier": mock_model_cfg,
        "trader_opportunity": mock_model_cfg,
        "trader_entry": mock_model_cfg,
        "orchestrator": mock_model_cfg,
    }

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("WATCHLIST_SYMBOLS", "AAPL,BTC")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("ALLOW_SHORT_SELLING", raising=False)

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpacaWithCrypto)
    monkeypatch.setattr(monitor_mod, "StorageLayer", MagicMock(return_value=mock_storage))
    monkeypatch.setattr(monitor_mod, "load_from_yaml", lambda p: mock_config)
    monkeypatch.setattr(monitor_mod, "OllamaProvider", MagicMock())
    for fn in [
        "build_technical_watching_agent", "build_technical_entry_agent",
        "build_market_context_agent", "build_company_analysis_agent",
        "build_trading_opportunity_agent", "build_trading_entry_agent",
    ]:
        monkeypatch.setattr(monitor_mod, fn, _fake_agent_builder)

    stop_event = asyncio.Event()
    stop_event.set()

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)

    calls = [c[0][0] for c in mock_storage.upsert_watchlist.call_args_list]
    assert "AAPL" in calls
    assert "BTC" not in calls


@pytest.mark.asyncio
async def test_run_monitor_watchlist_get_asset_exception_uses_fallback(monkeypatch):
    """run_monitor usa fallback mínimo cuando get_asset() lanza excepción."""
    import agents.monitor as monitor_mod

    class _FakeAlpacaRaising:
        def __init__(self, *a, **kw):
            pass

        async def is_available(self):
            return True, None

        async def get_account(self):
            return {"equity": 10000.0, "cash": 5000.0}

        def is_crypto(self, sym):
            return False

        async def get_asset(self, sym):
            raise ConnectionError("timeout")

    mock_storage = MagicMock()
    mock_storage.get_watchlist.return_value = []

    mock_model_cfg = MagicMock()
    mock_model_cfg.model = "qwen2.5:7b"
    mock_config = MagicMock()
    mock_config.trading = {"analysis_interval_hours": 4, "max_position_value": 500, "poll_interval_seconds": 60, "allow_short_selling": False}
    mock_config.models = {
        "technical": mock_model_cfg,
        "technical_entry": mock_model_cfg,
        "economics": mock_model_cfg,
        "news_classifier": mock_model_cfg,
        "trader_opportunity": mock_model_cfg,
        "trader_entry": mock_model_cfg,
        "orchestrator": mock_model_cfg,
    }

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("WATCHLIST_SYMBOLS", "AAPL")
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("ALLOW_SHORT_SELLING", raising=False)

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpacaRaising)
    monkeypatch.setattr(monitor_mod, "StorageLayer", MagicMock(return_value=mock_storage))
    monkeypatch.setattr(monitor_mod, "load_from_yaml", lambda p: mock_config)
    monkeypatch.setattr(monitor_mod, "OllamaProvider", MagicMock())
    for fn in [
        "build_technical_watching_agent", "build_technical_entry_agent",
        "build_market_context_agent", "build_company_analysis_agent",
        "build_trading_opportunity_agent", "build_trading_entry_agent",
    ]:
        monkeypatch.setattr(monitor_mod, fn, _fake_agent_builder)

    stop_event = asyncio.Event()
    stop_event.set()

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)

    mock_storage.upsert_watchlist.assert_called()


@pytest.mark.asyncio
async def test_run_monitor_watchlist_init_from_alpaca_when_env_empty(monkeypatch):
    """run_monitor llama _init_watchlist_from_alpaca cuando WATCHLIST_SYMBOLS no está seteado."""
    import agents.monitor as monitor_mod

    class _FakeAlpaca:
        def __init__(self, *a, **kw):
            pass

        async def is_available(self):
            return True, None

        async def get_account(self):
            return {"equity": 10000.0, "cash": 5000.0}

        async def get_assets(self):
            return [{"symbol": "AAPL", "name": "Apple", "exchange": "NASDAQ", "marginable": True, "shortable": False}]

    mock_storage = MagicMock()
    mock_storage.get_watchlist.return_value = []

    mock_model_cfg = MagicMock()
    mock_model_cfg.model = "qwen2.5:7b"
    mock_config = MagicMock()
    mock_config.trading = {"analysis_interval_hours": 4, "max_position_value": 500, "poll_interval_seconds": 60, "allow_short_selling": False}
    mock_config.models = {
        "technical": mock_model_cfg,
        "technical_entry": mock_model_cfg,
        "economics": mock_model_cfg,
        "news_classifier": mock_model_cfg,
        "trader_opportunity": mock_model_cfg,
        "trader_entry": mock_model_cfg,
        "orchestrator": mock_model_cfg,
    }

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.delenv("WATCHLIST_SYMBOLS", raising=False)
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.delenv("ALLOW_SHORT_SELLING", raising=False)

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpaca)
    monkeypatch.setattr(monitor_mod, "StorageLayer", MagicMock(return_value=mock_storage))
    monkeypatch.setattr(monitor_mod, "load_from_yaml", lambda p: mock_config)
    monkeypatch.setattr(monitor_mod, "OllamaProvider", MagicMock())
    for fn in [
        "build_technical_watching_agent", "build_technical_entry_agent",
        "build_market_context_agent", "build_company_analysis_agent",
        "build_trading_opportunity_agent", "build_trading_entry_agent",
    ]:
        monkeypatch.setattr(monitor_mod, fn, _fake_agent_builder)

    stop_event = asyncio.Event()
    stop_event.set()

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)

    mock_storage.upsert_watchlist.assert_called()


@pytest.mark.asyncio
async def test_run_monitor_on_event_handler_covers_all_branches(monkeypatch):
    """El handler on_event del EventBus cubre MODEL_CALLED, MODEL_FINISHED, TOOL_CALLED."""
    import agents.monitor as monitor_mod
    from agent_framework.events import EventType

    captured_callback = None

    class _FakeBus:
        def subscribe(self, pattern, callback):
            nonlocal captured_callback
            captured_callback = callback

    _make_run_monitor_mocks(monkeypatch)
    monkeypatch.setattr(monitor_mod, "EventBus", lambda: _FakeBus())

    stop_event = asyncio.Event()
    stop_event.set()

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)

    assert captured_callback is not None, "on_event no fue suscrito al bus"

    class _FakeEvent:
        def __init__(self, etype, data):
            self.type = etype
            self.data = data

    await captured_callback(_FakeEvent(EventType.MODEL_CALLED, {"role": "technical"}))
    await captured_callback(_FakeEvent(EventType.MODEL_FINISHED, {
        "role": "technical", "prompt": "p", "response": "r", "system_prompt": "s",
    }))
    await captured_callback(_FakeEvent(EventType.TOOL_CALLED, {"tool": "get_price"}))
    await captured_callback(_FakeEvent("UNKNOWN_EVENT", {}))


# ── run_monitor_sync ──────────────────────────────────────────────────────────

def test_run_monitor_sync_calls_asyncio_run(monkeypatch):
    """run_monitor_sync llama asyncio.run() con run_monitor correctamente."""
    import agents.monitor as monitor_mod

    called_with = {}

    async def _fake_run_monitor(**kwargs):
        called_with.update(kwargs)

    monkeypatch.setattr(monitor_mod, "run_monitor", _fake_run_monitor)

    stop = asyncio.Event()
    force = asyncio.Event()

    from agents.monitor import run_monitor_sync
    run_monitor_sync(stop_event=stop, force_event=force)

    assert called_with.get("stop_event") is stop
    assert called_with.get("force_event") is force
