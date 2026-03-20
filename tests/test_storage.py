"""
Tests de StorageLayer.
No requieren credenciales externas.
"""
import pytest
from datetime import datetime, timezone


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_article(title="Apple earnings beat", url="https://example.com/1", date="2024-01-15"):
    from src.ingestion import Article
    return Article(
        source="Reuters",
        title=title,
        description="Strong iPhone sales",
        content="Apple Inc. reported...",
        url=url,
        published_at=datetime.fromisoformat(f"{date}T10:00:00+00:00"),
    )


# ── Articles: check_and_mark, save_analysis, get_all, delete_by_url, reset ────

def test_content_hash_is_deterministic(tmp_db):
    from src.storage import StorageLayer
    a1 = _make_article(title="Same Title", date="2024-01-15")
    a2 = _make_article(title="Same Title", url="https://other.com", date="2024-01-15")
    h1 = StorageLayer._content_hash(a1)
    h2 = StorageLayer._content_hash(a2)
    assert h1 == h2  # mismo título + misma fecha → mismo hash


def test_content_hash_differs_on_different_date(tmp_db):
    from src.storage import StorageLayer
    a1 = _make_article(title="Same Title", date="2024-01-15")
    a2 = _make_article(title="Same Title", url="https://other.com", date="2024-01-16")
    assert StorageLayer._content_hash(a1) != StorageLayer._content_hash(a2)


def test_check_and_mark_new_article_is_new(tmp_db):
    article = _make_article()
    result = tmp_db.check_and_mark(article)
    assert result.is_new is True
    assert result.is_analyzed is False
    assert result.article is article


def test_check_and_mark_duplicate_returns_is_new_false(tmp_db):
    article = _make_article()
    tmp_db.check_and_mark(article)
    # Mismo título + fecha → duplicado
    duplicate = _make_article(url="https://example.com/other")
    result = tmp_db.check_and_mark(duplicate)
    assert result.is_new is False


def test_check_and_mark_analyzed_article_returns_is_analyzed_true(tmp_db):
    article = _make_article()
    tmp_db.check_and_mark(article)
    tmp_db.save_analysis(
        article,
        llm_output={"company": "Apple", "ticker": "AAPL", "impact_level": "HIGH",
                    "impact_type": "POSITIVE", "confidence": 85, "reason": "Earnings beat"},
        alert_result={"sent": True},
    )
    # Volver a marcar el mismo artículo
    duplicate = _make_article(url="https://example.com/other")
    result = tmp_db.check_and_mark(duplicate)
    assert result.is_new is False
    assert result.is_analyzed is True


def test_save_analysis_updates_article_fields(tmp_db):
    article = _make_article()
    tmp_db.check_and_mark(article)
    tmp_db.save_analysis(
        article,
        llm_output={"company": "Apple", "ticker": "AAPL", "impact_level": "HIGH",
                    "impact_type": "POSITIVE", "confidence": 85, "reason": "Earnings beat"},
        alert_result={"sent": True},
    )
    rows = tmp_db.get_all()
    assert len(rows) == 1
    row = rows[0]
    assert row["company"] == "Apple"
    assert row["ticker"] == "AAPL"
    assert row["impact_level"] == "HIGH"
    assert row["confidence"] == 85
    assert row["alert_sent"] == 1


def test_save_analysis_alert_not_sent(tmp_db):
    article = _make_article()
    tmp_db.check_and_mark(article)
    tmp_db.save_analysis(article, llm_output={}, alert_result={"sent": False})
    rows = tmp_db.get_all()
    assert rows[0]["alert_sent"] == 0


def test_get_all_returns_articles_ordered_by_created_at(tmp_db):
    for i in range(3):
        a = _make_article(title=f"Article {i}", url=f"https://example.com/{i}")
        tmp_db.check_and_mark(a)
    rows = tmp_db.get_all()
    assert len(rows) == 3


def test_get_all_empty_when_no_articles(tmp_db):
    assert tmp_db.get_all() == []


def test_delete_by_url_removes_article(tmp_db):
    article = _make_article()
    tmp_db.check_and_mark(article)
    assert len(tmp_db.get_all()) == 1

    tmp_db.delete_by_url(article.url)
    assert tmp_db.get_all() == []


def test_delete_by_url_noop_on_unknown_url(tmp_db):
    article = _make_article()
    tmp_db.check_and_mark(article)
    tmp_db.delete_by_url("https://nonexistent.com/article")
    assert len(tmp_db.get_all()) == 1  # no tocó el artículo existente


def test_reset_clears_only_articles(tmp_db):
    """reset() borra solo articles, no otras tablas."""
    article = _make_article()
    tmp_db.check_and_mark(article)
    tmp_db.upsert_watchlist("AAPL", "Apple", 2.0)

    tmp_db.reset()

    assert tmp_db.get_all() == []
    assert len(tmp_db.get_watchlist()) == 1  # watchlist intacta


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


def test_save_trade_with_pnl_and_fees(tmp_db):
    """save_trade acepta pnl y fees y los persiste correctamente."""
    tmp_db.save_trade(
        symbol="AAPL", side="sell", qty=2, price=190.0,
        order_id="ord-sell", status="filled",
        trigger="Target alcanzado", reason="Target alcanzado",
        pnl=27.0, fees=0.0,
    )

    trades = tmp_db.get_trades()
    t = trades[0]
    assert t["pnl"] == pytest.approx(27.0)
    assert t["fees"] == pytest.approx(0.0)


def test_get_last_buy_trade_returns_most_recent(tmp_db):
    """get_last_buy_trade devuelve el último trade de compra para el símbolo."""
    tmp_db.save_trade("AAPL", "buy", 5, 175.0, "ord-001", "filled", "entry_signal", "")
    tmp_db.save_trade("AAPL", "buy", 3, 180.0, "ord-002", "filled", "entry_signal", "")

    result = tmp_db.get_last_buy_trade("AAPL")

    assert result is not None
    assert result["side"] == "buy"
    assert result["price"] == pytest.approx(180.0)


def test_get_last_buy_trade_returns_none_when_no_buys(tmp_db):
    """get_last_buy_trade devuelve None si no hay trades de compra."""
    tmp_db.save_trade("AAPL", "sell", 5, 185.0, "ord-sell", "filled", "manual", "")

    result = tmp_db.get_last_buy_trade("AAPL")
    assert result is None


def test_get_last_buy_trade_none_for_unknown_symbol(tmp_db):
    """get_last_buy_trade devuelve None para símbolo sin trades."""
    result = tmp_db.get_last_buy_trade("UNKNOWN")
    assert result is None


def test_upsert_symbol_state_stores_pending_order_id(tmp_db):
    """upsert_symbol_state acepta y persiste pending_order_id."""
    tmp_db.upsert_symbol_state("AAPL", state="OPENING", pending_order_id="ord-123")

    state = tmp_db.get_symbol_state("AAPL")
    assert state["pending_order_id"] == "ord-123"


def test_upsert_symbol_state_clears_pending_order_id(tmp_db):
    """Se puede limpiar pending_order_id asignando None."""
    tmp_db.upsert_symbol_state("AAPL", state="OPENING", pending_order_id="ord-123")
    tmp_db.upsert_symbol_state("AAPL", state="POSITION_OPEN", pending_order_id=None)

    state = tmp_db.get_symbol_state("AAPL")
    assert state["state"] == "POSITION_OPEN"
    assert state["pending_order_id"] is None


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


# ── Market context ─────────────────────────────────────────────────────────────

def test_market_context_returns_none_when_empty(tmp_db):
    assert tmp_db.get_latest_market_context() is None


def test_market_context_save_and_get_latest(tmp_db):
    report = '{"market_sentiment": "BULLISH", "confidence": 80}'
    tmp_db.save_market_context(report, news_count=5)

    result = tmp_db.get_latest_market_context()
    assert result is not None
    assert result["report"] == report
    assert result["news_count"] == 5
    assert "created_at" in result


def test_market_context_get_latest_returns_most_recent(tmp_db):
    tmp_db.save_market_context('{"market_sentiment": "BEARISH"}', news_count=3)
    tmp_db.save_market_context('{"market_sentiment": "BULLISH"}', news_count=7)

    result = tmp_db.get_latest_market_context()
    assert '"BULLISH"' in result["report"]
    assert result["news_count"] == 7


def test_market_context_history_limit(tmp_db):
    for i in range(5):
        tmp_db.save_market_context(f'{{"run": {i}}}', news_count=i)

    history = tmp_db.get_market_context_history(limit=3)
    assert len(history) == 3


def test_market_context_history_ordered_by_recent(tmp_db):
    tmp_db.save_market_context('{"first": true}', news_count=1)
    tmp_db.save_market_context('{"second": true}', news_count=2)

    history = tmp_db.get_market_context_history(limit=10)
    assert history[0]["news_count"] == 2  # más reciente primero
    assert history[1]["news_count"] == 1


def test_market_context_history_empty_when_no_reports(tmp_db):
    assert tmp_db.get_market_context_history() == []


# ── reset_all ─────────────────────────────────────────────────────────────────

def test_reset_all_clears_all_tables(tmp_db):
    """reset_all() vacía las 7 tablas."""
    tmp_db.upsert_watchlist("AAPL", "Apple", 2.0)
    tmp_db.save_price_snapshot("AAPL", 175.0, 1000, "2024-01-01T10:00:00Z")
    tmp_db.save_trade("AAPL", "buy", 1, 175.0, "ord-1", "filled", "test", "test")
    tmp_db.upsert_symbol_state("AAPL", state="WATCHING")
    tmp_db.save_agent_report("AAPL", "technical", '{"trend": "BULLISH"}')
    tmp_db.save_market_context('{"sentiment": "BULLISH"}', news_count=3)

    tmp_db.reset_all()

    assert tmp_db.get_watchlist() == []
    assert tmp_db.get_last_price("AAPL") is None
    assert tmp_db.get_trades() == []
    assert tmp_db.get_all_symbol_states() == []
    assert tmp_db.get_agent_reports("AAPL") == []
    assert tmp_db.get_latest_market_context() is None


def test_reset_all_schema_intact_after_reset(tmp_db):
    """Después de reset_all() se pueden seguir insertando datos."""
    tmp_db.reset_all()

    tmp_db.upsert_watchlist("MSFT", "Microsoft", 1.5)
    assert len(tmp_db.get_watchlist()) == 1

    tmp_db.save_market_context('{"ok": true}', news_count=0)
    assert tmp_db.get_latest_market_context() is not None


def test_reset_all_idempotent_on_empty_db(tmp_db):
    """reset_all() en una BD vacía no lanza excepción."""
    tmp_db.reset_all()
    tmp_db.reset_all()  # segunda vez sobre BD ya vacía
    assert tmp_db.get_watchlist() == []


def test_storage_migrations_idempotent(tmp_path):
    """Crear StorageLayer dos veces sobre la misma BD no falla (migraciones idempotentes)."""
    from src.storage import StorageLayer
    db_path = str(tmp_path / "test.db")
    # Primera creación aplica las migraciones
    StorageLayer(db_path)
    # Segunda creación intenta ALTER TABLE en columnas ya existentes → except silenciado
    s2 = StorageLayer(db_path)
    # La BD sigue siendo funcional
    s2.upsert_watchlist("AAPL", "Apple", 2.0)
    assert len(s2.get_watchlist()) == 1


# ── Short selling: position_side y get_last_sell_trade ────────────────────────

def test_upsert_symbol_state_position_side_long(tmp_db):
    """Insertar un estado con position_side='long' se persiste correctamente."""
    tmp_db.upsert_symbol_state("AAPL", state="POSITION_OPEN", position_side="long")
    row = tmp_db.get_symbol_state("AAPL")
    assert row["position_side"] == "long"


def test_upsert_symbol_state_position_side_short(tmp_db):
    """Insertar un estado con position_side='short' se persiste correctamente."""
    tmp_db.upsert_symbol_state("AAPL", state="POSITION_OPEN", position_side="short")
    row = tmp_db.get_symbol_state("AAPL")
    assert row["position_side"] == "short"


def test_get_symbol_state_default_position_side(tmp_db):
    """El estado por defecto para un símbolo nuevo incluye position_side='long'."""
    row = tmp_db.get_symbol_state("NEWSTK")
    assert row["position_side"] == "long"


def test_get_last_sell_trade_returns_most_recent(tmp_db):
    """get_last_sell_trade devuelve el último trade de venta."""
    tmp_db.save_trade("AAPL", "sell", 5.0, 190.0, "ord-1", "filled", "entry", "")
    tmp_db.save_trade("AAPL", "sell", 3.0, 195.0, "ord-2", "filled", "entry", "")

    result = tmp_db.get_last_sell_trade("AAPL")
    assert result is not None
    assert result["price"] == pytest.approx(195.0)
    assert result["qty"] == pytest.approx(3.0)


def test_get_last_sell_trade_returns_none_when_no_sells(tmp_db):
    """get_last_sell_trade devuelve None si no hay trades de venta."""
    tmp_db.save_trade("AAPL", "buy", 5.0, 175.0, "ord-1", "filled", "entry", "")
    result = tmp_db.get_last_sell_trade("AAPL")
    assert result is None


# ── save_article / get_articles_by_scope / expire_old_articles (Fase 3) ────────

def test_save_article_returns_true_on_new(tmp_db):
    """save_article retorna True al insertar un artículo nuevo."""
    article = _make_article(url="https://example.com/new-1")
    result = tmp_db.save_article(article)
    assert result is True


def test_save_article_returns_false_on_duplicate_url(tmp_db):
    """save_article retorna False si la URL ya existe."""
    article = _make_article(url="https://example.com/dup-1")
    tmp_db.save_article(article)
    result = tmp_db.save_article(article)
    assert result is False


def test_save_article_returns_false_on_duplicate_content_hash(tmp_db):
    """save_article retorna False si el content_hash ya existe (mismo título+fecha, diferente URL)."""
    a1 = _make_article(title="Same Title", url="https://example.com/hash-1", date="2024-01-15")
    a2 = _make_article(title="Same Title", url="https://example.com/hash-2", date="2024-01-15")
    tmp_db.save_article(a1)
    result = tmp_db.save_article(a2)
    assert result is False


def test_save_article_with_classification_persists_scope(tmp_db):
    """save_article almacena los campos de clasificación correctamente."""
    article = _make_article(url="https://example.com/classified-1")
    classification = {
        "scope": "company", "ticker": "AAPL", "exchange": None,
        "impact": "HIGH", "reasoning": "Apple earnings beat.",
    }
    tmp_db.save_article(article, classification)

    rows = tmp_db.get_articles_by_scope("company", ticker="AAPL")
    assert len(rows) == 1
    assert rows[0]["scope"] == "company"
    assert rows[0]["ticker"] == "AAPL"
    assert rows[0]["impact"] == "HIGH"
    assert rows[0]["reasoning"] == "Apple earnings beat."


def test_save_article_expires_at_high_is_7_days(tmp_db):
    """Artículos con impact=HIGH tienen expires_at en 7 días."""
    from datetime import timedelta
    article = _make_article(url="https://example.com/high-1")
    classification = {"scope": "company", "ticker": "AAPL", "exchange": None, "impact": "HIGH", "reasoning": ""}
    now_before = datetime.now(timezone.utc)
    tmp_db.save_article(article, classification)
    now_after = datetime.now(timezone.utc)

    rows = tmp_db.get_articles_by_scope("company", ticker="AAPL")
    expires_at = datetime.fromisoformat(rows[0]["expires_at"])
    assert (now_before + timedelta(days=7)) <= expires_at <= (now_after + timedelta(days=7))


def test_save_article_expires_at_medium_is_3_days(tmp_db):
    """Artículos con impact=MEDIUM tienen expires_at en 3 días."""
    from datetime import timedelta
    article = _make_article(url="https://example.com/medium-1")
    classification = {"scope": "market", "ticker": None, "exchange": None, "impact": "MEDIUM", "reasoning": ""}
    now_before = datetime.now(timezone.utc)
    tmp_db.save_article(article, classification)
    now_after = datetime.now(timezone.utc)

    rows = tmp_db.get_articles_by_scope("market")
    expires_at = datetime.fromisoformat(rows[0]["expires_at"])
    assert (now_before + timedelta(days=3)) <= expires_at <= (now_after + timedelta(days=3))


def test_save_article_expires_at_low_is_1_day(tmp_db):
    """Artículos con impact=LOW tienen expires_at en 1 día."""
    from datetime import timedelta
    article = _make_article(url="https://example.com/low-1")
    classification = {"scope": "market", "ticker": None, "exchange": None, "impact": "LOW", "reasoning": ""}
    now_before = datetime.now(timezone.utc)
    tmp_db.save_article(article, classification)
    now_after = datetime.now(timezone.utc)

    rows = tmp_db.get_articles_by_scope("market")
    expires_at = datetime.fromisoformat(rows[0]["expires_at"])
    assert (now_before + timedelta(days=1)) <= expires_at <= (now_after + timedelta(days=1))


def test_get_articles_by_scope_company_filters_by_ticker(tmp_db):
    """get_articles_by_scope filtra correctamente por ticker."""
    a_aapl = _make_article(url="https://example.com/aapl-1")
    a_msft = _make_article(url="https://example.com/msft-1")
    cls_aapl = {"scope": "company", "ticker": "AAPL", "exchange": None, "impact": "HIGH", "reasoning": ""}
    cls_msft = {"scope": "company", "ticker": "MSFT", "exchange": None, "impact": "MEDIUM", "reasoning": ""}
    tmp_db.save_article(a_aapl, cls_aapl)
    tmp_db.save_article(a_msft, cls_msft)

    rows = tmp_db.get_articles_by_scope("company", ticker="AAPL")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"


def test_get_articles_by_scope_exchange(tmp_db):
    """get_articles_by_scope filtra correctamente por exchange."""
    article = _make_article(url="https://example.com/nasdaq-1")
    classification = {"scope": "exchange", "ticker": None, "exchange": "NASDAQ", "impact": "HIGH", "reasoning": ""}
    tmp_db.save_article(article, classification)

    rows = tmp_db.get_articles_by_scope("exchange", exchange="NASDAQ")
    assert len(rows) == 1
    assert rows[0]["exchange_affected"] == "NASDAQ"


def test_get_articles_by_scope_filters_expired(tmp_db):
    """get_articles_by_scope no devuelve artículos cuyo expires_at ya pasó."""
    import sqlite3 as _sqlite3
    from datetime import timedelta
    article = _make_article(url="https://example.com/expired-1")
    tmp_db.save_article(article, {"scope": "market", "ticker": None, "exchange": None, "impact": "LOW", "reasoning": ""})

    # Forzar expires_at en el pasado
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _sqlite3.connect(tmp_db.db_path) as conn:
        conn.execute("UPDATE articles SET expires_at = ? WHERE url = ?", (past, article.url))

    rows = tmp_db.get_articles_by_scope("market")
    assert len(rows) == 0


def test_expire_old_articles_deletes_past_expires(tmp_db):
    """expire_old_articles borra artículos expirados y retorna el conteo."""
    import sqlite3 as _sqlite3
    from datetime import timedelta
    a1 = _make_article(title="Expiring article", url="https://example.com/exp-a", date="2024-01-15")
    a2 = _make_article(title="Surviving article", url="https://example.com/exp-b", date="2024-01-16")
    tmp_db.save_article(a1, {"scope": "market", "ticker": None, "exchange": None, "impact": "LOW", "reasoning": ""})
    tmp_db.save_article(a2, {"scope": "market", "ticker": None, "exchange": None, "impact": "HIGH", "reasoning": ""})

    # Forzar solo a1 en el pasado
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _sqlite3.connect(tmp_db.db_path) as conn:
        conn.execute("UPDATE articles SET expires_at = ? WHERE url = ?", (past, a1.url))

    deleted = tmp_db.expire_old_articles()
    assert deleted == 1

    remaining = tmp_db.get_articles_by_scope("market")
    assert len(remaining) == 1
    assert remaining[0]["url"] == a2.url
