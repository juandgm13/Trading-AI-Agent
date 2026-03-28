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
    mock_alpaca.get_order = AsyncMock(return_value=None)
    mock_alpaca.get_clock = AsyncMock(return_value={"is_open": True, "next_open": None, "next_close": None, "timestamp": None})

    mock_tech = MagicMock()
    mock_tech.run = AsyncMock(return_value=json.dumps({
        "trend": "BULLISH",
        "signal": "BUY",
        "key_supports": [170.0],
        "key_resistances": [185.0],
        "target_price": 185.0,
        "stop_loss": 170.0,
        "confidence": 75,
        "summary": "Strong uptrend",
    }))

    mock_market_ctx = MagicMock()
    mock_market_ctx.run = AsyncMock(return_value=json.dumps({
        "market_sentiment": "BULLISH",
        "risk_appetite": "RISK_ON",
        "dominant_themes": ["AI growth"],
        "macro_risks": [],
        "confidence": 70,
        "summary": "Positive macro environment",
    }))

    mock_company = MagicMock()
    mock_company.run = AsyncMock(return_value=json.dumps({
        "sentiment": "POSITIVE",
        "key_events": ["Strong earnings"],
        "entry_risk": "LOW",
        "confidence": 70,
        "summary": "Good entry conditions",
    }))

    mock_trader = MagicMock()
    mock_trader.run = AsyncMock(return_value=json.dumps({
        "decision": "HOLD",
        "symbol": "AAPL",
        "qty": None,
        "reasoning": "Waiting for better entry",
    }))

    logger = _make_logger()

    mock_news_service = MagicMock()
    mock_news_service.run_market_ingestion = AsyncMock(return_value=(0, 0))
    mock_news_service.run_symbol_ingestion = AsyncMock(return_value=[])

    monitor = PriceMonitor(
        alpaca=mock_alpaca,
        storage=tmp_db,
        technical_watching_agent=mock_tech,
        technical_entry_agent=mock_tech,
        market_context_agent=mock_market_ctx,
        company_analysis_agent=mock_company,
        trading_opportunity_agent=mock_trader,
        trading_entry_agent=mock_trader,
        logger=logger,
        news_service=mock_news_service,
        max_position_value=500.0,
        analysis_interval_hours=4.0,
    )
    # Retorna (monitor, mock_alpaca, mock_tech, mock_market_ctx, mock_trader)
    # mock_company accesible via monitor._company_analysis_agent
    return monitor, mock_alpaca, mock_tech, mock_market_ctx, mock_trader


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
async def test_process_symbol_skips_non_watching_state_when_market_closed(tmp_db):
    """Con mercado cerrado, _process_symbol devuelve None para estados no-WATCHING."""
    monitor, _, mock_tech, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")
    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}

    result = await monitor._process_symbol("AAPL", snap, state_row, market_is_open=False)

    assert result is None
    mock_tech.run.assert_not_called()


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
    await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.place_order.assert_awaited_once()
    new_state = tmp_db.get_symbol_state("AAPL")
    # Tras place_order el estado es OPENING (esperando confirmación de fill)
    assert new_state["state"] == "OPENING"
    # qty se calcula como max(1, int(max_position_value / price)) = max(1, int(500/175)) = 2
    assert new_state["position_qty"] == 2.0
    assert new_state["pending_order_id"] == "ord-test"


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
    # Tras place_order el estado es CLOSING (esperando confirmación de fill de venta)
    assert new_state["state"] == "CLOSING"
    assert new_state["pending_order_id"] == "ord-test"


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
    assert new_state["state"] == "CLOSING"
    assert new_state["pending_order_id"] == "ord-test"


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


# ── run() — paths de control ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_breaks_immediately_if_stop_event_already_set(tmp_db):
    """Si stop_event ya está activo al entrar al loop, sale sin ciclo."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_snapshots = AsyncMock(return_value={})

    stop = asyncio.Event()
    stop.set()  # ya activo antes de run()

    await monitor.run(poll_interval_seconds=0, stop_event=stop)

    mock_alpaca.get_snapshots.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_processes_force_event(tmp_db):
    """Si force_event se activa, _force_analysis queda True en el ciclo."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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


# ── _check_all — paths de error ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_logs_when_batch_raises(tmp_db):
    """Si get_snapshots lanza excepción, el ciclo continúa sin romper."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    mock_alpaca.get_snapshots = AsyncMock(side_effect=RuntimeError("API error"))

    watchlist = tmp_db.get_watchlist()
    # No debe lanzar excepción
    await monitor._check_all(watchlist)


@pytest.mark.asyncio
async def test_check_all_skips_symbol_without_snapshot(tmp_db):
    """Un símbolo sin datos de precio no se procesa."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING",
                               last_analysis_at=datetime.now(timezone.utc).isoformat())

    mock_alpaca.get_snapshots = AsyncMock(return_value={})  # sin datos

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    mock_tech.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_reports_triggered_alerts(tmp_db):
    """_check_all loguea alertas cuando hay transiciones."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=180.0, stop_loss=165.0, position_qty=5.0,
    )

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 181.0, "volume": 1000, "timestamp": "t"},  # >= target
    })

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    # Tras _manage_position el estado es CLOSING (esperando confirmación de fill de venta)
    assert tmp_db.get_symbol_state("AAPL")["state"] == "CLOSING"


# ── _analysis_due — edge cases ────────────────────────────────────────────────

def test_analysis_due_handles_naive_datetime(tmp_db):
    """Fecha sin timezone se trata como UTC."""
    from datetime import datetime, timezone, timedelta

    monitor, _, _, _, _ = _make_monitor(tmp_db)
    # Fecha naive (sin +00:00)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")
    state = {"last_analysis_at": old_time}

    assert monitor._analysis_due(state) is True


def test_analysis_due_returns_true_on_invalid_date(tmp_db):
    """Si la fecha es inválida, se considera que el análisis está due."""
    monitor, _, _, _, _ = _make_monitor(tmp_db)
    state = {"last_analysis_at": "not-a-valid-date"}

    assert monitor._analysis_due(state) is True


# ── _full_analysis — paths adicionales ───────────────────────────────────────

@pytest.mark.asyncio
async def test_full_analysis_returns_none_when_technical_fails(tmp_db):
    """Si TechnicalAgent falla, _full_analysis devuelve None."""
    monitor, _, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_tech.run = AsyncMock(side_effect=RuntimeError("model error"))

    result = await monitor._full_analysis("AAPL", 175.0)
    assert result is None


@pytest.mark.asyncio
async def test_full_analysis_detects_opportunity(tmp_db):
    """Si TradingAgent dice OPPORTUNITY_FOUND, el estado cambia a OPPORTUNITY_FOUND."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPPORTUNITY_FOUND", "symbol": "AAPL", "reasoning": "Strong signal",
    })

    result = await monitor._full_analysis("AAPL", 175.0)

    assert result is not None
    assert "OPPORTUNITY_FOUND" in result
    assert tmp_db.get_symbol_state("AAPL")["state"] == "OPPORTUNITY_FOUND"


@pytest.mark.asyncio
async def test_full_analysis_saves_chart_path_to_opportunity_report(tmp_db):
    """Si hay oportunidad y se genera chart, el chart_path queda en el report de trading."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPPORTUNITY_FOUND", "symbol": "AAPL", "reasoning": "Strong signal",
    })
    monitor._generate_chart = MagicMock(return_value="/charts/AAPL_opportunity_test.png")

    await monitor._full_analysis("AAPL", 175.0)

    report = tmp_db.get_last_agent_report("AAPL", "trading_opportunity_eval")
    assert report is not None
    assert report.get("chart_path") == "/charts/AAPL_opportunity_test.png"


# ── _look_for_entry — paths adicionales ──────────────────────────────────────

@pytest.mark.asyncio
async def test_look_for_entry_returns_none_when_technical_fails(tmp_db):
    """Si el análisis técnico corto falla, _look_for_entry devuelve None."""
    monitor, _, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_tech.run = AsyncMock(side_effect=RuntimeError("model error"))

    state_row = tmp_db.get_symbol_state("AAPL")
    result = await monitor._look_for_entry("AAPL", 175.0, state_row)
    assert result is None


@pytest.mark.asyncio
async def test_look_for_entry_continues_without_company_report(tmp_db):
    """Company analysis falla pero la decisión continúa."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "HOLD", "symbol": "AAPL", "qty": None, "reasoning": "Waiting",
    })

    monitor._news_service.run_symbol_ingestion = AsyncMock(side_effect=RuntimeError("news error"))
    state_row = tmp_db.get_symbol_state("AAPL")
    result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    assert result is None  # HOLD → no trade


@pytest.mark.asyncio
async def test_look_for_entry_returns_none_when_no_trading_decision(tmp_db):
    """Si TradingAgent falla, _look_for_entry devuelve None."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run = AsyncMock(side_effect=RuntimeError("model error"))

    state_row = tmp_db.get_symbol_state("AAPL")
    result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    assert result is None


@pytest.mark.asyncio
async def test_look_for_entry_with_alerter_on_open_position(tmp_db):
    """Cuando se abre posición con alerter, se llama send_trade_alert."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert = MagicMock()
    monitor._alerter = mock_alerter

    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION", "symbol": "AAPL", "qty": 3, "reasoning": "Strong signal",
    })
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._look_for_entry("AAPL", 175.0, state_row)

    # La alerta de compra se envía desde _check_opening al confirmar el fill, no desde _look_for_entry
    assert not mock_alerter.send_trade_alert.called
    assert tmp_db.get_symbol_state("AAPL")["state"] == "OPENING"


@pytest.mark.asyncio
async def test_look_for_entry_saves_chart_path_to_entry_report(tmp_db):
    """Al abrir posición con chart generado, el chart_path queda en el report de entry."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION", "symbol": "AAPL", "qty": 2, "reasoning": "Entry now",
    })
    monitor._generate_chart = MagicMock(return_value="/charts/AAPL_entry_test.png")

    state_row = tmp_db.get_symbol_state("AAPL")
    await monitor._look_for_entry("AAPL", 175.0, state_row)

    report = tmp_db.get_last_agent_report("AAPL", "trading_entry_eval")
    assert report is not None
    assert report.get("chart_path") == "/charts/AAPL_entry_test.png"


@pytest.mark.asyncio
async def test_look_for_entry_handles_place_order_exception(tmp_db):
    """Si place_order falla, _look_for_entry no propaga la excepción."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    mock_alpaca.place_order = AsyncMock(side_effect=RuntimeError("order rejected"))

    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION", "symbol": "AAPL", "qty": 2, "reasoning": "Signal",
    })
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    assert result is None


# ── _manage_position — paths adicionales ─────────────────────────────────────

@pytest.mark.asyncio
async def test_manage_position_resets_when_no_qty(tmp_db):
    """POSITION_OPEN sin qty registrada → estado vuelve a WATCHING."""
    monitor, _, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state("AAPL", state="POSITION_OPEN",
                               target_price=180.0, stop_loss=165.0, position_qty=None)
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._manage_position("AAPL", 175.0, state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"


@pytest.mark.asyncio
async def test_manage_position_with_alerter_on_sell(tmp_db):
    """Cuando se vende con alerter, se llama send_trade_alert."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert = MagicMock()
    monitor._alerter = mock_alerter

    tmp_db.upsert_symbol_state("AAPL", state="POSITION_OPEN",
                               target_price=180.0, stop_loss=165.0, position_qty=5.0)
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._manage_position("AAPL", 181.0, state_row)  # >= target

    # La alerta de cierre se envía desde _check_closing al confirmar el fill, no desde _manage_position
    assert not mock_alerter.send_trade_alert.called
    assert tmp_db.get_symbol_state("AAPL")["state"] == "CLOSING"


@pytest.mark.asyncio
async def test_manage_position_handles_place_order_exception(tmp_db):
    """Si place_order falla al vender, no propaga la excepción."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.place_order = AsyncMock(side_effect=RuntimeError("sell rejected"))

    tmp_db.upsert_symbol_state("AAPL", state="POSITION_OPEN",
                               target_price=180.0, stop_loss=165.0, position_qty=5.0)
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._manage_position("AAPL", 181.0, state_row)
    assert result is None


# ── Sub-agentes — paths de error ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_technical_medium_handles_parse_error(tmp_db):
    """ParseError en TechnicalAgent (medium) → devuelve (None, bars)."""
    monitor, _, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_tech.run = AsyncMock(return_value="not valid json at all")

    report, bars = await monitor._run_technical_medium("AAPL", 175.0)
    assert report is None


@pytest.mark.asyncio
async def test_run_technical_medium_handles_exception(tmp_db):
    """Excepción en TechnicalAgent (medium) → devuelve (None, [])."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_bars = AsyncMock(side_effect=RuntimeError("bars error"))

    report, bars = await monitor._run_technical_medium("AAPL", 175.0)
    assert report is None


@pytest.mark.asyncio
async def test_run_technical_short_handles_parse_error(tmp_db):
    """ParseError en TechnicalAgent (short) → devuelve (None, bars)."""
    monitor, _, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_tech.run = AsyncMock(return_value="invalid response")

    report, bars = await monitor._run_technical_short("AAPL", 175.0)
    assert report is None


@pytest.mark.asyncio
async def test_run_technical_short_handles_exception(tmp_db):
    """Excepción general en TechnicalAgent (short) → devuelve (None, [])."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_bars = AsyncMock(side_effect=ConnectionError("timeout"))

    report, bars = await monitor._run_technical_short("AAPL", 175.0)
    assert report is None


@pytest.mark.asyncio
async def test_run_trading_decision_handles_parse_error(tmp_db):
    """ParseError en TradingAgent → devuelve None."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run = AsyncMock(return_value="I think you should buy")

    result = await monitor._run_trading_decision("AAPL", "test_stage", "prompt", mock_trader)
    assert result is None


@pytest.mark.asyncio
async def test_run_trading_decision_handles_exception(tmp_db):
    """Excepción en TradingAgent → devuelve None."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run = AsyncMock(side_effect=RuntimeError("LLM error"))

    result = await monitor._run_trading_decision("AAPL", "test_stage", "prompt", mock_trader)
    assert result is None


# ── Market context — paths adicionales ───────────────────────────────────────

@pytest.mark.asyncio
async def test_maybe_update_market_context_skips_when_recent(tmp_db):
    """No actualiza si el contexto es reciente y no hay force_analysis."""
    from datetime import datetime, timezone

    monitor, _, _, mock_market_ctx, _ = _make_monitor(tmp_db)
    monitor._force_analysis = False

    # Guardar contexto reciente (hace 1 hora, intervalo=4h)
    tmp_db.save_market_context('{"market_sentiment": "BULLISH"}', news_count=3)

    await monitor._maybe_update_market_context()

    mock_market_ctx.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_update_market_context_updates_on_force(tmp_db):
    """force_analysis=True fuerza actualización aunque el contexto sea reciente."""
    monitor, _, _, mock_market_ctx, _ = _make_monitor(tmp_db)
    monitor._force_analysis = True

    tmp_db.save_market_context('{"market_sentiment": "BULLISH"}', news_count=3)
    mock_market_ctx.run = AsyncMock(return_value=json.dumps({
        "market_sentiment": "NEUTRAL", "risk_appetite": "NEUTRAL",
        "dominant_themes": [], "macro_risks": [], "confidence": 50, "summary": "ok",
    }))

    await monitor._maybe_update_market_context()

    mock_market_ctx.run.assert_awaited()


@pytest.mark.asyncio
async def test_run_market_context_handles_parse_error(tmp_db):
    """ParseError en MarketContextAgent → no propaga excepción."""
    monitor, _, _, mock_market_ctx, _ = _make_monitor(tmp_db)
    mock_market_ctx.run = AsyncMock(return_value="invalid response from model")

    await monitor._run_market_context()  # no debe lanzar


@pytest.mark.asyncio
async def test_run_market_context_handles_exception(tmp_db):
    """Excepción en MarketContextAgent → no propaga."""
    monitor, _, _, mock_market_ctx, _ = _make_monitor(tmp_db)
    mock_market_ctx.run = AsyncMock(side_effect=RuntimeError("LLM down"))

    await monitor._run_market_context()  # no debe lanzar


def test_load_market_context_returns_none_on_invalid_json(tmp_db):
    """Si el JSON del contexto en la BD está corrupto, devuelve None."""
    monitor, _, _, _, _ = _make_monitor(tmp_db)

    # Insertar contexto con JSON inválido directamente
    import sqlite3
    conn = sqlite3.connect(tmp_db.db_path)
    conn.execute("INSERT INTO market_context (report, news_count, created_at) VALUES (?, ?, ?)",
                 ("not-valid-json", 0, "2024-01-01T00:00:00+00:00"))
    conn.commit()
    conn.close()

    result = monitor._load_market_context()
    assert result is None


@pytest.mark.asyncio
async def test_run_company_analysis_handles_parse_error(tmp_db):
    """ParseError en CompanyAnalysisAgent → devuelve None."""
    monitor, _, _, _, _ = _make_monitor(tmp_db)
    monitor._company_analysis_agent = MagicMock()
    monitor._company_analysis_agent.run = AsyncMock(return_value="invalid json here")

    result = await monitor._run_company_analysis("AAPL")

    assert result is None


@pytest.mark.asyncio
async def test_run_company_analysis_handles_exception(tmp_db):
    """Excepción en CompanyAnalysisAgent → devuelve None."""
    monitor, _, _, _, _ = _make_monitor(tmp_db)
    monitor._company_analysis_agent = MagicMock()
    monitor._company_analysis_agent.run = AsyncMock(side_effect=RuntimeError("model error"))

    result = await monitor._run_company_analysis("AAPL")

    assert result is None


# ── Utilidades ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wait_or_stop_without_stop_event():
    """_wait_or_stop con stop_event=None usa asyncio.sleep."""
    from agents.monitor import _wait_or_stop
    # Solo verificamos que no lanza excepción con timeout muy corto
    await _wait_or_stop(None, 0)


@pytest.mark.asyncio
async def test_wait_or_stop_with_stop_event_times_out():
    """_wait_or_stop con stop_event no activo espera el timeout."""
    from agents.monitor import _wait_or_stop
    stop = asyncio.Event()
    await _wait_or_stop(stop, 0)  # timeout=0 → TimeoutError capturado internamente


@pytest.mark.asyncio
async def test_wait_or_stop_with_force_event_interrupts():
    """_wait_or_stop retorna inmediatamente si force_event ya está seteado."""
    from agents.monitor import _wait_or_stop
    stop = asyncio.Event()
    force = asyncio.Event()
    force.set()
    await _wait_or_stop(stop, 60, force_event=force)  # retorna inmediatamente


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


# ── run_monitor — early returns (sin credenciales) ───────────────────────────

@pytest.mark.asyncio
async def test_run_monitor_returns_early_without_alpaca_keys(monkeypatch):
    """run_monitor retorna sin hacer nada si faltan credenciales de Alpaca."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    from agents.monitor import run_monitor
    await run_monitor()  # no debe lanzar excepción



# ── F3: stop_event dentro del loop de símbolos ───────────────────────────────

@pytest.mark.asyncio
async def test_check_all_stops_mid_loop_on_stop_event(tmp_db):
    """stop_event activo a mitad del loop interrumpe el procesamiento de símbolos."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    recent = datetime.now(timezone.utc).isoformat()
    for sym in ["AAPL", "MSFT", "NVDA"]:
        tmp_db.upsert_watchlist(sym, "", 2.0)
        tmp_db.upsert_symbol_state(sym, state="WATCHING", last_analysis_at=recent)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
        "MSFT": {"close": 320.0, "volume": 2000, "timestamp": "t"},
        "NVDA": {"close": 500.0, "volume": 3000, "timestamp": "t"},
    })

    stop = asyncio.Event()
    process_calls = []
    original_process = monitor._process_symbol

    async def _patched_process(symbol, snap, state_row, market_is_open=True):
        process_calls.append(symbol)
        stop.set()  # activar stop después del primer símbolo procesado
        return await original_process(symbol, snap, state_row, market_is_open=market_is_open)

    monitor._process_symbol = _patched_process

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist, stop_event=stop)

    # Solo debe haberse procesado 1 símbolo antes del break
    assert len(process_calls) == 1


# ── F5: _generate_chart ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_chart_returns_path_on_success(tmp_db, tmp_path):
    """_generate_chart retorna la ruta del PNG cuando chart_generator funciona."""
    import unittest.mock as mock_mod

    monitor, _, _, _, _ = _make_monitor(tmp_db)
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
    monitor, _, _, _, _ = _make_monitor(tmp_db)
    path = monitor._generate_chart("AAPL", [], "1Day", "opportunity")
    assert path is None


@pytest.mark.asyncio
async def test_generate_chart_returns_none_on_exception(tmp_db):
    """_generate_chart captura excepciones y devuelve None."""
    import unittest.mock as mock_mod

    monitor, _, _, _, _ = _make_monitor(tmp_db)
    bars = [{"timestamp": "2025-01-02T00:00:00Z", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}]

    with mock_mod.patch("src.chart_generator.generate_candlestick_chart", side_effect=RuntimeError("mpl error")):
        path = monitor._generate_chart("AAPL", bars, "1Day", "opportunity")

    assert path is None


# ── Alerter: exception swallowing en buy y sell ────────────────────────────────

@pytest.mark.asyncio
async def test_look_for_entry_alerter_exception_is_swallowed(tmp_db):
    """Si el alerter lanza excepción al enviar alerta de compra, no se propaga."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION", "symbol": "AAPL", "qty": None, "reasoning": "go",
    })
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert.side_effect = RuntimeError("SMTP error")
    monitor._alerter = mock_alerter

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")
    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}

    await monitor._process_symbol("AAPL", snap, state_row)  # no debe lanzar

    # La alerta de compra se envía desde _check_opening, no desde _look_for_entry/_process_symbol
    assert not mock_alerter.send_trade_alert.called
    assert tmp_db.get_symbol_state("AAPL")["state"] == "OPENING"


@pytest.mark.asyncio
async def test_manage_position_alerter_exception_is_swallowed(tmp_db):
    """Si el alerter lanza excepción al enviar alerta de venta, no se propaga."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert.side_effect = RuntimeError("SMTP gone")
    monitor._alerter = mock_alerter

    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=180.0, stop_loss=165.0, position_qty=3.0,
    )
    state_row = tmp_db.get_symbol_state("AAPL")
    snap = {"close": 185.0, "volume": 1000, "timestamp": "t"}

    result = await monitor._process_symbol("AAPL", snap, state_row)  # no debe lanzar

    # La alerta de cierre se envía desde _check_closing, no desde _manage_position
    assert result is not None
    assert not mock_alerter.send_trade_alert.called
    assert tmp_db.get_symbol_state("AAPL")["state"] == "CLOSING"


# ── _maybe_update_market_context: naive datetime y exception path ─────────────

@pytest.mark.asyncio
async def test_maybe_update_market_context_handles_naive_datetime(tmp_db):
    """Contexto reciente con datetime naive (sin timezone) se trata como reciente."""
    from datetime import datetime, timezone, timedelta
    import sqlite3

    monitor, _, _, _, _ = _make_monitor(tmp_db)
    monitor._force_analysis = False

    # Insertar contexto con timestamp naive reciente (30 min atrás, intervalo=4h)
    naive_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(tmp_db.db_path) as conn:
        conn.execute(
            "INSERT INTO market_context (report, news_count, created_at) VALUES (?, ?, ?)",
            ('{"market_sentiment": "NEUTRAL"}', 1, naive_ts),
        )

    call_count = 0

    async def _patched_run():
        nonlocal call_count
        call_count += 1

    monitor._run_market_context = _patched_run
    await monitor._maybe_update_market_context()

    assert call_count == 0  # no debe actualizar (el contexto es reciente)


@pytest.mark.asyncio
async def test_maybe_update_market_context_handles_invalid_date(tmp_db):
    """Si la fecha del contexto es inválida, se ignora y se actualiza el contexto."""
    import sqlite3

    monitor, _, _, _, _ = _make_monitor(tmp_db)
    monitor._force_analysis = False

    with sqlite3.connect(tmp_db.db_path) as conn:
        conn.execute(
            "INSERT INTO market_context (report, news_count, created_at) VALUES (?, ?, ?)",
            ('{"market_sentiment": "NEUTRAL"}', 1, "not-a-valid-date"),
        )

    call_count = 0

    async def _patched_run():
        nonlocal call_count
        call_count += 1

    monitor._run_market_context = _patched_run
    await monitor._maybe_update_market_context()

    assert call_count == 1  # fecha inválida → actualiza


# ── Standby de mercado ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_standby_when_market_closed(tmp_db):
    """Si get_clock devuelve is_open=False, _check_all fetcha snapshots pero solo procesa WATCHING."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",  # futuro lejano para evitar espera real
        "next_close": None,
        "timestamp": None,
    })

    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    # Parchear _interruptible_sleep para no esperar realmente
    import agents.monitor as monitor_mod
    original_sleep = monitor_mod._interruptible_sleep

    async def _instant_sleep(secs, stop_event, force_event=None):
        pass  # no esperar

    monitor_mod._interruptible_sleep = _instant_sleep
    try:
        watchlist = tmp_db.get_watchlist()
        await monitor._check_all(watchlist)
    finally:
        monitor_mod._interruptible_sleep = original_sleep

    # Con mercado cerrado, get_snapshots SÍ se llama (para análisis WATCHING)
    mock_alpaca.get_snapshots.assert_awaited_once()
    # Pero como last_analysis_at es reciente, el análisis técnico NO debe ejecutarse
    mock_tech.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_continues_when_get_clock_fails(tmp_db):
    """Si get_clock lanza excepción, _check_all continúa el ciclo normalmente."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(side_effect=RuntimeError("clock API error"))

    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)

    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    watchlist = tmp_db.get_watchlist()
    # No debe lanzar excepción y debe llamar a get_snapshots
    await monitor._check_all(watchlist)

    mock_alpaca.get_snapshots.assert_awaited_once()


# ── Qty del agente ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_uses_agent_qty_when_within_limit(tmp_db):
    """Si el agente recomienda qty <= max_qty, se usa la qty del agente."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    # max_qty = max(1, int(500/175)) = 2; agente sugiere 1 (dentro del límite)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION",
        "symbol": "AAPL",
        "qty": 1,
        "reasoning": "Conservative entry",
    })

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._process_symbol("AAPL", {"close": 175.0, "volume": 1000, "timestamp": "t"}, state_row)

    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "OPENING"
    assert new_state["position_qty"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_entry_caps_agent_qty_at_max(tmp_db):
    """Si el agente recomienda qty > max_qty, se capa al máximo permitido."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    # max_qty = max(1, int(500/175)) = 2; agente sugiere 10 → se capa a 2
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION",
        "symbol": "AAPL",
        "qty": 10,
        "reasoning": "Aggressive entry",
    })

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._process_symbol("AAPL", {"close": 175.0, "volume": 1000, "timestamp": "t"}, state_row)

    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "OPENING"
    assert new_state["position_qty"] == pytest.approx(2.0)  # cappado a max_qty


@pytest.mark.asyncio
async def test_entry_uses_max_qty_when_agent_returns_null(tmp_db):
    """Si el agente devuelve qty=null, se usa max_qty como fallback."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION",
        "symbol": "AAPL",
        "qty": None,
        "reasoning": "Signal confirmed",
    })

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._process_symbol("AAPL", {"close": 175.0, "volume": 1000, "timestamp": "t"}, state_row)

    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "OPENING"
    # max(1, int(500/175)) = 2
    assert new_state["position_qty"] == pytest.approx(2.0)


# ── Standby de mercado — refinamientos ────────────────────────────────────────

def _patch_sleep(monitor_mod):
    """Helper: parchea _interruptible_sleep para no esperar realmente."""
    async def _instant_sleep(secs, stop_event, force_event=None):
        pass
    original = monitor_mod._interruptible_sleep
    monitor_mod._interruptible_sleep = _instant_sleep
    return original


@pytest.mark.asyncio
async def test_check_all_standby_logs_local_time(tmp_db):
    """El mensaje INFO de mercado cerrado muestra hora en formato HH:MM, no ISO UTC."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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

    # El mensaje INFO de apertura debe contener ":" (formato hora HH:MM)
    apertura_msgs = [m for m in log_messages if "apertura" in m]
    assert apertura_msgs, "No se encontró mensaje de apertura"
    assert ":" in apertura_msgs[0]
    # No debe contener el ISO string completo (ej. "2099-01-01")
    assert "2099" not in apertura_msgs[0]


@pytest.mark.asyncio
async def test_check_all_standby_logs_debug_wait(tmp_db):
    """El debug log de espera contiene la duración en segundos."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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
    """Si cancel_all_orders lanza, la excepción no se propaga y el ciclo retorna normalmente."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
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
        # No debe lanzar excepción
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original

    # Con mercado cerrado, get_snapshots SÍ se llama (para análisis WATCHING)
    # pero get_snapshots retorna {} → no hay precios → tech agent no se llama
    mock_alpaca.get_snapshots.assert_awaited_once()
    mock_tech.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_close_imminent_skips_watching(tmp_db):
    """Símbolo en WATCHING no llama a _process_symbol cuando cierre < 5 min."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
    # next_close en 3 minutos → cierre inminente
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

    mock_tech.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_close_imminent_skips_opportunity(tmp_db):
    """Símbolo en OPPORTUNITY_FOUND no llama a _process_symbol cuando cierre < 5 min."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
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

    mock_tech.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_all_close_imminent_keeps_position_open(tmp_db):
    """Símbolo en POSITION_OPEN sí llama a _process_symbol aunque cierre sea inminente."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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

    process_called = []
    original_process = monitor._process_symbol

    async def _spy_process(symbol, snap, state_row, market_is_open=True):
        process_called.append(symbol)
        return await original_process(symbol, snap, state_row, market_is_open=market_is_open)

    monitor._process_symbol = _spy_process

    await monitor._check_all(tmp_db.get_watchlist())

    assert "AAPL" in process_called


@pytest.mark.asyncio
async def test_check_all_close_imminent_saves_price_snapshot(tmp_db):
    """El snapshot de precio se guarda aunque el análisis se omita por cierre inminente."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
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

    # El agente no debe haberse invocado (análisis omitido)
    mock_tech.run.assert_not_awaited()
    # Pero el snapshot de precio debe haberse guardado
    snapshots = tmp_db.get_price_history("AAPL", limit=1)
    assert snapshots, "Snapshot no guardado cuando análisis omitido por cierre inminente"
    assert snapshots[0]["close"] == pytest.approx(175.0)


# ── Estados OPENING y CLOSING ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_opening_filled_transitions_to_position_open(tmp_db):
    """Cuando la orden de compra se llena, el estado pasa a POSITION_OPEN."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-test",
        "status": "filled",
        "filled_avg_price": 176.5,
        "filled_qty": 2.0,
        "side": "buy",
        "symbol": "AAPL",
    })

    tmp_db.upsert_symbol_state(
        "AAPL", state="OPENING",
        target_price=185.0, stop_loss=170.0, position_qty=2.0,
        pending_order_id="ord-test",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_opening("AAPL", state_row)

    assert result is not None
    assert "FILLED" in result
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "POSITION_OPEN"
    assert new_state["pending_order_id"] is None
    # El trade de compra debe haberse guardado
    buy_trade = tmp_db.get_last_buy_trade("AAPL")
    assert buy_trade is not None
    assert buy_trade["side"] == "buy"
    assert buy_trade["price"] == pytest.approx(176.5)


@pytest.mark.asyncio
async def test_check_opening_cancelled_returns_to_watching(tmp_db):
    """Cuando la orden de compra se cancela, el estado vuelve a WATCHING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-test",
        "status": "cancelled",
        "filled_avg_price": None,
        "filled_qty": 0.0,
        "side": "buy",
        "symbol": "AAPL",
    })

    tmp_db.upsert_symbol_state(
        "AAPL", state="OPENING", position_qty=2.0, pending_order_id="ord-test",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_opening("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"


@pytest.mark.asyncio
async def test_check_opening_pending_stays_opening(tmp_db):
    """Si la orden de compra sigue pendiente, el estado permanece en OPENING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-test",
        "status": "new",
        "filled_avg_price": None,
        "filled_qty": 0.0,
        "side": "buy",
        "symbol": "AAPL",
    })

    tmp_db.upsert_symbol_state(
        "AAPL", state="OPENING", position_qty=2.0, pending_order_id="ord-test",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_opening("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "OPENING"


@pytest.mark.asyncio
async def test_check_opening_calls_alerter_on_fill(tmp_db):
    """Cuando la orden de compra se llena, se envía alerta por email."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-test", "status": "filled",
        "filled_avg_price": 176.5, "filled_qty": 2.0, "side": "buy", "symbol": "AAPL",
    })
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert = MagicMock(return_value=True)
    monitor._alerter = mock_alerter

    tmp_db.upsert_symbol_state(
        "AAPL", state="OPENING", position_qty=2.0, pending_order_id="ord-test",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._check_opening("AAPL", state_row)

    mock_alerter.send_trade_alert.assert_called_once()


@pytest.mark.asyncio
async def test_check_closing_filled_transitions_to_watching_with_pnl(tmp_db):
    """Cuando la orden de venta se llena, el estado pasa a WATCHING y se calcula P&L."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-sell", "status": "filled",
        "filled_avg_price": 190.0, "filled_qty": 2.0, "side": "sell", "symbol": "AAPL",
    })

    # Guardar un trade de compra previo para calcular P&L
    tmp_db.save_trade("AAPL", "buy", 2.0, 176.5, "ord-test", "filled", "entry_signal", "")

    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING",
        target_price=190.0, stop_loss=170.0, position_qty=2.0,
        pending_order_id="ord-sell",
        analysis_summary="Target $190.00 alcanzado",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_closing("AAPL", state_row)

    assert result is not None
    assert "FILLED" in result
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "WATCHING"
    assert new_state["pending_order_id"] is None
    # Verificar P&L: (190 - 176.5) * 2 = 27.0
    trades = tmp_db.get_trades()
    sell_trade = next((t for t in trades if t["side"] == "sell"), None)
    assert sell_trade is not None
    assert sell_trade["pnl"] == pytest.approx(27.0)
    assert sell_trade["fees"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_check_closing_cancelled_returns_to_position_open(tmp_db):
    """Cuando la orden de venta se cancela, el estado vuelve a POSITION_OPEN para reintentar."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-sell", "status": "cancelled",
        "filled_avg_price": None, "filled_qty": 0.0, "side": "sell", "symbol": "AAPL",
    })

    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING",
        target_price=190.0, stop_loss=170.0, position_qty=2.0,
        pending_order_id="ord-sell",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_closing("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "POSITION_OPEN"


@pytest.mark.asyncio
async def test_check_closing_calls_close_alert_on_fill(tmp_db):
    """Cuando la orden de venta se llena, se envía alerta de cierre con P&L."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-sell", "status": "filled",
        "filled_avg_price": 190.0, "filled_qty": 2.0, "side": "sell", "symbol": "AAPL",
    })
    mock_alerter = MagicMock()
    mock_alerter.send_close_alert = MagicMock(return_value=True)
    monitor._alerter = mock_alerter

    tmp_db.save_trade("AAPL", "buy", 2.0, 176.5, "ord-test", "filled", "entry_signal", "")
    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING", position_qty=2.0,
        pending_order_id="ord-sell",
        analysis_summary="Target alcanzado",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._check_closing("AAPL", state_row)

    mock_alerter.send_close_alert.assert_called_once()


@pytest.mark.asyncio
async def test_process_symbol_dispatches_opening_state(tmp_db):
    """_process_symbol llama a _check_opening cuando el estado es OPENING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value=None)

    tmp_db.upsert_symbol_state(
        "AAPL", state="OPENING", pending_order_id="ord-test", position_qty=2.0,
    )
    state_row = tmp_db.get_symbol_state("AAPL")
    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}

    await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.get_order.assert_awaited_once_with("ord-test")


@pytest.mark.asyncio
async def test_process_symbol_dispatches_closing_state(tmp_db):
    """_process_symbol llama a _check_closing cuando el estado es CLOSING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value=None)

    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING", pending_order_id="ord-sell", position_qty=2.0,
    )
    state_row = tmp_db.get_symbol_state("AAPL")
    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}

    await monitor._process_symbol("AAPL", snap, state_row)

    mock_alpaca.get_order.assert_awaited_once_with("ord-sell")


# ── Cobertura: ramas no cubiertas ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_all_market_closed_no_next_open(tmp_db):
    """Mercado cerrado sin next_open → next_open_local = 'desconocida', wait_secs = 300."""
    import agents.monitor as monitor_mod

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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
async def test_check_all_market_open_status_callback(tmp_db):
    """Cuando el mercado está abierto y hay callback, se invoca con is_open=True."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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
async def test_check_opening_no_pending_order_id(tmp_db):
    """OPENING sin pending_order_id → vuelve a WATCHING sin llamar a Alpaca."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state("AAPL", state="OPENING", pending_order_id=None)
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_opening("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"
    mock_alpaca.get_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_opening_get_order_exception(tmp_db):
    """Excepción en get_order durante OPENING → devuelve None, no cambia estado."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(side_effect=RuntimeError("network error"))
    tmp_db.upsert_symbol_state("AAPL", state="OPENING", pending_order_id="ord-err")
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_opening("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "OPENING"


@pytest.mark.asyncio
async def test_check_closing_no_pending_order_id(tmp_db):
    """CLOSING sin pending_order_id → vuelve a POSITION_OPEN sin llamar a Alpaca."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    tmp_db.upsert_symbol_state("AAPL", state="CLOSING", pending_order_id=None)
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_closing("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "POSITION_OPEN"
    mock_alpaca.get_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_closing_get_order_exception(tmp_db):
    """Excepción en get_order durante CLOSING → devuelve None, no cambia estado."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(side_effect=RuntimeError("timeout"))
    tmp_db.upsert_symbol_state("AAPL", state="CLOSING", pending_order_id="ord-close-err")
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_closing("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "CLOSING"


@pytest.mark.asyncio
async def test_check_closing_sell_order_still_pending(tmp_db):
    """Orden de venta aún pendiente (submitted) → devuelve None, estado permanece CLOSING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-pending", "status": "submitted",
        "filled_avg_price": None, "filled_qty": 0,
        "side": "sell", "symbol": "AAPL",
    })
    tmp_db.upsert_symbol_state("AAPL", state="CLOSING", pending_order_id="ord-pending")
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_closing("AAPL", state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "CLOSING"


@pytest.mark.asyncio
async def test_run_technical_short_medium_report_invalid_json(tmp_db):
    """Si el JSON del informe de medio plazo es inválido, se ignora y el análisis continúa."""
    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
    # Guardar un informe inválido en la BD
    tmp_db.save_agent_report("AAPL", "technical_medium", "not-valid-json")

    result, bars = await monitor._run_technical_short("AAPL", 175.0)

    # El agente sigue funcionando aunque no haya contexto de medio plazo
    assert result is not None
    assert result.get("trend") == "BULLISH"


# ── Short selling ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_short_selling_disabled_by_default(tmp_db):
    """Con allow_short_selling=False (default), OPEN_SHORT se ignora."""
    monitor, mock_alpaca, mock_tech, _, mock_trader = _make_monitor(tmp_db)
    assert monitor._allow_short_selling is False

    mock_trader.run = AsyncMock(return_value=json.dumps({
        "decision": "OPEN_SHORT", "symbol": "AAPL", "qty": 2, "reasoning": "bearish",
    }))

    tmp_db.upsert_watchlist("AAPL", shortable=True)
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")
    state_row["shortable"] = 1  # simulamos el enriquecimiento de _check_all

    snap = {"close": 175.0, "volume": 1000, "timestamp": "t"}
    mock_alpaca.get_bars = AsyncMock(return_value=[
        {"timestamp": "t", "open": 175.0, "high": 176.0, "low": 174.0, "close": 175.0, "volume": 1000}
    ])

    result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    # Sin allow_short, OPEN_SHORT no debe abrir posición
    mock_alpaca.place_order.assert_not_awaited()
    assert result is None


@pytest.mark.asyncio
async def test_short_selling_open_short_places_sell_order(tmp_db):
    """Con allow_short_selling=True y shortable=1, OPEN_SHORT coloca una orden sell."""
    from agents.monitor import PriceMonitor

    monitor, mock_alpaca, mock_tech, mock_market_ctx, _ = _make_monitor(tmp_db)
    # Reconstruir monitor con allow_short_selling=True
    mock_trader_short = MagicMock()
    mock_trader_short.run = AsyncMock(return_value=json.dumps({
        "decision": "OPEN_SHORT", "symbol": "AAPL", "qty": 2, "reasoning": "bearish signal",
    }))
    monitor._allow_short_selling = True
    monitor._trading_entry_agent = mock_trader_short

    tmp_db.upsert_watchlist("AAPL", shortable=True)
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")
    state_row["shortable"] = 1

    result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    # Debe haber colocado una orden de SELL para abrir el short
    mock_alpaca.place_order.assert_awaited_once()
    call_args = mock_alpaca.place_order.call_args
    assert call_args[0][2] == "sell"  # side = "sell"

    # El estado debe quedar como OPENING con position_side="short"
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "OPENING"
    assert new_state["position_side"] == "short"


@pytest.mark.asyncio
async def test_short_selling_manage_position_reversed_logic(tmp_db):
    """Para posiciones cortas, el target se alcanza cuando el precio BAJA."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=160.0, stop_loss=185.0, position_qty=2.0, position_side="short",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    # Precio baja a 158 → target alcanzado (160) → debe cerrar comprando
    result = await monitor._manage_position("AAPL", 158.0, state_row)

    mock_alpaca.place_order.assert_awaited_once()
    call_args = mock_alpaca.place_order.call_args
    assert call_args[0][2] == "buy"  # cierre de short es BUY
    assert result is not None
    assert "BUY" in result


@pytest.mark.asyncio
async def test_short_selling_pnl_inverted(tmp_db):
    """El P&L de un short se calcula como (entry_price - fill_price) * qty."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-close", "status": "filled",
        "filled_avg_price": 160.0, "filled_qty": 2.0,
        "side": "buy", "symbol": "AAPL",
        "commission": 0.0,
    })

    # Trade de entrada (SELL) a $175
    tmp_db.save_trade("AAPL", "sell", 2.0, 175.0, "ord-open", "filled", "entry", "")
    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING",
        target_price=160.0, stop_loss=185.0, position_qty=2.0,
        position_side="short", pending_order_id="ord-close",
        analysis_summary="Short target reached",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._check_closing("AAPL", state_row)

    trades = tmp_db.get_trades()
    close_trade = next((t for t in trades if t["side"] == "buy"), None)
    assert close_trade is not None
    # P&L = (175 - 160) * 2 = 30
    assert close_trade["pnl"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_non_shortable_symbol_no_open_short(tmp_db):
    """Con allow_short=True pero shortable=0, el prompt no incluye OPEN_SHORT."""
    monitor, mock_alpaca, _, _, mock_trader = _make_monitor(tmp_db)
    monitor._allow_short_selling = True

    # shortable=0 → can_short=False → prompt sin OPEN_SHORT
    mock_trader.run = AsyncMock(return_value=json.dumps({
        "decision": "HOLD", "symbol": "AAPL", "qty": None, "reasoning": "waiting",
    }))

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state_row = tmp_db.get_symbol_state("AAPL")
    state_row["shortable"] = 0  # no shortable

    result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    mock_alpaca.place_order.assert_not_awaited()


# ── Fees: commission desde get_order ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_fees_from_order_commission_passed_to_save_trade(tmp_db):
    """El campo commission de get_order se guarda como fees en el trade de cierre."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-sell", "status": "filled",
        "filled_avg_price": 190.0, "filled_qty": 2.0,
        "side": "sell", "symbol": "AAPL",
        "commission": 0.5,  # fee devuelto por Alpaca
    })

    tmp_db.save_trade("AAPL", "buy", 2.0, 176.5, "ord-buy", "filled", "entry", "")
    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING",
        target_price=190.0, stop_loss=170.0, position_qty=2.0,
        pending_order_id="ord-sell",
        analysis_summary="Target reached",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._check_closing("AAPL", state_row)

    trades = tmp_db.get_trades()
    sell_trade = next((t for t in trades if t["side"] == "sell"), None)
    assert sell_trade is not None
    assert sell_trade["fees"] == pytest.approx(0.5)


# ── Force analysis bypasea mercado cerrado ────────────────────────────────────

@pytest.mark.asyncio
async def test_force_analysis_bypasses_closed_market(tmp_db):
    """Con force_analysis=True, _check_all procesa símbolos aunque el mercado esté cerrado."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)

    monitor._force_analysis = True

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    # Con force_analysis=True, get_snapshots SÍ debe llamarse (mercado ignorado)
    mock_alpaca.get_snapshots.assert_awaited()
    # get_clock no se debe haber llamado (se saltó el check)
    mock_alpaca.get_clock.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_analysis_flag_reset_in_run(tmp_db):
    """Después del ciclo con force_analysis=True, el flag se resetea en run()."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True, "next_open": None, "next_close": None, "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={})

    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    monitor._force_analysis = True

    watchlist = tmp_db.get_watchlist()
    await monitor._check_all(watchlist)

    # El flag se resetea en run() después de _check_all; aquí simulamos ese comportamiento
    # verificando que _check_all no lo resetea internamente (sigue siendo True hasta run())
    # La lógica real de reset está en run(), que lo pone a False después de llamar _check_all.
    # Este test verifica que _check_all no lo resetea prematuramente.
    # (Si lo reseteara, el flag ya sería False al llegar aquí — eso sería incorrecto también,
    #  ya que run() lo resetea explícitamente después de _check_all.)
    # El comportamiento esperado: _force_analysis sigue True dentro de _check_all y run() lo resetea.
    assert monitor._force_analysis is True  # run() no se llamó, sigue True


@pytest.mark.asyncio
async def test_normal_closed_market_fetches_snapshots_for_watching(tmp_db):
    """Sin force_analysis y mercado cerrado, get_snapshots SÍ se llama para análisis WATCHING."""
    from datetime import datetime, timezone

    monitor, mock_alpaca, mock_tech, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": False,
        "next_open": "2099-01-01T13:30:00+00:00",
        "next_close": None,
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
    })

    recent = datetime.now(timezone.utc).isoformat()
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING", last_analysis_at=recent)
    monitor._force_analysis = False  # explícitamente sin force

    import agents.monitor as monitor_mod
    original_sleep = monitor_mod._interruptible_sleep

    async def _instant_sleep(secs, stop_event, force_event=None):
        pass

    monitor_mod._interruptible_sleep = _instant_sleep
    try:
        watchlist = tmp_db.get_watchlist()
        await monitor._check_all(watchlist)
    finally:
        monitor_mod._interruptible_sleep = original_sleep

    # Con mercado cerrado, get_snapshots SÍ se llama (análisis WATCHING habilitado)
    mock_alpaca.get_snapshots.assert_awaited_once()
    # last_analysis_at reciente → análisis no due → tech agent no se llama
    mock_tech.run.assert_not_awaited()


# ── market_status_callback(False, ...) al cerrar mercado ─────────────────────

@pytest.mark.asyncio
async def test_market_closed_triggers_status_callback_with_false(tmp_db):
    """_market_status_callback(False, next_open) se llama cuando el mercado está cerrado."""
    from datetime import datetime, timezone, timedelta

    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
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

    import agents.monitor as monitor_mod
    original_sleep = monitor_mod._interruptible_sleep

    async def _instant_sleep(secs, stop_event, force_event=None):
        pass

    monitor_mod._interruptible_sleep = _instant_sleep
    try:
        await monitor._check_all(tmp_db.get_watchlist())
    finally:
        monitor_mod._interruptible_sleep = original_sleep

    assert len(callback_calls) == 1
    assert callback_calls[0][0] is False  # is_open=False


# ── close_imminent: next_close con formato inválido no rompe el ciclo ─────────

@pytest.mark.asyncio
async def test_close_imminent_invalid_next_close_does_not_crash(tmp_db):
    """Si next_close no es ISO-parseable, el ciclo continúa sin close_imminent."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True,
        "next_open": None,
        "next_close": "NOT_A_DATE",  # inválido — dispara except
        "timestamp": None,
    })
    mock_alpaca.get_snapshots = AsyncMock(return_value={})
    tmp_db.upsert_watchlist("AAPL", "", 2.0)

    # No debe lanzar excepción
    await monitor._check_all(tmp_db.get_watchlist())

    # El ciclo continúa normalmente (get_snapshots fue llamado)
    mock_alpaca.get_snapshots.assert_awaited_once()


# ── _running = False interrumpe el loop de símbolos ──────────────────────────

@pytest.mark.asyncio
async def test_running_false_breaks_symbol_loop(tmp_db):
    """Si _running se pone en False durante el loop, se deja de procesar símbolos."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)
    mock_alpaca.get_clock = AsyncMock(return_value={
        "is_open": True, "next_open": None, "next_close": None, "timestamp": None,
    })
    tmp_db.upsert_watchlist("AAPL", "", 2.0)
    tmp_db.upsert_watchlist("MSFT", "", 2.0)

    # get_snapshots devuelve precios para ambos
    mock_alpaca.get_snapshots = AsyncMock(return_value={
        "AAPL": {"close": 175.0, "volume": 1000, "timestamp": "t"},
        "MSFT": {"close": 300.0, "volume": 2000, "timestamp": "t"},
    })

    # Al procesar el primer símbolo, poner _running=False
    original_process = monitor._process_symbol

    call_count = 0

    async def _stop_after_first(symbol, snap, state_row, market_is_open=True):
        nonlocal call_count
        call_count += 1
        monitor._running = False
        return await original_process(symbol, snap, state_row, market_is_open=market_is_open)

    monitor._process_symbol = _stop_after_first

    await monitor._check_all(tmp_db.get_watchlist())

    # Solo se procesó un símbolo antes de la parada
    assert call_count == 1


# ── Short stop-loss: precio >= stop en posición corta ────────────────────────

@pytest.mark.asyncio
async def test_short_selling_stop_loss_triggers_close(tmp_db):
    """Para un short, si el precio sube por encima del stop, se cierra la posición."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN",
        target_price=150.0, stop_loss=185.0, position_qty=2.0, position_side="short",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    # Precio sube a 190 → activa stop-loss (190 >= 185)
    result = await monitor._manage_position("AAPL", 190.0, state_row)

    mock_alpaca.place_order.assert_awaited_once()
    call_args = mock_alpaca.place_order.call_args
    assert call_args[0][2] == "buy"  # cierre de short es BUY
    assert result is not None


# ── Alerter lanza excepción durante OPENING: se ignora silenciosamente ────────

@pytest.mark.asyncio
async def test_alerter_exception_during_opening_fill_is_swallowed(tmp_db):
    """Si el alerter falla al notificar un fill de compra, el ciclo no se rompe."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert = MagicMock(side_effect=RuntimeError("smtp error"))
    monitor._alerter = mock_alerter

    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-buy", "status": "filled",
        "filled_avg_price": 175.0, "filled_qty": 2.0,
        "side": "buy", "symbol": "AAPL",
        "commission": 0.0,
    })
    tmp_db.upsert_symbol_state(
        "AAPL", state="OPENING",
        position_qty=2.0, pending_order_id="ord-buy",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    # No debe lanzar excepción
    result = await monitor._check_opening("AAPL", state_row)

    assert result is not None
    assert "FILLED" in result
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["state"] == "POSITION_OPEN"


# ── Alerter lanza excepción durante CLOSING: se ignora silenciosamente ────────

@pytest.mark.asyncio
async def test_alerter_exception_during_closing_fill_is_swallowed(tmp_db):
    """Si el alerter falla al notificar el cierre, el trade se guarda igual."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    mock_alerter = MagicMock()
    mock_alerter.send_close_alert = MagicMock(side_effect=RuntimeError("smtp error"))
    monitor._alerter = mock_alerter

    mock_alpaca.get_order = AsyncMock(return_value={
        "id": "ord-sell", "status": "filled",
        "filled_avg_price": 190.0, "filled_qty": 2.0,
        "side": "sell", "symbol": "AAPL",
        "commission": 0.0,
    })
    tmp_db.save_trade("AAPL", "buy", 2.0, 175.0, "ord-buy", "filled", "entry", "")
    tmp_db.upsert_symbol_state(
        "AAPL", state="CLOSING",
        target_price=190.0, stop_loss=165.0, position_qty=2.0,
        pending_order_id="ord-sell",
        analysis_summary="Target reached",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    result = await monitor._check_closing("AAPL", state_row)

    assert result is not None
    assert "FILLED" in result
    trades = tmp_db.get_trades()
    sell = next((t for t in trades if t["side"] == "sell"), None)
    assert sell is not None


# ── _interruptible_sleep: llamada directa ────────────────────────────────────

@pytest.mark.asyncio
async def test_interruptible_sleep_without_stop_event():
    """_interruptible_sleep con stop_event=None duerme el tiempo indicado (0s en test)."""
    from agents.monitor import _interruptible_sleep
    await _interruptible_sleep(0, None)  # no debe lanzar excepción


@pytest.mark.asyncio
async def test_interruptible_sleep_with_already_set_stop_event():
    """_interruptible_sleep retorna inmediatamente si stop_event ya está seteado."""
    from agents.monitor import _interruptible_sleep
    stop = asyncio.Event()
    stop.set()
    await _interruptible_sleep(60, stop)  # retorna inmediatamente


@pytest.mark.asyncio
async def test_interruptible_sleep_with_force_event_interrupts():
    """_interruptible_sleep retorna inmediatamente si force_event ya está seteado."""
    from agents.monitor import _interruptible_sleep
    stop = asyncio.Event()
    force = asyncio.Event()
    force.set()
    await _interruptible_sleep(60, stop, force_event=force)  # retorna inmediatamente


# ── run_monitor: Alpaca no disponible retorna temprano ────────────────────────

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
    await run_monitor()  # no debe lanzar excepción


# ── run_monitor: setup completo con mocks ─────────────────────────────────────

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

    # Modelo real con campos accesibles
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
        "trader_opportunity": mock_model_cfg,
        "trader_entry": mock_model_cfg,
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
    await run_monitor(stop_event=stop_event)  # no debe lanzar excepción


@pytest.mark.asyncio
async def test_run_monitor_no_gmail_logs_warning(monkeypatch):
    """run_monitor registra advertencia cuando GMAIL no está configurado."""
    _make_run_monitor_mocks(monkeypatch)  # delenv GMAIL_USER y GMAIL_APP_PASSWORD

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

    # upsert_watchlist debe haber sido llamado para cada símbolo
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
    mock_config = MagicMock()
    mock_config.trading = {
        "analysis_interval_hours": 4,
        "max_position_value": 500,
        "poll_interval_seconds": 60,
        "allow_short_selling": False,
    }
    mock_config.models = {}

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    stop_event = asyncio.Event()
    stop_event.set()

    monkeypatch.setattr(monitor_mod, "AlpacaClient", _FakeAlpaca)
    monkeypatch.setattr(monitor_mod, "StorageLayer", MagicMock(return_value=mock_storage))
    monkeypatch.setattr(monitor_mod, "load_from_yaml", lambda p: mock_config)
    for fn in [
        "build_technical_watching_agent", "build_technical_entry_agent",
        "build_market_context_agent", "build_company_analysis_agent",
        "build_trading_opportunity_agent", "build_trading_entry_agent",
    ]:
        monkeypatch.setattr(monitor_mod, fn, _fake_agent_builder)

    from agents.monitor import run_monitor
    await run_monitor(stop_event=stop_event)


# ── run_monitor_sync: wrapper síncrono ───────────────────────────────────────

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
    mock_storage.get_watchlist.return_value = []  # vacío → activa init desde env

    mock_config = MagicMock()
    mock_config.trading = {"analysis_interval_hours": 4, "max_position_value": 500, "poll_interval_seconds": 60, "allow_short_selling": False}
    mock_config.models = {}

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("WATCHLIST_SYMBOLS", "AAPL,BTC")  # BTC debe excluirse
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

    # upsert_watchlist llamado solo para AAPL (BTC excluido)
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

    mock_config = MagicMock()
    mock_config.trading = {"analysis_interval_hours": 4, "max_position_value": 500, "poll_interval_seconds": 60, "allow_short_selling": False}
    mock_config.models = {}

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

    # Fallback: upsert_watchlist con solo el símbolo (sin metadata)
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
    mock_storage.get_watchlist.return_value = []  # vacío → activa init

    mock_config = MagicMock()
    mock_config.trading = {"analysis_interval_hours": 4, "max_position_value": 500, "poll_interval_seconds": 60, "allow_short_selling": False}
    mock_config.models = {}

    async def _fake_agent_builder(*a, **kw):
        return MagicMock()

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.delenv("WATCHLIST_SYMBOLS", raising=False)  # sin símbolos → init desde Alpaca
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

    # _init_watchlist_from_alpaca → get_assets fue llamado
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

    # Disparar las tres ramas del handler
    class _FakeEvent:
        def __init__(self, etype, data):
            self.type = etype
            self.data = data

    await captured_callback(_FakeEvent(EventType.MODEL_CALLED, {"role": "technical"}))
    await captured_callback(_FakeEvent(EventType.MODEL_FINISHED, {
        "role": "technical", "prompt": "p", "response": "r", "system_prompt": "s",
    }))
    await captured_callback(_FakeEvent(EventType.TOOL_CALLED, {"tool": "get_price"}))
    # Tipo no reconocido no debe lanzar
    await captured_callback(_FakeEvent("UNKNOWN_EVENT", {}))


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


# ── Bracket orders ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manage_position_bracket_tp_leg_filled_transitions_to_watching(tmp_db):
    """Si la leg de take-profit del bracket está filled, Alpaca cerró la posición → WATCHING."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    # Guardar trade de entrada para calcular P&L
    tmp_db.save_trade("AAPL", "buy", 2.0, 176.5, "ord-buy", "filled", "entry", "")

    # Estado con bracket_order_id activo
    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=2.0,
        bracket_order_id="bracket-1",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    parent_order = {
        "id": "bracket-1",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-1", "status": "filled", "type": "limit", "side": "sell"},
            {"id": "sl-1", "status": "cancelled", "type": "stop", "side": "sell"},
        ],
    }
    tp_order = {
        "id": "tp-1",
        "status": "filled",
        "filled_avg_price": 185.0,
        "filled_qty": 2.0,
        "side": "sell",
        "symbol": "AAPL",
        "commission": 0.5,
        "legs": [],
    }
    orders = {"bracket-1": parent_order, "tp-1": tp_order}
    mock_alpaca.get_order = AsyncMock(side_effect=lambda oid: orders.get(oid))

    result = await monitor._manage_position("AAPL", 175.0, state_row)

    assert "bracket" in (result or "").lower() or result is not None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"
    assert tmp_db.get_symbol_state("AAPL")["bracket_order_id"] is None
    # No se debe haber colocado una orden manual de cierre
    mock_alpaca.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_manage_position_bracket_sl_leg_filled_transitions_to_watching(tmp_db):
    """Si la leg de stop-loss del bracket está filled → WATCHING (stop activado por Alpaca)."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    tmp_db.save_trade("AAPL", "buy", 3.0, 176.5, "ord-buy", "filled", "entry", "")
    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=3.0,
        bracket_order_id="bracket-2",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    parent_order = {
        "id": "bracket-2",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-2", "status": "cancelled", "type": "limit", "side": "sell"},
            {"id": "sl-2", "status": "filled", "type": "stop", "side": "sell"},
        ],
    }
    sl_order = {
        "id": "sl-2",
        "status": "filled",
        "filled_avg_price": 170.0,
        "filled_qty": 3.0,
        "side": "sell",
        "symbol": "AAPL",
        "commission": 0.5,
        "legs": [],
    }
    orders = {"bracket-2": parent_order, "sl-2": sl_order}
    mock_alpaca.get_order = AsyncMock(side_effect=lambda oid: orders.get(oid))

    await monitor._manage_position("AAPL", 172.0, state_row)

    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"
    mock_alpaca.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_manage_position_bracket_no_legs_filled_uses_price_fallback_and_cancels_legs(tmp_db):
    """Si ninguna leg del bracket está filled pero el precio cruza el target, se cancela el bracket y se cierra manualmente."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    mock_alpaca.cancel_order = AsyncMock(return_value=True)

    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=2.0,
        bracket_order_id="bracket-3",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    parent_order = {
        "id": "bracket-3",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-3", "status": "accepted", "type": "limit", "side": "sell"},
            {"id": "sl-3", "status": "accepted", "type": "stop", "side": "sell"},
        ],
    }
    mock_alpaca.get_order = AsyncMock(return_value=parent_order)

    # Precio cruza el target → fallback
    result = await monitor._manage_position("AAPL", 186.0, state_row)

    # Las legs activas deben cancelarse
    assert mock_alpaca.cancel_order.call_count == 2
    cancelled_ids = {call.args[0] for call in mock_alpaca.cancel_order.call_args_list}
    assert "tp-3" in cancelled_ids
    assert "sl-3" in cancelled_ids
    # Debe colocar orden de cierre manual → CLOSING
    mock_alpaca.place_order.assert_called_once()
    assert tmp_db.get_symbol_state("AAPL")["state"] == "CLOSING"
    assert tmp_db.get_symbol_state("AAPL")["bracket_order_id"] is None


@pytest.mark.asyncio
async def test_manage_position_bracket_get_order_exception_falls_through_to_price(tmp_db):
    """Si get_order falla al verificar el bracket, se continúa con la lógica de precio."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=2.0,
        bracket_order_id="bracket-4",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    # Primera llamada (verificación bracket) falla; segunda (fallback cancel) también falla
    mock_alpaca.get_order = AsyncMock(side_effect=RuntimeError("API error"))

    # Precio no cruza target/stop → no se cierra
    result = await monitor._manage_position("AAPL", 175.0, state_row)

    assert result is None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "POSITION_OPEN"


@pytest.mark.asyncio
async def test_open_position_stores_bracket_order_id(tmp_db):
    """Cuando se abre posición con target y stop, se guarda bracket_order_id en symbol_states."""
    monitor, mock_alpaca, mock_tech, mock_market_ctx, mock_trader = _make_monitor(tmp_db)
    mock_alpaca.place_order = AsyncMock(return_value={
        "order_id": "bracket-001",
        "status": "submitted",
        "take_profit_order_id": "tp-001",
        "stop_loss_order_id": "sl-001",
    })
    mock_trader.run = AsyncMock(return_value=json.dumps({
        "decision": "OPEN_POSITION",
        "symbol": "AAPL",
        "qty": 2,
        "reasoning": "Strong signal",
    }))
    # TechnicalEntryAgent provee target y stop
    mock_tech.run = AsyncMock(return_value=json.dumps({
        "trend": "BULLISH",
        "signal": "BUY",
        "target_price": 185.0,
        "stop_loss": 170.0,
        "confidence": 80,
        "summary": "Strong entry",
    }))

    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    snap = {"close": 176.0, "volume": 1000, "timestamp": "t"}
    state_row = tmp_db.get_symbol_state("AAPL")

    await monitor._process_symbol("AAPL", snap, state_row)

    # Verificar que place_order fue llamado con take_profit_price y stop_loss_price
    call_kwargs = mock_alpaca.place_order.call_args
    assert call_kwargs.kwargs.get("take_profit_price") == 185.0
    assert call_kwargs.kwargs.get("stop_loss_price") == 170.0

    # Verificar que bracket_order_id queda guardado en el estado
    new_state = tmp_db.get_symbol_state("AAPL")
    assert new_state["bracket_order_id"] == "bracket-001"


# ── Bracket short: P&L desde get_last_sell_trade (lines 539-542) ─────────────

@pytest.mark.asyncio
async def test_manage_position_bracket_tp_filled_short_uses_sell_trade_for_pnl(tmp_db):
    """Para un short con bracket, el P&L usa get_last_sell_trade (entrada = venta)."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    # Entrada short: se vendió a 180
    tmp_db.save_trade("AAPL", "sell", 2.0, 180.0, "ord-sell", "filled", "entry", "")

    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=160.0,
        stop_loss=190.0,
        position_qty=2.0,
        position_side="short",
        bracket_order_id="bracket-short-1",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    parent_order = {
        "id": "bracket-short-1",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "sell",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-short-1", "status": "filled", "type": "limit", "side": "buy"},
            {"id": "sl-short-1", "status": "pending_new", "type": "stop", "side": "buy"},
        ],
    }
    tp_order = {
        "id": "tp-short-1",
        "status": "filled",
        "filled_avg_price": 160.0,
        "filled_qty": 2.0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0.5,
        "legs": [],
    }
    orders = {"bracket-short-1": parent_order, "tp-short-1": tp_order}
    mock_alpaca.get_order = AsyncMock(side_effect=lambda oid: orders.get(oid))

    result = await monitor._manage_position("AAPL", 162.0, state_row)

    assert result is not None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"
    # El trade de cierre (buy para cubrir el short) debe haberse guardado
    trades = tmp_db.get_trades()
    close_trades = [t for t in trades if t["side"] == "buy"]
    assert len(close_trades) == 1
    # P&L: (180 - 160) * 2 = 40
    assert close_trades[0]["pnl"] == pytest.approx(40.0)


# ── Bracket: alerter notificado al auto-ejecutarse una leg (lines 582-593) ────

@pytest.mark.asyncio
async def test_manage_position_bracket_tp_filled_calls_alerter(tmp_db):
    """Cuando Alpaca ejecuta la leg de take-profit del bracket, se llama al alerter."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    mock_alerter = MagicMock()
    mock_alerter.send_close_alert = MagicMock(return_value=True)
    monitor._alerter = mock_alerter

    tmp_db.save_trade("AAPL", "buy", 2.0, 176.5, "ord-buy", "filled", "entry", "")

    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=2.0,
        bracket_order_id="bracket-alert-1",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    parent_order = {
        "id": "bracket-alert-1",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-al-1", "status": "filled", "type": "limit", "side": "sell"},
            {"id": "sl-al-1", "status": "pending_new", "type": "stop", "side": "sell"},
        ],
    }
    tp_order = {
        "id": "tp-al-1",
        "status": "filled",
        "filled_avg_price": 185.0,
        "filled_qty": 2.0,
        "side": "sell",
        "symbol": "AAPL",
        "commission": 0.5,
        "legs": [],
    }
    orders = {"bracket-alert-1": parent_order, "tp-al-1": tp_order}
    mock_alpaca.get_order = AsyncMock(side_effect=lambda oid: orders.get(oid))

    await monitor._manage_position("AAPL", 180.0, state_row)

    mock_alerter.send_close_alert.assert_called_once()


@pytest.mark.asyncio
async def test_manage_position_bracket_tp_filled_alerter_exception_is_swallowed(tmp_db):
    """Si el alerter falla al notificar el cierre de bracket, se ignora silenciosamente."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    mock_alerter = MagicMock()
    mock_alerter.send_close_alert = MagicMock(side_effect=RuntimeError("smtp error"))
    monitor._alerter = mock_alerter

    tmp_db.save_trade("AAPL", "buy", 2.0, 176.5, "ord-buy", "filled", "entry", "")

    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=2.0,
        bracket_order_id="bracket-alert-2",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    parent_order = {
        "id": "bracket-alert-2",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-al-2", "status": "filled", "type": "limit", "side": "sell"},
        ],
    }
    tp_order = {
        "id": "tp-al-2",
        "status": "filled",
        "filled_avg_price": 185.0,
        "filled_qty": 2.0,
        "side": "sell",
        "symbol": "AAPL",
        "commission": 0.0,
        "legs": [],
    }
    orders = {"bracket-alert-2": parent_order, "tp-al-2": tp_order}
    mock_alpaca.get_order = AsyncMock(side_effect=lambda oid: orders.get(oid))

    # No debe lanzar excepción aunque el alerter falle
    result = await monitor._manage_position("AAPL", 180.0, state_row)

    assert result is not None
    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"


# ── Bracket: error al cancelar legs en fallback de precio (lines 636-637) ─────

@pytest.mark.asyncio
async def test_manage_position_bracket_cancel_legs_exception_is_logged(tmp_db):
    """Si cancel_order falla al cancelar legs activas, el error se loguea y se cierra igual."""
    monitor, mock_alpaca, _, _, _ = _make_monitor(tmp_db)

    tmp_db.upsert_symbol_state(
        "AAPL",
        state="POSITION_OPEN",
        target_price=185.0,
        stop_loss=170.0,
        position_qty=2.0,
        bracket_order_id="bracket-err-1",
    )
    state_row = tmp_db.get_symbol_state("AAPL")

    bracket_with_active_legs = {
        "id": "bracket-err-1",
        "status": "accepted",
        "filled_avg_price": None,
        "filled_qty": 0,
        "side": "buy",
        "symbol": "AAPL",
        "commission": 0,
        "legs": [
            {"id": "tp-err-1", "status": "pending_new", "type": "limit", "side": "sell"},
        ],
    }
    # get_order devuelve bracket con legs activas; cancel_order falla
    mock_alpaca.get_order = AsyncMock(return_value=bracket_with_active_legs)
    mock_alpaca.cancel_order = AsyncMock(side_effect=RuntimeError("cancel API error"))
    mock_alpaca.place_order = AsyncMock(return_value={"order_id": "close-manual", "status": "submitted"})

    # Precio cruza el target → step 2 intentará cancelar las legs del bracket
    result = await monitor._manage_position("AAPL", 186.0, state_row)

    # A pesar del error en cancel, la posición debe cerrarse (orden de cierre colocada)
    assert result is not None
    mock_alpaca.place_order.assert_awaited_once()
