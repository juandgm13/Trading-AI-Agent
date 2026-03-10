"""
Tests del PriceMonitor con máquina de estados.
Usan mocks para simular Alpaca y los sub-agentes — no requieren credenciales.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.logger import AgentLogger


def _make_logger():
    """Helper: crea un AgentLogger silencioso para tests."""
    return AgentLogger(level="ERROR")  # solo errores críticos en tests


def _make_monitor(tmp_db, on_log=None):
    """Helper: crea un PriceMonitor con mocks para todos los sub-agentes."""
    from agents.monitor import PriceMonitor

    mock_alpaca = MagicMock()
    mock_alpaca.get_bars = AsyncMock(return_value=[
        {"timestamp": "t", "open": 175.0, "high": 176.0, "low": 174.0, "close": 175.0, "volume": 1000}
    ])
    mock_alpaca.place_order = AsyncMock(return_value={"order_id": "ord-test", "status": "submitted"})

    mock_tech = MagicMock()
    mock_tech.run = AsyncMock(return_value=json.dumps({
        "trend": "BULLISH",
        "signal": "BUY",
        "target_price": 185.0,
        "stop_loss": 170.0,
        "confidence": 75,
        "summary": "Strong uptrend",
    }))

    mock_econ = MagicMock()
    mock_econ.run = AsyncMock(return_value=json.dumps({
        "sentiment": "POSITIVE",
        "market_context": "RISK_ON",
        "key_events": ["Strong earnings"],
        "confidence": 70,
        "summary": "Positive news flow",
    }))

    mock_trader = MagicMock()
    mock_trader.run = AsyncMock(return_value=json.dumps({
        "decision": "HOLD",
        "symbol": "AAPL",
        "qty": None,
        "reasoning": "Waiting for better entry",
    }))

    logger = _make_logger()

    monitor = PriceMonitor(
        alpaca=mock_alpaca,
        storage=tmp_db,
        technical_agent=mock_tech,
        economics_agent=mock_econ,
        trading_agent=mock_trader,
        logger=logger,
        gnews_api_key="test-key",
        max_qty=10,
        analysis_interval_hours=4.0,
    )
    return monitor, mock_alpaca, mock_tech, mock_econ, mock_trader


# ── Estado WATCHING ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watching_no_analysis_when_not_due(tmp_db):
    """En estado WATCHING, si no ha pasado el intervalo, no se analiza."""
    from datetime import datetime, timezone

    monitor, _, mock_tech, mock_econ, mock_trader = _make_monitor(tmp_db)

    # Registrar análisis reciente (hace 1 hora, intervalo = 4h)
    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    await monitor._process_symbol("AAPL", snap, state_row)

    mock_tech.run.assert_not_called()
    mock_trader.run.assert_not_called()


@pytest.mark.asyncio
async def test_watching_triggers_analysis_when_due(tmp_db):
    """En estado WATCHING, si pasó el intervalo, se ejecuta el análisis completo."""
    from datetime import datetime, timezone, timedelta

    monitor, _, mock_tech, mock_econ, mock_trader = _make_monitor(tmp_db)

    # Análisis hace 5 horas (> intervalo de 4h)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=old_time)
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    with pytest.MonkeyPatch.context() as mp:
        # Mock SearchNewsTool para evitar llamada a GNews
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._process_symbol("AAPL", snap, state_row)

    mock_tech.run.assert_awaited()


@pytest.mark.asyncio
async def test_watching_no_analysis_on_first_cycle_if_no_time_elapsed(tmp_db):
    """Sin last_analysis_at → análisis siempre due (primera vez)."""
    monitor, _, mock_tech, _, _ = _make_monitor(tmp_db)
    state_row = tmp_db.get_symbol_state("AAPL")  # sin last_analysis_at

    assert monitor._analysis_due(state_row) is True


# ── Estado OPPORTUNITY_FOUND ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opportunity_found_runs_short_term_analysis(tmp_db):
    """En OPPORTUNITY_FOUND, se ejecuta el análisis técnico de corto plazo."""
    monitor, _, mock_tech, _, mock_trader = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    await monitor._process_symbol("AAPL", snap, state_row)

    mock_tech.run.assert_awaited()
    mock_trader.run.assert_awaited()


@pytest.mark.asyncio
async def test_opportunity_found_opens_position_on_decision(tmp_db):
    """Si TradingAgent dice OPEN_POSITION, se coloca la orden y el estado cambia."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION",
        "symbol": "AAPL",
        "qty": 5,
        "reasoning": "Strong signal",
    })

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.place_order.assert_awaited_once()
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "POSITION_OPEN"
    assert new_state["position_qty"] == 5.0


@pytest.mark.asyncio
async def test_opportunity_found_reject_returns_to_watching(tmp_db):
    """Si TradingAgent dice REJECT, el estado vuelve a WATCHING."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "REJECT",
        "symbol": "AAPL",
        "qty": None,
        "reasoning": "Signal too weak",
    })

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    await monitor._process_symbol("AAPL", snap, state_row)

    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "WATCHING"


# ── Estado POSITION_OPEN ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_position_open_sells_at_target(tmp_db):
    """Cuando el precio alcanza target_price, se vende y vuelve a WATCHING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=180.0, stop_loss=165.0, position_qty=5.0,
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 180.5, "volume": 1000, "timestamp": "t"}  # >= target
    await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.place_order.assert_awaited_once()
    call_args = mock_alpaca.place_order.call_args
    assert call_args[0][2] == "sell"  # side

    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "WATCHING"
    assert new_state["target_price"] is None


@pytest.mark.asyncio
async def test_position_open_sells_at_stop_loss(tmp_db):
    """Cuando el precio cae al stop_loss, se vende."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=180.0, stop_loss=165.0, position_qty=5.0,
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 163.0, "volume": 1000, "timestamp": "t"}  # <= stop_loss
    await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.place_order.assert_awaited_once()
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "WATCHING"


@pytest.mark.asyncio
async def test_position_open_no_action_between_levels(tmp_db):
    """No se vende si el precio está entre stop_loss y target_price."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=180.0, stop_loss=165.0, position_qty=5.0,
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.place_order.assert_not_awaited()
    assert tmp_db.get_symbol_state("AAPL")["state"] == "POSITION_OPEN"


# ── Loop principal ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_stop_exits_loop(tmp_db):
    """monitor.stop() detiene el loop antes del siguiente ciclo."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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
async def test_force_analysis_flag_triggers_full_analysis(tmp_db):
    """El flag _force_analysis provoca análisis aunque el intervalo no haya pasado."""
    from datetime import datetime, timezone

    monitor, _, mock_tech, mock_econ, _ = _make_monitor(tmp_db)
    monitor._force_analysis = True  # simular force

    # Análisis reciente (no debería analizar sin force)
    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)
    state_row = tmp_db.get_symbol_state("AAPL")

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._process_symbol("AAPL", snap, state_row)

    mock_tech.run.assert_awaited()


@pytest.mark.asyncio
async def test_check_all_uses_batch_snapshots(tmp_db):
    """_check_all llama a get_snapshots una vez con todos los símbolos del batch."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    recent = datetime.now(timezone.utc).isoformat()
    for sym in ["AAPL", "MSFT", "NVDA"]:
        tmp_db.upsert_watchlist(sym, "", 2.0)
        # Marcar análisis reciente para evitar disparo de análisis completo
        tmp_db.upsert_symbol_state(sym, state="WATCHING", last_analysis_at=recent)

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
    """_check_all guarda snapshots de precio para cada símbolo."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    # Marcar análisis reciente para evitar que dispare análisis completo
    tmp_db.upsert_symbol_state(
        "AAPL", state="WATCHING",
        last_analysis_at=datetime.now(timezone.utc).isoformat(),
    )

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    assert tmp_db.get_last_price("AAPL") == pytest.approx(175.0)
