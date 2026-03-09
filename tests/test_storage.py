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
