"""
Tests de las nuevas fuentes de noticias: AlpacaNewsSource, RSSNewsSource,
YahooSymbolSource y NewsAggregator.

Todos los tests son unitarios (sin red real).
"""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.news_sources import (
    AlpacaNewsSource,
    RSSNewsSource,
    YahooSymbolSource,
    NewsAggregator,
)
from src.ingestion import Article


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _alpaca_item(n: int) -> dict:
    return {
        "headline": f"Alpaca Headline {n}",
        "summary": f"Summary {n}",
        "url": f"https://alpaca.com/news/{n}",
        "author": f"Author{n}",
        "created_at": "2024-03-15T10:00:00Z",
    }


def _rss_entry(n: int) -> dict:
    return {
        "title": f"RSS Title {n}",
        "link": f"https://rss.com/article/{n}",
        "summary": f"RSS Summary {n}",
        "published": "Mon, 15 Mar 2024 10:00:00 +0000",
    }


def _make_parsed_feed(entries: list[dict]) -> MagicMock:
    feed = MagicMock()
    feed.get = lambda key, default=None: entries if key == "entries" else default
    return feed


# ── AlpacaNewsSource ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alpaca_news_source_parses_response():
    """Verifica que AlpacaNewsSource parsea correctamente la respuesta de la API."""
    source = AlpacaNewsSource(api_key="key", secret_key="secret")
    data = {"news": [_alpaca_item(1), _alpaca_item(2)]}

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=data)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.news_sources.aiohttp.ClientSession", return_value=mock_session_ctx):
        articles = await source.fetch(limit=10)

    assert len(articles) == 2
    assert articles[0].title == "Alpaca Headline 1"
    assert articles[0].url == "https://alpaca.com/news/1"
    assert articles[0].feed_group == "alpaca"
    assert isinstance(articles[0].published_at, datetime)


@pytest.mark.asyncio
async def test_alpaca_news_source_by_symbol_includes_symbol_param():
    """Verifica que al pasar symbols, el parámetro se incluye en la petición."""
    source = AlpacaNewsSource(api_key="key", secret_key="secret")
    captured_params = {}

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"news": []})

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    def _capture_get(url, params=None, timeout=None):
        captured_params.update(params or {})
        return mock_ctx

    mock_session = MagicMock()
    mock_session.get = _capture_get

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.news_sources.aiohttp.ClientSession", return_value=mock_session_ctx):
        await source.fetch(symbols=["AAPL"], limit=20)

    assert captured_params.get("symbols") == "AAPL"


@pytest.mark.asyncio
async def test_alpaca_news_source_returns_empty_on_error():
    """Verifica que retorna lista vacía en caso de excepción de red."""
    source = AlpacaNewsSource(api_key="key", secret_key="secret")

    with patch("src.news_sources.aiohttp.ClientSession", side_effect=Exception("network error")):
        articles = await source.fetch()

    assert articles == []


# ── RSSNewsSource ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rss_source_parses_feed():
    """Verifica que RSSNewsSource parsea correctamente las entradas de un feed."""
    source = RSSNewsSource()
    entries = [_rss_entry(1), _rss_entry(2)]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch(feed_groups=("stocks",))

    # Al menos los artículos de los feeds stocks (4 feeds, uno mockeado)
    urls = [a.url for a in articles]
    assert "https://rss.com/article/1" in urls
    assert all(a.feed_group == "stocks" for a in articles)
    assert all(isinstance(a.published_at, datetime) for a in articles)


@pytest.mark.asyncio
async def test_rss_source_returns_empty_on_timeout():
    """Verifica que un feed con timeout no rompe el resto."""
    source = RSSNewsSource()

    async def _timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        articles = await source.fetch(feed_groups=("stocks",))

    assert articles == []


# ── YahooSymbolSource ─────────────────────────────────────────────────────────────

def test_yahoo_symbol_source_builds_correct_url():
    """Verifica que la URL se construye correctamente con el símbolo dado."""
    source = YahooSymbolSource()
    expected = "https://finance.yahoo.com/rss/headline?s=AAPL"
    assert source.TEMPLATE.format(symbol="AAPL") == expected


@pytest.mark.asyncio
async def test_yahoo_symbol_source_parses_feed():
    """Verifica que YahooSymbolSource parsea correctamente las entradas."""
    source = YahooSymbolSource()
    entries = [_rss_entry(1)]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch("TSLA")

    assert len(articles) == 1
    assert articles[0].feed_group == "yahoo_symbol"
    assert articles[0].source == "Yahoo Finance"


# ── NewsAggregator ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_news_aggregator_deduplicates_by_url():
    """Verifica que artículos con la misma URL de distintas fuentes se deduplicán."""
    shared_url = "https://shared.com/article/1"

    alpaca_article = Article(
        source="Alpaca", title="A", description="", content="",
        url=shared_url, published_at=datetime.now(timezone.utc), feed_group="alpaca"
    )
    rss_article = Article(
        source="Reuters", title="A duplicate", description="", content="",
        url=shared_url, published_at=datetime.now(timezone.utc), feed_group="stocks"
    )

    mock_alpaca = MagicMock()
    mock_alpaca.fetch = AsyncMock(return_value=[alpaca_article])
    mock_rss = MagicMock()
    mock_rss.fetch = AsyncMock(return_value=[rss_article])
    mock_yahoo = MagicMock()

    aggregator = NewsAggregator(mock_alpaca, mock_rss, mock_yahoo)
    articles = await aggregator.fetch_market_news()

    urls = [a.url for a in articles]
    assert urls.count(shared_url) == 1


@pytest.mark.asyncio
async def test_news_aggregator_fetch_symbol_excludes_stored_urls():
    """Verifica que stored_urls excluye artículos ya presentes en BD."""
    stored_url = "https://already-in-db.com/1"
    fresh_url = "https://new-article.com/2"

    yahoo_articles = [
        Article(source="Yahoo Finance", title="Old", description="", content="",
                url=stored_url, published_at=datetime.now(timezone.utc), feed_group="yahoo_symbol"),
        Article(source="Yahoo Finance", title="New", description="", content="",
                url=fresh_url, published_at=datetime.now(timezone.utc), feed_group="yahoo_symbol"),
    ]

    mock_alpaca = MagicMock()
    mock_alpaca.fetch = AsyncMock(return_value=[])
    mock_rss = MagicMock()
    mock_yahoo = MagicMock()
    mock_yahoo.fetch = AsyncMock(return_value=yahoo_articles)

    aggregator = NewsAggregator(mock_alpaca, mock_rss, mock_yahoo)
    articles = await aggregator.fetch_symbol_news("AAPL", stored_urls={stored_url})

    assert len(articles) == 1
    assert articles[0].url == fresh_url
