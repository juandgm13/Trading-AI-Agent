import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .ingestion import Article


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
                    url          TEXT PRIMARY KEY,
                    content_hash TEXT UNIQUE,
                    title        TEXT,
                    source       TEXT,
                    published_at TEXT,
                    company      TEXT,
                    ticker       TEXT,
                    impact_level TEXT,
                    impact_type  TEXT,
                    confidence   INTEGER,
                    reason       TEXT,
                    alert_sent   INTEGER DEFAULT 0,
                    created_at   TEXT
                )
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
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol        TEXT PRIMARY KEY,
                    company       TEXT,
                    threshold_pct REAL DEFAULT 2.0,
                    active        INTEGER DEFAULT 1
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
    ):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (symbol, side, qty, price, order_id, status, trigger, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, side, qty, price, order_id, status, trigger, reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

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

    def upsert_watchlist(self, symbol: str, company: str = "", threshold_pct: float = 2.0):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist (symbol, company, threshold_pct, active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(symbol) DO UPDATE SET
                    company       = excluded.company,
                    threshold_pct = excluded.threshold_pct,
                    active        = 1
                """,
                (symbol.upper(), company, threshold_pct),
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
                         position_qty, position_open_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        now,
                    ),
                )
            else:
                # Construir SET dinámico solo con los campos proporcionados
                allowed = {
                    "state", "last_analysis_at", "analysis_summary",
                    "entry_signal_at", "target_price", "stop_loss",
                    "position_qty", "position_open_at",
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
