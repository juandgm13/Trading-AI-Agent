"""
Fuentes de noticias financieras: Alpaca News API, RSS feeds y Yahoo Finance por símbolo.

Reemplaza GNews como fuente de noticias del sistema.
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import aiohttp
import feedparser

from src.ingestion import Article


_TIMEOUT_SECONDS = 10
_RSS_TIMEOUT_SECONDS = 5
_MAX_LOG_CHARS = 4000


class AlpacaNewsSource:
    """Noticias vía Alpaca News API (usa las mismas credenciales que el broker)."""

    BASE_URL = "https://data.alpaca.markets/v1beta1/news"

    def __init__(self, api_key: str, secret_key: str, logger=None):
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self._logger = logger

    async def fetch(
        self,
        symbols: list[str] | None = None,
        limit: int = 50,
    ) -> list[Article]:
        """
        Obtiene artículos de Alpaca News API.

        symbols=None → noticias generales de mercado.
        symbols=["AAPL"] → noticias filtradas por símbolo.
        """
        params: dict = {"limit": min(limit, 50)}
        if symbols:
            params["symbols"] = ",".join(symbols)

        # Solo noticias de los últimos 3 días
        start = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
        params["start"] = start

        try:
            async with aiohttp.ClientSession(headers=self._headers) as session:
                async with session.get(
                    self.BASE_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status != 200:
                        if self._logger:
                            self._logger.warning(
                                f"Alpaca News API → HTTP {resp.status}", source="alpaca_news"
                            )
                        return []
                    data = await resp.json()

            if self._logger and self._logger.is_debug:
                raw = json.dumps(data, ensure_ascii=False)
                if len(raw) > _MAX_LOG_CHARS:
                    raw = raw[:_MAX_LOG_CHARS] + f"… [truncado {len(raw)} chars]"
                self._logger.debug(
                    f"GET {self.BASE_URL} symbols={symbols} → {len(data.get('news', []))} artículos | {raw}",
                    source="alpaca_news",
                )

            articles = []
            for item in data.get("news", []):
                url = item.get("url", "")
                if not url:
                    continue
                try:
                    published_at = datetime.fromisoformat(
                        item.get("created_at", "").replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    published_at = datetime.now(timezone.utc)

                articles.append(
                    Article(
                        source=item.get("author") or item.get("source", "Alpaca"),
                        title=item.get("headline", ""),
                        description=item.get("summary", ""),
                        content=item.get("summary", ""),
                        url=url,
                        published_at=published_at,
                        feed_group="alpaca",
                    )
                )
            return articles

        except Exception as exc:
            if self._logger:
                self._logger.error(
                    f"Error fetching Alpaca News: {exc}", source="alpaca_news", exc_info=True
                )
            return []


class RSSNewsSource:
    """Noticias vía feeds RSS usando feedparser."""

    FEEDS_STOCKS: list[tuple[str, str]] = [
        ("Reuters", "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("Yahoo Finance", "https://finance.yahoo.com/rss/topstories"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ]

    FEEDS_MACRO: list[tuple[str, str]] = [
        ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
        ("Financial Times", "https://www.ft.com/world?format=rss"),
        ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml"),
    ]

    FEEDS_CRYPTO: list[tuple[str, str]] = []  # provisión futura

    def __init__(self, logger=None):
        self._logger = logger

    async def fetch(
        self,
        feed_groups: tuple[str, ...] = ("stocks", "macro"),
        max_articles_per_feed: int = 20,
    ) -> list[Article]:
        """
        Obtiene artículos de los feeds RSS de los grupos indicados.

        feed_groups puede incluir "stocks", "macro", "crypto" (deshabilitado).
        max_articles_per_feed: máximo de artículos a devolver por feed (los más recientes).
        """
        feeds: list[tuple[str, str, str]] = []  # (source, url, group)
        if "stocks" in feed_groups:
            feeds.extend((src, url, "stocks") for src, url in self.FEEDS_STOCKS)
        if "macro" in feed_groups:
            feeds.extend((src, url, "macro") for src, url in self.FEEDS_MACRO)
        if "crypto" in feed_groups:
            feeds.extend((src, url, "crypto") for src, url in self.FEEDS_CRYPTO)

        loop = asyncio.get_event_loop()
        tasks = [
            self._fetch_feed(loop, source, url, group, max_articles_per_feed)
            for source, url, group in feeds
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        articles: list[Article] = []
        for res in results:
            if isinstance(res, list):
                articles.extend(res)
        return articles

    async def _fetch_feed(
        self,
        loop: asyncio.AbstractEventLoop,
        source: str,
        url: str,
        group: str,
        max_articles: int = 20,
    ) -> list[Article]:
        """Parsea un feed RSS en un executor con timeout. Devuelve los max_articles más recientes."""
        try:
            parsed = await asyncio.wait_for(
                loop.run_in_executor(None, feedparser.parse, url),
                timeout=_RSS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            if self._logger:
                self._logger.warning(f"RSS timeout: {url}", source="rss_news")
            return []
        except Exception as exc:
            if self._logger:
                self._logger.error(
                    f"RSS error ({url}): {exc}", source="rss_news", exc_info=True
                )
            return []

        articles: list[Article] = []
        for entry in parsed.get("entries", []):
            url_entry = entry.get("link", "")
            if not url_entry:
                continue

            published_at: datetime
            if entry.get("published"):
                try:
                    published_at = parsedate_to_datetime(entry["published"])
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                except Exception:
                    published_at = datetime.now(timezone.utc)
            else:
                published_at = datetime.now(timezone.utc)

            description = entry.get("summary", "") or entry.get("description", "")
            articles.append(
                Article(
                    source=source,
                    title=entry.get("title", ""),
                    description=description,
                    content=description,
                    url=url_entry,
                    published_at=published_at,
                    feed_group=source,
                )
            )

        articles.sort(key=lambda a: a.published_at, reverse=True)
        articles = articles[:max_articles]

        if self._logger and self._logger.is_debug:
            self._logger.debug(
                f"RSS {source}: {len(articles)} artículos (top {max_articles} recientes)",
                source="rss_news",
            )
        return articles


class YahooSymbolSource:
    """Noticias de Yahoo Finance RSS filtradas por símbolo específico."""

    TEMPLATE = "https://finance.yahoo.com/rss/headline?s={symbol}"

    def __init__(self, logger=None):
        self._logger = logger

    async def fetch(self, symbol: str, max_articles: int = 20) -> list[Article]:
        """Obtiene noticias de Yahoo Finance para un símbolo concreto (los max_articles más recientes)."""
        url = self.TEMPLATE.format(symbol=symbol)
        loop = asyncio.get_event_loop()
        try:
            parsed = await asyncio.wait_for(
                loop.run_in_executor(None, feedparser.parse, url),
                timeout=_RSS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            if self._logger:
                self._logger.warning(
                    f"Yahoo RSS timeout para {symbol}", source="yahoo_news"
                )
            return []
        except Exception as exc:
            if self._logger:
                self._logger.error(
                    f"Yahoo RSS error ({symbol}): {exc}", source="yahoo_news", exc_info=True
                )
            return []

        articles: list[Article] = []
        for entry in parsed.get("entries", []):
            url_entry = entry.get("link", "")
            if not url_entry:
                continue

            published_at: datetime
            if entry.get("published"):
                try:
                    published_at = parsedate_to_datetime(entry["published"])
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                except Exception:
                    published_at = datetime.now(timezone.utc)
            else:
                published_at = datetime.now(timezone.utc)

            description = entry.get("summary", "") or entry.get("description", "")
            articles.append(
                Article(
                    source="Yahoo Finance",
                    title=entry.get("title", ""),
                    description=description,
                    content=description,
                    url=url_entry,
                    published_at=published_at,
                    feed_group="Yahoo Finance",
                )
            )

        articles.sort(key=lambda a: a.published_at, reverse=True)
        articles = articles[:max_articles]

        if self._logger and self._logger.is_debug:
            self._logger.debug(
                f"Yahoo {symbol}: {len(articles)} artículos (top {max_articles} recientes)",
                source="yahoo_news",
            )
        return articles


class NewsAggregator:
    """
    Orquesta todas las fuentes de noticias y elimina duplicados por URL.

    Uso:
        aggregator = NewsAggregator(alpaca, rss, yahoo, logger=logger)
        market_articles = await aggregator.fetch_market_news()
        symbol_articles = await aggregator.fetch_symbol_news("AAPL", stored_urls={"https://..."})
    """

    def __init__(
        self,
        alpaca_source: AlpacaNewsSource,
        rss_source: RSSNewsSource,
        yahoo_source: YahooSymbolSource,
        logger=None,
    ):
        self._alpaca = alpaca_source
        self._rss = rss_source
        self._yahoo = yahoo_source
        self._logger = logger

    async def fetch_market_news(self, limit_per_source: int = 20) -> list[Article]:
        """
        Noticias para contexto global de mercado:
        - Alpaca News general (sin filtro de símbolo)
        - RSS stocks + macro
        Deduplica por URL. Devuelve los limit_per_source artículos más recientes por fuente.
        """
        alpaca_task = self._alpaca.fetch(symbols=None, limit=limit_per_source)
        rss_task = self._rss.fetch(feed_groups=("stocks", "macro"), max_articles_per_feed=limit_per_source)

        alpaca_articles, rss_articles = await asyncio.gather(alpaca_task, rss_task)

        seen_urls: set[str] = set()
        articles: list[Article] = []
        for article in alpaca_articles + rss_articles:
            if article.url and article.url not in seen_urls:
                seen_urls.add(article.url)
                articles.append(article)

        if self._logger:
            self._logger.info(
                f"fetch_market_news: {len(articles)} artículos únicos "
                f"(alpaca={len(alpaca_articles)}, rss={len(rss_articles)})",
                source="news_aggregator",
            )
        return articles

    async def fetch_symbol_news(
        self,
        symbol: str,
        stored_urls: set[str] | None = None,
    ) -> list[Article]:
        """
        Noticias frescas para un símbolo concreto:
        - Yahoo Finance RSS por símbolo
        - Alpaca News filtrado por símbolo
        Excluye URLs ya presentes en stored_urls (ya están en BD).
        """
        stored_urls = stored_urls or set()

        yahoo_task = self._yahoo.fetch(symbol, max_articles=20)
        alpaca_task = self._alpaca.fetch(symbols=[symbol], limit=20)

        yahoo_articles, alpaca_articles = await asyncio.gather(yahoo_task, alpaca_task)

        seen_urls: set[str] = set(stored_urls)
        articles: list[Article] = []
        for article in yahoo_articles + alpaca_articles:
            if article.url and article.url not in seen_urls:
                seen_urls.add(article.url)
                articles.append(article)

        if self._logger and self._logger.is_debug:
            self._logger.debug(
                f"fetch_symbol_news({symbol}): {len(articles)} artículos nuevos",
                source="news_aggregator",
            )
        return articles
