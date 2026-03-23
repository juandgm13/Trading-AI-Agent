import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from .ingestion import Article


_RETENTION_DAYS: dict[str | None, int] = {"HIGH": 7, "MEDIUM": 3, "LOW": 1, None: 1}


@dataclass
class DeduplicationResult:
    is_new: bool
    is_analyzed: bool
    article: Article


class StorageLayer:
    def __init__(self, db_path: str = "trading_agent.db"):
        self.db_path = db_path
        self._init_db()

    @staticmethod
    def _content_hash(article: Article) -> str:
        # Deduplica por contenido: mismo título + misma fecha de publicación
        # (ignora la hora exacta para tolerar pequeñas variaciones entre sindicados)
        key = f"{article.title.strip().lower()}|{article.published_at.date().isoformat()}"
        return hashlib.sha256(key.encode()).hexdigest()

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    url               TEXT PRIMARY KEY,
                    content_hash      TEXT UNIQUE,
                    title             TEXT,
                    description       TEXT,
                    source            TEXT,
                    published_at      TEXT,
                    feed_group        TEXT,
                    scope             TEXT,
                    ticker            TEXT,
                    exchange_affected TEXT,
                    impact            TEXT,
                    reasoning         TEXT,
                    expires_at        TEXT,
                    -- Legacy columns (conservadas para compatibilidad)
                    company           TEXT,
                    impact_level      TEXT,
                    impact_type       TEXT,
                    confidence        INTEGER,
                    reason            TEXT,
                    alert_sent        INTEGER DEFAULT 0,
                    created_at        TEXT
                )
            """)
            # Migraciones — añadir columnas nuevas si no existen
            _migrations = [
                "ALTER TABLE symbol_states ADD COLUMN pending_order_id TEXT",
                "ALTER TABLE trades ADD COLUMN pnl REAL",
                "ALTER TABLE trades ADD COLUMN fees REAL",
                "ALTER TABLE symbol_states ADD COLUMN position_side TEXT DEFAULT 'long'",
                # Fase 3: nuevas columnas de clasificación de artículos
                "ALTER TABLE articles ADD COLUMN description TEXT",
                "ALTER TABLE articles ADD COLUMN feed_group TEXT",
                "ALTER TABLE articles ADD COLUMN scope TEXT",
                "ALTER TABLE articles ADD COLUMN exchange_affected TEXT",
                "ALTER TABLE articles ADD COLUMN impact TEXT",
                "ALTER TABLE articles ADD COLUMN reasoning TEXT",
                "ALTER TABLE articles ADD COLUMN expires_at TEXT",
            ]
            for _sql in _migrations:
                try:
                    conn.execute(_sql)
                except Exception:
                    pass  # columna ya existe
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_articles_scope
                ON articles(scope, ticker, exchange_affected)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_articles_expires
                ON articles(expires_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_snapshots (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT NOT NULL,
                    close       REAL NOT NULL,
                    volume      INTEGER,
                    bar_time    TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol     TEXT NOT NULL,
                    side       TEXT NOT NULL,
                    qty        REAL NOT NULL,
                    price      REAL,
                    order_id   TEXT,
                    status     TEXT,
                    trigger    TEXT,
                    reason     TEXT,
                    pnl        REAL,
                    fees       REAL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol        TEXT PRIMARY KEY,
                    company       TEXT,
                    threshold_pct REAL DEFAULT 2.0,
                    active        INTEGER DEFAULT 1,
                    exchange      TEXT,
                    marginable    INTEGER DEFAULT 0,
                    shortable     INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_states (
                    symbol           TEXT PRIMARY KEY,
                    state            TEXT NOT NULL DEFAULT 'WATCHING',
                    last_analysis_at TEXT,
                    analysis_summary TEXT,
                    entry_signal_at  TEXT,
                    target_price     REAL,
                    stop_loss        REAL,
                    position_qty     REAL,
                    position_open_at TEXT,
                    chart_path       TEXT,
                    pending_order_id TEXT,
                    position_side    TEXT DEFAULT 'long',
                    updated_at       TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_reports (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol      TEXT NOT NULL,
                    agent_name  TEXT NOT NULL,
                    report      TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_context (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    report      TEXT NOT NULL,
                    news_count  INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT NOT NULL
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def check_and_mark(self, article: Article) -> DeduplicationResult:
        content_hash = self._content_hash(article)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT impact_level FROM articles WHERE content_hash = ?", (content_hash,)
            ).fetchone()

            if row:
                is_analyzed = row[0] is not None
                return DeduplicationResult(is_new=False, is_analyzed=is_analyzed, article=article)

            conn.execute(
                """
                INSERT INTO articles (url, content_hash, title, source, published_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    article.url,
                    content_hash,
                    article.title,
                    article.source,
                    article.published_at.isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        return DeduplicationResult(is_new=True, is_analyzed=False, article=article)

    def save_analysis(self, article: Article, llm_output: dict, alert_result: dict):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE articles
                SET company      = ?,
                    ticker       = ?,
                    impact_level = ?,
                    impact_type  = ?,
                    confidence   = ?,
                    reason       = ?,
                    alert_sent   = ?
                WHERE url = ?
                """,
                (
                    llm_output.get("company"),
                    llm_output.get("ticker"),
                    llm_output.get("impact_level"),
                    llm_output.get("impact_type"),
                    llm_output.get("confidence"),
                    llm_output.get("reason"),
                    1 if alert_result.get("sent") else 0,
                    article.url,
                ),
            )

    def get_all(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM articles ORDER BY created_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_by_url(self, url: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM articles WHERE url = ?", (url,))

    def reset(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM articles")

    def get_classified_article_urls(self, urls: list[str]) -> set[str]:
        """Devuelve el subconjunto de URLs que ya están clasificadas (scope IS NOT NULL)."""
        if not urls:
            return set()
        placeholders = ",".join("?" * len(urls))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT url FROM articles WHERE url IN ({placeholders}) AND scope IS NOT NULL",
                urls,
            ).fetchall()
        return {row[0] for row in rows}

    def save_article(self, article: Article, classification: dict | None = None) -> bool:
        """
        Inserta un artículo nuevo con sus datos de clasificación.

        Retorna True si es nuevo, False si ya existía (deduplicación por url).
        Si classification es None, el artículo queda sin clasificar (scope=NULL).
        expires_at se calcula según impact: HIGH=7d, MEDIUM=3d, LOW/None=1d.
        """
        content_hash = self._content_hash(article)
        now = datetime.now(timezone.utc)

        scope = classification.get("scope") if classification else None
        ticker = classification.get("ticker") if classification else None
        exchange_affected = classification.get("exchange") if classification else None
        impact = classification.get("impact") if classification else None
        reasoning = classification.get("reasoning") if classification else None

        retention_days = _RETENTION_DAYS.get(impact, 1)
        expires_at = (now + timedelta(days=retention_days)).isoformat()

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO articles
                        (url, content_hash, title, description, source, published_at,
                         feed_group, scope, ticker, exchange_affected, impact, reasoning,
                         expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article.url,
                        content_hash,
                        article.title,
                        article.description,
                        article.source,
                        article.published_at.isoformat(),
                        article.feed_group,
                        scope,
                        ticker,
                        exchange_affected,
                        impact,
                        reasoning,
                        expires_at,
                        now.isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            # url o content_hash duplicado — artículo ya existe
            return False

    def get_articles_by_scope(
        self,
        scope: str,
        ticker: str | None = None,
        exchange: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Devuelve artículos clasificados no expirados filtrados por scope.

        scope="company" + ticker="AAPL" → noticias de empresa.
        scope="exchange" + exchange="NASDAQ" → noticias de exchange.
        scope="market" → noticias macro.
        Filtra WHERE expires_at > NOW() OR expires_at IS NULL.
        """
        now = datetime.now(timezone.utc).isoformat()
        params: list = [scope]
        where_extra = ""

        if ticker:
            where_extra += " AND ticker = ?"
            params.append(ticker.upper())
        if exchange:
            where_extra += " AND exchange_affected = ?"
            params.append(exchange.upper())

        params.extend([now, limit])

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT * FROM articles
                WHERE scope = ?{where_extra}
                  AND (expires_at > ? OR expires_at IS NULL)
                ORDER BY published_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def expire_old_articles(self) -> int:
        """Elimina artículos cuyo expires_at ya pasó. Retorna el número de filas borradas."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM articles WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            return cursor.rowcount

    def reset_all(self):
        """Borra todos los datos de todas las tablas (watchlist incluida)."""
        tables = [
            "articles",
            "price_snapshots",
            "trades",
            "watchlist",
            "symbol_states",
            "agent_reports",
            "market_context",
        ]
        with self._connect() as conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table}")

    # ── Price snapshots ───────────────────────────────────────────────────────

    def save_price_snapshot(self, symbol: str, close: float, volume: int | None, bar_time: str):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO price_snapshots (symbol, close, volume, bar_time, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, close, volume, bar_time, datetime.now(timezone.utc).isoformat()),
            )

    def get_last_price(self, symbol: str) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT close FROM price_snapshots WHERE symbol = ? ORDER BY recorded_at DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        return row[0] if row else None

    def get_price_history(self, symbol: str, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM price_snapshots WHERE symbol = ? ORDER BY recorded_at DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Trades ────────────────────────────────────────────────────────────────

    def save_trade(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float | None,
        order_id: str | None,
        status: str,
        trigger: str,
        reason: str,
        pnl: float | None = None,
        fees: float | None = None,
    ):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (symbol, side, qty, price, order_id, status, trigger, reason, pnl, fees, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, side, qty, price, order_id, status, trigger, reason, pnl, fees,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_last_buy_trade(self, symbol: str) -> dict | None:
        """Devuelve el último trade de compra para el símbolo, o None si no existe."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? AND side = 'buy' ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        return dict(row) if row else None

    def get_last_sell_trade(self, symbol: str) -> dict | None:
        """Devuelve el último trade de venta para el símbolo, o None si no existe.
        Usado para calcular P&L en posiciones cortas (la entrada es un SELL)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM trades WHERE symbol = ? AND side = 'sell' ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        return dict(row) if row else None

    def get_trades(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Watchlist ─────────────────────────────────────────────────────────────

    def get_watchlist(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE active = 1 ORDER BY symbol"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_watchlist(
        self,
        symbol: str,
        company: str = "",
        threshold_pct: float = 2.0,
        exchange: str = "",
        marginable: bool = False,
        shortable: bool = False,
    ):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist (symbol, company, threshold_pct, active, exchange, marginable, shortable)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    company       = excluded.company,
                    threshold_pct = excluded.threshold_pct,
                    active        = 1,
                    exchange      = excluded.exchange,
                    marginable    = excluded.marginable,
                    shortable     = excluded.shortable
                """,
                (symbol.upper(), company, threshold_pct, exchange, int(marginable), int(shortable)),
            )

    def deactivate_watchlist(self, symbol: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE watchlist SET active = 0 WHERE symbol = ?", (symbol.upper(),)
            )

    # ── Symbol states (máquina de estados por símbolo) ────────────────────────

    def get_symbol_state(self, symbol: str) -> dict:
        """Devuelve la fila de estado para el símbolo, o un estado por defecto (WATCHING)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM symbol_states WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
        if row:
            return dict(row)
        return {
            "symbol": symbol.upper(),
            "state": "WATCHING",
            "last_analysis_at": None,
            "analysis_summary": None,
            "entry_signal_at": None,
            "target_price": None,
            "stop_loss": None,
            "position_qty": None,
            "position_open_at": None,
            "chart_path": None,
            "pending_order_id": None,
            "position_side": "long",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def upsert_symbol_state(self, symbol: str, **kwargs):
        """Crea o actualiza el estado de un símbolo. Los kwargs sobreescriben campos individuales."""
        symbol = symbol.upper()
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM symbol_states WHERE symbol = ?", (symbol,)
            ).fetchone()

            if existing is None:
                # Insertar con defaults
                conn.execute(
                    """
                    INSERT INTO symbol_states
                        (symbol, state, last_analysis_at, analysis_summary,
                         entry_signal_at, target_price, stop_loss,
                         position_qty, position_open_at, chart_path, pending_order_id,
                         position_side, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol,
                        kwargs.get("state", "WATCHING"),
                        kwargs.get("last_analysis_at"),
                        kwargs.get("analysis_summary"),
                        kwargs.get("entry_signal_at"),
                        kwargs.get("target_price"),
                        kwargs.get("stop_loss"),
                        kwargs.get("position_qty"),
                        kwargs.get("position_open_at"),
                        kwargs.get("chart_path"),
                        kwargs.get("pending_order_id"),
                        kwargs.get("position_side", "long"),
                        now,
                    ),
                )
            else:
                # Construir SET dinámico solo con los campos proporcionados
                allowed = {
                    "state", "last_analysis_at", "analysis_summary",
                    "entry_signal_at", "target_price", "stop_loss",
                    "position_qty", "position_open_at", "chart_path", "pending_order_id",
                    "position_side",
                }
                updates = {k: v for k, v in kwargs.items() if k in allowed}
                updates["updated_at"] = now
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [symbol]
                conn.execute(
                    f"UPDATE symbol_states SET {set_clause} WHERE symbol = ?",
                    values,
                )

    def get_all_symbol_states(self) -> list[dict]:
        """Devuelve el estado de todos los símbolos."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM symbol_states ORDER BY symbol"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Agent reports ─────────────────────────────────────────────────────────

    def save_agent_report(self, symbol: str, agent_name: str, report: str):
        """Guarda el informe de un sub-agente para un símbolo."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_reports (symbol, agent_name, report, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (symbol.upper(), agent_name, report, datetime.now(timezone.utc).isoformat()),
            )

    # ── Market context (informe holístico global) ─────────────────────────────

    def save_market_context(self, report: str, news_count: int) -> None:
        """Persiste un nuevo informe de contexto global de mercado."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_context (report, news_count, created_at)
                VALUES (?, ?, ?)
                """,
                (report, news_count, datetime.now(timezone.utc).isoformat()),
            )

    def get_latest_market_context(self) -> dict | None:
        """Devuelve el informe más reciente de contexto global, o None si no hay ninguno."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM market_context ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_market_context_history(self, limit: int = 10) -> list[dict]:
        """Devuelve el historial de informes de contexto global."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM market_context ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_agent_reports(self, symbol: str, limit: int = 20) -> list[dict]:
        """Devuelve el historial de informes de sub-agentes para un símbolo."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM agent_reports
                WHERE symbol = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (symbol.upper(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_agent_report(self, symbol: str, agent_name: str) -> dict | None:
        """Devuelve el informe más reciente de un sub-agente específico para un símbolo."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM agent_reports
                WHERE symbol = ? AND agent_name = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (symbol.upper(), agent_name),
            ).fetchone()
        return dict(row) if row else None
