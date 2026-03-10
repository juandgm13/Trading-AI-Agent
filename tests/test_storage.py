"""
Tests de StorageLayer — nuevas tablas: watchlist, price_snapshots, trades.
No requieren credenciales externas.
"""
import pytest
from datetime import datetime, timezone


# ── Watchlist ─────────────────────────────────────────────────────────────────

def test_watchlist_insert_and_read(tmp_db):
    tmp_db.upsert_watchlist("AAPL", "Apple Inc.", 2.0)
    tmp_db.upsert_watchlist("MSFT", "Microsoft", 1.5)

    rows = tmp_db.get_watchlist()
    symbols = [r["symbol"] for r in rows]

    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert len(rows) == 2


def test_watchlist_upsert_updates_existing(tmp_db):
    tmp_db.upsert_watchlist("AAPL", "Apple", 2.0)
    tmp_db.upsert_watchlist("AAPL", "Apple Inc.", 3.5)  # actualizar

    rows = tmp_db.get_watchlist()
    assert len(rows) == 1
    assert rows[0]["threshold_pct"] == 3.5
    assert rows[0]["company"] == "Apple Inc."


def test_watchlist_deactivate(tmp_db):
    tmp_db.upsert_watchlist("AAPL", "Apple", 2.0)
    tmp_db.deactivate_watchlist("AAPL")

    rows = tmp_db.get_watchlist()
    assert len(rows) == 0  # get_watchlist solo devuelve activos


def test_watchlist_symbols_uppercased(tmp_db):
    tmp_db.upsert_watchlist("aapl", "Apple", 2.0)
    rows = tmp_db.get_watchlist()
    assert rows[0]["symbol"] == "AAPL"


# ── Price snapshots ───────────────────────────────────────────────────────────

def test_save_and_get_last_price(tmp_db):
    tmp_db.save_price_snapshot("AAPL", 175.0, 1_000_000, "2024-01-01T10:00:00Z")
    assert tmp_db.get_last_price("AAPL") == pytest.approx(175.0)


def test_get_last_price_returns_most_recent(tmp_db):
    tmp_db.save_price_snapshot("AAPL", 175.0, 500_000, "2024-01-01T10:00:00Z")
    tmp_db.save_price_snapshot("AAPL", 178.5, 600_000, "2024-01-01T10:01:00Z")

    # El segundo se inserta después → recorded_at más reciente
    assert tmp_db.get_last_price("AAPL") == pytest.approx(178.5)


def test_get_last_price_none_for_unknown_symbol(tmp_db):
    assert tmp_db.get_last_price("UNKNOWN") is None


def test_get_price_history(tmp_db):
    for i in range(5):
        tmp_db.save_price_snapshot("MSFT", 300.0 + i, 100_000, f"2024-01-01T10:0{i}:00Z")

    history = tmp_db.get_price_history("MSFT", limit=10)
    assert len(history) == 5
    assert all(r["symbol"] == "MSFT" for r in history)


def test_price_snapshot_different_symbols(tmp_db):
    tmp_db.save_price_snapshot("AAPL", 175.0, None, "2024-01-01T10:00:00Z")
    tmp_db.save_price_snapshot("MSFT", 320.0, None, "2024-01-01T10:00:00Z")

    assert tmp_db.get_last_price("AAPL") == pytest.approx(175.0)
    assert tmp_db.get_last_price("MSFT") == pytest.approx(320.0)


# ── Trades ────────────────────────────────────────────────────────────────────

def test_save_and_get_trades(tmp_db):
    tmp_db.save_trade(
        symbol="AAPL", side="buy", qty=5, price=175.5,
        order_id="ord-001", status="filled",
        trigger="price_change", reason="Strong earnings beat",
    )

    trades = tmp_db.get_trades()
    assert len(trades) == 1
    t = trades[0]
    assert t["symbol"] == "AAPL"
    assert t["side"] == "buy"
    assert t["qty"] == pytest.approx(5)
    assert t["price"] == pytest.approx(175.5)
    assert t["order_id"] == "ord-001"
    assert t["status"] == "filled"
    assert t["reason"] == "Strong earnings beat"


def test_get_trades_limit(tmp_db):
    for i in range(10):
        tmp_db.save_trade(
            symbol="MSFT", side="buy", qty=1, price=300.0 + i,
            order_id=f"ord-{i:03d}", status="filled",
            trigger="test", reason="Test trade",
        )

    trades = tmp_db.get_trades(limit=5)
    assert len(trades) == 5


def test_trade_without_order_id(tmp_db):
    tmp_db.save_trade(
        symbol="NVDA", side="sell", qty=2, price=None,
        order_id=None, status="submitted",
        trigger="manual", reason="Taking profits",
    )

    trades = tmp_db.get_trades()
    assert trades[0]["order_id"] is None
    assert trades[0]["price"] is None


# ── Symbol states ──────────────────────────────────────────────────────────────

def test_symbol_state_default_is_watching(tmp_db):
    """Un símbolo sin estado devuelve WATCHING por defecto."""
    state = tmp_db.get_symbol_state("AAPL")
    assert state["state"] == "WATCHING"
    assert state["symbol"] == "AAPL"
    assert state["last_analysis_at"] is None


def test_symbol_state_upsert_creates_new(tmp_db):
    """upsert_symbol_state crea una nueva fila si no existe."""
    tmp_db.upsert_symbol_state("AAPL", state="OPPORTUNITY_FOUND")
    state = tmp_db.get_symbol_state("AAPL")
    assert state["state"] == "OPPORTUNITY_FOUND"


def test_symbol_state_upsert_updates_existing(tmp_db):
    """upsert_symbol_state actualiza campos específicos sin tocar los demás."""
    tmp_db.upsert_symbol_state("MSFT", state="WATCHING", last_analysis_at="2024-01-01T00:00:00Z")
    tmp_db.upsert_symbol_state("MSFT", state="OPPORTUNITY_FOUND")

    state = tmp_db.get_symbol_state("MSFT")
    assert state["state"] == "OPPORTUNITY_FOUND"
    assert state["last_analysis_at"] == "2024-01-01T00:00:00Z"  # no se tocó


def test_symbol_state_position_open_fields(tmp_db):
    """Campos de POSITION_OPEN se guardan correctamente."""
    tmp_db.upsert_symbol_state(
        "NVDA",
        state="POSITION_OPEN",
        target_price=550.0,
        stop_loss=480.0,
        position_qty=3.0,
        position_open_at="2024-06-01T10:00:00Z",
    )
    state = tmp_db.get_symbol_state("NVDA")
    assert state["state"] == "POSITION_OPEN"
    assert state["target_price"] == pytest.approx(550.0)
    assert state["stop_loss"] == pytest.approx(480.0)
    assert state["position_qty"] == pytest.approx(3.0)


def test_symbol_state_reset_to_watching(tmp_db):
    """Se puede resetear POSITION_OPEN → WATCHING limpiando campos de posición."""
    tmp_db.upsert_symbol_state(
        "AAPL", state="POSITION_OPEN", target_price=200.0, stop_loss=160.0, position_qty=5.0
    )
    tmp_db.upsert_symbol_state(
        "AAPL", state="WATCHING", target_price=None, stop_loss=None, position_qty=None
    )
    state = tmp_db.get_symbol_state("AAPL")
    assert state["state"] == "WATCHING"
    assert state["target_price"] is None
    assert state["position_qty"] is None


def test_get_all_symbol_states(tmp_db):
    """get_all_symbol_states devuelve todos los símbolos con estado."""
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING")
    tmp_db.upsert_symbol_state("MSFT", state="OPPORTUNITY_FOUND")
    tmp_db.upsert_symbol_state("NVDA", state="POSITION_OPEN")

    states = tmp_db.get_all_symbol_states()
    symbols = {s["symbol"] for s in states}
    assert symbols == {"AAPL", "MSFT", "NVDA"}


# ── Agent reports ──────────────────────────────────────────────────────────────

def test_save_and_get_agent_report(tmp_db):
    """save_agent_report persiste el informe y get_agent_reports lo recupera."""
    tmp_db.save_agent_report("AAPL", "technical_medium", '{"trend": "BULLISH"}')

    reports = tmp_db.get_agent_reports("AAPL")
    assert len(reports) == 1
    assert reports[0]["symbol"] == "AAPL"
    assert reports[0]["agent_name"] == "technical_medium"
    assert '"BULLISH"' in reports[0]["report"]


def test_agent_reports_multiple_agents(tmp_db):
    """Se pueden guardar informes de múltiples agentes para el mismo símbolo."""
    tmp_db.save_agent_report("MSFT", "technical_medium", '{"trend": "NEUTRAL"}')
    tmp_db.save_agent_report("MSFT", "economics", '{"sentiment": "POSITIVE"}')
    tmp_db.save_agent_report("MSFT", "trading_eval", '{"decision": "HOLD"}')

    reports = tmp_db.get_agent_reports("MSFT")
    agent_names = {r["agent_name"] for r in reports}
    assert agent_names == {"technical_medium", "economics", "trading_eval"}


def test_agent_reports_limit(tmp_db):
    """get_agent_reports respeta el limit."""
    for i in range(10):
        tmp_db.save_agent_report("AAPL", "technical_medium", f'{{"run": {i}}}')

    reports = tmp_db.get_agent_reports("AAPL", limit=5)
    assert len(reports) == 5


def test_agent_reports_empty_for_unknown_symbol(tmp_db):
    """No hay informes para un símbolo desconocido."""
    reports = tmp_db.get_agent_reports("UNKNOWN")
    assert reports == []
