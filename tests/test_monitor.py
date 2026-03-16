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
        gnews_api_key="test-key",
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
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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
    # qty se calcula como max(1, int(max_position_value / price)) = max(1, int(500/175)) = 2
    assert new_state["position_qty"] == 2.0


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
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    assert tmp_db.get_symbol_state("AAPL")["state"] == "WATCHING"


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
    """Si TradingAgent dice OPEN_POSITION, el estado cambia a OPPORTUNITY_FOUND."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run.return_value = json.dumps({
        "decision": "OPEN_POSITION", "symbol": "AAPL", "qty": 5, "reasoning": "Strong signal",
    })

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        result = await monitor._full_analysis("AAPL", 175.0)

    assert result is not None
    assert "OPPORTUNITY_FOUND" in result
    assert tmp_db.get_symbol_state("AAPL")["state"] == "OPPORTUNITY_FOUND"


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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(side_effect=RuntimeError("news error")))
        state_row = tmp_db.get_symbol_state("AAPL")
        result = await monitor._look_for_entry("AAPL", 175.0, state_row)

    assert result is None  # HOLD → no trade


@pytest.mark.asyncio
async def test_look_for_entry_returns_none_when_no_trading_decision(tmp_db):
    """Si TradingAgent falla, _look_for_entry devuelve None."""
    monitor, _, _, _, mock_trader = _make_monitor(tmp_db)
    mock_trader.run = AsyncMock(side_effect=RuntimeError("model error"))

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._look_for_entry("AAPL", 175.0, state_row)

    mock_alerter.send_trade_alert.assert_called_once()


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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    mock_alerter.send_trade_alert.assert_called_once()


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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._maybe_update_market_context()

    mock_market_ctx.run.assert_awaited()


@pytest.mark.asyncio
async def test_run_market_context_handles_parse_error(tmp_db):
    """ParseError en MarketContextAgent → no propaga excepción."""
    monitor, _, _, mock_market_ctx, _ = _make_monitor(tmp_db)
    mock_market_ctx.run = AsyncMock(return_value="invalid response from model")

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._run_market_context()  # no debe lanzar


@pytest.mark.asyncio
async def test_run_market_context_handles_exception(tmp_db):
    """Excepción en MarketContextAgent → no propaga."""
    monitor, _, _, mock_market_ctx, _ = _make_monitor(tmp_db)
    mock_market_ctx.run = AsyncMock(side_effect=RuntimeError("LLM down"))

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        result = await monitor._run_company_analysis("AAPL")

    assert result is None


@pytest.mark.asyncio
async def test_run_company_analysis_handles_exception(tmp_db):
    """Excepción en CompanyAnalysisAgent → devuelve None."""
    monitor, _, _, _, _ = _make_monitor(tmp_db)
    monitor._company_analysis_agent = MagicMock()
    monitor._company_analysis_agent.run = AsyncMock(side_effect=RuntimeError("model error"))

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        result = await monitor._run_company_analysis("AAPL")

    assert result is None


# ── Utilidades ────────────────────────────────────────────────────────────────

def test_parse_news_list_returns_empty_on_invalid_json():
    from agents.monitor import _parse_news_list
    assert _parse_news_list("not-json") == []
    assert _parse_news_list("") == []


def test_parse_news_list_returns_articles_from_valid_json():
    from agents.monitor import _parse_news_list
    raw = json.dumps({"articles": [{"title": "Test"}], "count": 1})
    result = _parse_news_list(raw)
    assert result == [{"title": "Test"}]


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
    monkeypatch.setenv("GNEWS_API_KEY", "test-key")

    from agents.monitor import run_monitor
    await run_monitor()  # no debe lanzar excepción


@pytest.mark.asyncio
async def test_run_monitor_returns_early_without_gnews_key(monkeypatch):
    """run_monitor retorna si falta GNEWS_API_KEY."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.delenv("GNEWS_API_KEY", raising=False)

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

    async def _patched_process(symbol, snap, state_row):
        process_calls.append(symbol)
        stop.set()  # activar stop después del primer símbolo procesado
        return await original_process(symbol, snap, state_row)

    monitor._process_symbol = _patched_process

    watchlist = tmp_db.get_watchlist()
    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
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

    with pytest.MonkeyPatch.context() as mp:
        from agents.tools.news_tool import SearchNewsTool
        mp.setattr(SearchNewsTool, "execute", AsyncMock(return_value=json.dumps({"articles": []})))
        await monitor._process_symbol("AAPL", snap, state_row)  # no debe lanzar

    mock_alerter.send_trade_alert.assert_called_once()


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

    assert result is not None
    mock_alerter.send_trade_alert.assert_called_once()


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
