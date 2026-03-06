import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from .ingestion import Article


@dataclass
class DeduplicationResult:
    is_new: bool
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
                    alert_sent   INTEGER DEFAULT 0,
                    created_at   TEXT
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def check_and_mark(self, article: Article) -> DeduplicationResult:
        content_hash = self._content_hash(article)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT url FROM articles WHERE content_hash = ?", (content_hash,)
            ).fetchone()

            if row:
                return DeduplicationResult(is_new=False, article=article)

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

        return DeduplicationResult(is_new=True, article=article)

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
                    alert_sent   = ?
                WHERE url = ?
                """,
                (
                    llm_output.get("company"),
                    llm_output.get("ticker"),
                    llm_output.get("impact_level"),
                    llm_output.get("impact_type"),
                    llm_output.get("confidence"),
                    1 if alert_result.get("sent") else 0,
                    article.url,
                ),
            )

    def reset(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM articles")
