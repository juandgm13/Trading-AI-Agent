"""
Recogida de noticias públicas para el informe diario pre-apertura.

Reutiliza las fuentes de `src/news_sources.py` (RSS + Yahoo Finance por símbolo),
sin LLM, sin Ollama y sin credenciales. Escribe un JSON compacto que Claude lee
después para redactar el informe (ver `claude_agent/daily_prompt.md`).

Uso:
    python claude_agent/fetch_news.py                 # → claude_agent/data/news_YYYY-MM-DD.json
    python claude_agent/fetch_news.py --output x.json --no-full-content
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import yaml

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_ROOT))

from src.ingestion import Article  # noqa: E402
from src.news_sources import (  # noqa: E402
    RSSNewsSource,
    YahooSymbolSource,
    _TextExtractor,
    fetch_article_content,
)

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
FEED_GROUPS = ("espana", "europa", "eeuu", "macro", "cripto")
_MIN_FULL_TEXT_CHARS = 200  # por debajo suele ser un muro de cookies o "(null)"


class CollectingLogger:
    """Logger mínimo compatible con las fuentes de noticias que guarda avisos y errores."""

    is_debug = False

    def __init__(self) -> None:
        self.issues: list[dict] = []

    def _add(self, level: str, msg: str, source: str = "") -> None:
        self.issues.append({"level": level, "source": source, "message": msg})

    def warning(self, msg, source="", **_):
        self._add("warning", msg, source)

    def error(self, msg, source="", **_):
        self._add("error", msg, source)

    def info(self, msg, source="", **_):
        pass

    def debug(self, msg, source="", **_):
        pass


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def lookback_hours(cfg: dict, now_local: datetime) -> int:
    """Los lunes amplía la ventana para incluir el fin de semana."""
    if now_local.weekday() == 0:
        return int(cfg.get("monday_lookback_hours", 72))
    return int(cfg.get("lookback_hours", 24))


def clean_text(html: str, max_chars: int) -> str:
    """Quita etiquetas HTML y recorta a max_chars."""
    if not html:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        text = extractor.get_text()
    except Exception:
        text = html
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def article_to_dict(article: Article, group: str, summary_max: int) -> dict:
    return {
        "source": article.source,
        "group": group,
        "title": " ".join((article.title or "").split()),
        "summary": clean_text(article.description, summary_max),
        "published_at": article.published_at.astimezone(timezone.utc).isoformat(),
        "url": article.url,
    }


def filter_and_dedup(
    articles: list[Article], since: datetime, seen: set[str]
) -> list[Article]:
    """Filtra por fecha y elimina duplicados por URL y título normalizado (muta `seen`)."""
    result: list[Article] = []
    for art in sorted(articles, key=lambda a: a.published_at, reverse=True):
        if art.published_at < since:
            continue
        title_key = "t:" + " ".join((art.title or "").lower().split())
        if art.url in seen or title_key in seen:
            continue
        seen.update({art.url, title_key})
        result.append(art)
    return result


def _feeds_from_config(cfg: dict) -> list[tuple[str, str, str]]:
    feeds = []
    for group in FEED_GROUPS:
        for feed in (cfg.get("feeds") or {}).get(group) or []:
            feeds.append((feed["name"], feed["url"], group))
    return feeds


def _watchlist_from_config(cfg: dict) -> list[tuple[str, str, str]]:
    """Watchlist bursátil (índices, España, EE. UU.): (símbolo, nombre, grupo)."""
    items = []
    for group, entries in (cfg.get("watchlist") or {}).items():
        for entry in entries or []:
            items.append((entry["symbol"], entry.get("name", entry["symbol"]), group))
    return items


def _crypto_watchlist_from_config(cfg: dict) -> list[tuple[str, str, str]]:
    """Watchlist de criptomonedas (bloque `crypto` de config.yaml): (símbolo, nombre, "cripto")."""
    return [
        (entry["symbol"], entry.get("name", entry["symbol"]), "cripto")
        for entry in ((cfg.get("crypto") or {}).get("watchlist") or [])
    ]


async def collect(cfg: dict, now: datetime | None = None, full_content: bool = True) -> dict:
    """Descarga noticias de mercado y por símbolo y devuelve el dict serializable."""
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    hours = lookback_hours(cfg, now_local)
    since = now - timedelta(hours=hours)
    summary_max = int(cfg.get("summary_max_chars", 400))

    logger = CollectingLogger()
    feeds = _feeds_from_config(cfg)
    group_by_source = {name: group for name, _, group in feeds}
    rss = RSSNewsSource(logger=logger, feeds=feeds)
    yahoo = YahooSymbolSource(logger=logger)
    watchlist = _watchlist_from_config(cfg) + _crypto_watchlist_from_config(cfg)

    rss_task = rss.fetch(
        feed_groups=FEED_GROUPS,
        max_articles_per_feed=int(cfg.get("max_articles_per_feed", 15)),
    )
    per_symbol = int(cfg.get("max_articles_per_symbol", 5))
    symbol_tasks = [yahoo.fetch(sym, max_articles=per_symbol * 3) for sym, _, _ in watchlist]
    results = await asyncio.gather(rss_task, *symbol_tasks, return_exceptions=True)

    seen: set[str] = set()
    rss_articles = results[0] if isinstance(results[0], list) else []
    market_articles = filter_and_dedup(rss_articles, since, seen)

    market: dict[str, list[dict]] = {g: [] for g in FEED_GROUPS}
    for art in market_articles:
        group = group_by_source.get(art.source, "eeuu")
        market[group].append(article_to_dict(art, group, summary_max))

    sources_with_news = {a.source for a in market_articles}
    sources_ok = {a.source for a in rss_articles}
    feed_status = [
        {
            "name": name,
            "group": group,
            "status": "ok" if name in sources_with_news else ("sin_noticias_recientes" if name in sources_ok else "fallo"),
        }
        for name, _, group in feeds
    ]

    symbols: dict[str, dict] = {}
    for (sym, name, group), res in zip(watchlist, results[1:]):
        arts = res if isinstance(res, list) else []
        fresh = filter_and_dedup(arts, since, seen)[:per_symbol]
        symbols[sym] = {
            "name": name,
            "group": group,
            "articles": [article_to_dict(a, f"simbolo:{sym}", summary_max) for a in fresh],
        }

    if full_content:
        await _add_full_content(cfg, market, logger)

    return {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "lookback_hours": hours,
        "counts": {
            "market": {g: len(v) for g, v in market.items()},
            "symbols": sum(len(v["articles"]) for v in symbols.values()),
        },
        "feed_status": feed_status,
        "issues": logger.issues,
        "market": market,
        "symbols": symbols,
    }


async def _add_full_content(cfg: dict, market: dict, logger: CollectingLogger) -> None:
    """Añade `full_text` a los artículos más recientes de España y EE. UU."""
    top_n = int(cfg.get("full_content_top_n", 0))
    if top_n <= 0:
        return
    max_chars = int(cfg.get("full_content_max_chars", 2500))
    skip = tuple(cfg.get("full_content_skip_domains") or ())
    candidates = sorted(
        (
            a
            for a in market.get("espana", []) + market.get("eeuu", [])
            if not any(dom in a["url"] for dom in skip)
        ),
        key=lambda a: a["published_at"],
        reverse=True,
    )[:top_n]
    # trust_env=True → respeta HTTPS_PROXY (necesario en la nube de Anthropic)
    async with aiohttp.ClientSession(trust_env=True) as session:
        texts = await asyncio.gather(
            *(fetch_article_content(a["url"], session) for a in candidates),
            return_exceptions=True,
        )
    for art, text in zip(candidates, texts):
        if isinstance(text, str) and len(text.strip()) >= _MIN_FULL_TEXT_CHARS:
            art["full_text"] = clean_text(text, max_chars)


def default_output(now_local: datetime) -> Path:
    return DEFAULT_DATA_DIR / f"news_{now_local.date().isoformat()}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recoge noticias públicas para el informe diario.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-full-content", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    data = asyncio.run(collect(cfg, full_content=not args.no_full_content))

    out = Path(args.output) if args.output else default_output(
        datetime.fromisoformat(data["generated_at_local"])
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = data["counts"]
    failed = [f["name"] for f in data["feed_status"] if f["status"] == "fallo"]
    print(f"Noticias guardadas en {out}")
    print(f"  Mercado: {counts['market']} | Por símbolo: {counts['symbols']}")
    print(f"  Feeds con fallo: {', '.join(failed) if failed else 'ninguno'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
