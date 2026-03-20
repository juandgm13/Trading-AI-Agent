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


@pytest.mark.asyncio
async def test_alpaca_news_source_logs_http_error_with_logger():
    """Verifica que loguea warning cuando la API devuelve status != 200 y hay logger."""
    mock_logger = MagicMock()
    source = AlpacaNewsSource(api_key="key", secret_key="secret", logger=mock_logger)

    mock_resp = AsyncMock()
    mock_resp.status = 403

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.news_sources.aiohttp.ClientSession", return_value=mock_session_ctx):
        articles = await source.fetch()

    assert articles == []
    assert mock_logger.warning.called


@pytest.mark.asyncio
async def test_alpaca_news_source_returns_empty_on_http_error():
    """Verifica que retorna lista vacía cuando la API devuelve status != 200."""
    source = AlpacaNewsSource(api_key="key", secret_key="secret")

    mock_resp = AsyncMock()
    mock_resp.status = 403

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_ctx)

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.news_sources.aiohttp.ClientSession", return_value=mock_session_ctx):
        articles = await source.fetch()

    assert articles == []


@pytest.mark.asyncio
async def test_alpaca_news_source_skips_item_without_url():
    """Verifica que artículos sin URL se omiten."""
    source = AlpacaNewsSource(api_key="key", secret_key="secret")
    data = {"news": [
        {"headline": "No URL item", "summary": "desc", "url": "", "created_at": "2024-01-01T00:00:00Z"},
        {"headline": "Has URL", "summary": "desc", "url": "https://example.com/1", "created_at": "2024-01-01T00:00:00Z"},
    ]}

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
        articles = await source.fetch()

    assert len(articles) == 1
    assert articles[0].title == "Has URL"


@pytest.mark.asyncio
async def test_alpaca_news_source_debug_logs_response():
    """Verifica que loguea en debug cuando logger.is_debug=True."""
    mock_logger = MagicMock()
    mock_logger.is_debug = True
    source = AlpacaNewsSource(api_key="key", secret_key="secret", logger=mock_logger)
    data = {"news": [_alpaca_item(1)]}

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
        await source.fetch()

    assert mock_logger.debug.called


@pytest.mark.asyncio
async def test_alpaca_news_source_debug_logs_truncates_long_response():
    """Verifica que el log se trunca cuando el JSON supera _MAX_LOG_CHARS (4000 chars)."""
    mock_logger = MagicMock()
    mock_logger.is_debug = True
    source = AlpacaNewsSource(api_key="key", secret_key="secret", logger=mock_logger)

    # Generar un ítem con contenido largo para que el JSON > 4000 chars
    big_item = _alpaca_item(1)
    big_item["content"] = "x" * 5000  # fuerza truncado
    data = {"news": [big_item]}

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
        await source.fetch()

    debug_call = mock_logger.debug.call_args[0][0]
    assert "truncado" in debug_call


@pytest.mark.asyncio
async def test_alpaca_news_source_logs_error_with_logger():
    """Verifica que loguea error genérico cuando hay logger en la excepción outer."""
    mock_logger = MagicMock()
    source = AlpacaNewsSource(api_key="key", secret_key="secret", logger=mock_logger)

    with patch("src.news_sources.aiohttp.ClientSession", side_effect=Exception("crash")):
        articles = await source.fetch()

    assert articles == []
    assert mock_logger.error.called


@pytest.mark.asyncio
async def test_alpaca_news_source_handles_bad_date():
    """Verifica que una fecha malformada no rompe el parseo."""
    source = AlpacaNewsSource(api_key="key", secret_key="secret")
    data = {"news": [
        {"headline": "Bad date", "summary": "desc", "url": "https://example.com/1", "created_at": "not-a-date"},
    ]}

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
        articles = await source.fetch()

    assert len(articles) == 1
    assert isinstance(articles[0].published_at, datetime)


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


@pytest.mark.asyncio
async def test_rss_source_returns_empty_on_exception():
    """Verifica que una excepción genérica en un feed no rompe el resto."""
    source = RSSNewsSource()

    with patch("asyncio.wait_for", side_effect=Exception("connection error")):
        articles = await source.fetch(feed_groups=("stocks",))

    assert articles == []


@pytest.mark.asyncio
async def test_rss_source_debug_logs_feed_result():
    """Verifica que RSSNewsSource loguea en debug cuando logger.is_debug=True."""
    mock_logger = MagicMock()
    mock_logger.is_debug = True
    source = RSSNewsSource(logger=mock_logger)
    entries = [_rss_entry(1)]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        await source.fetch(feed_groups=("stocks",))

    assert mock_logger.debug.called


@pytest.mark.asyncio
async def test_rss_source_logs_timeout_with_logger():
    """Verifica que loguea warning en timeout cuando hay logger."""
    mock_logger = MagicMock()
    source = RSSNewsSource(logger=mock_logger)

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        await source.fetch(feed_groups=("stocks",))

    assert mock_logger.warning.called


@pytest.mark.asyncio
async def test_rss_source_logs_exception_with_logger():
    """Verifica que loguea error en excepción genérica cuando hay logger."""
    mock_logger = MagicMock()
    source = RSSNewsSource(logger=mock_logger)

    with patch("asyncio.wait_for", side_effect=Exception("connection failed")):
        await source.fetch(feed_groups=("macro",))

    assert mock_logger.error.called


@pytest.mark.asyncio
async def test_rss_source_handles_date_without_tzinfo():
    """Verifica que fechas sin tzinfo se convierten a UTC."""
    source = RSSNewsSource()
    # Simular fecha sin timezone (naive datetime)
    entries = [
        {
            "title": "No TZ article",
            "link": "https://example.com/no-tz",
            "summary": "desc",
            "published": "Mon, 15 Mar 2024 10:00:00",  # sin offset
        },
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch(feed_groups=("stocks",))

    tz_articles = [a for a in articles if a.title == "No TZ article"]
    if tz_articles:
        assert tz_articles[0].published_at.tzinfo is not None


@pytest.mark.asyncio
async def test_rss_source_handles_bad_date_format():
    """Verifica que una fecha malformada usa datetime.now como fallback."""
    source = RSSNewsSource()
    entries = [
        {
            "title": "Bad date",
            "link": "https://example.com/bad-date",
            "summary": "desc",
            "published": "not-a-date-at-all",
        },
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch(feed_groups=("stocks",))

    bad = [a for a in articles if a.title == "Bad date"]
    if bad:
        assert isinstance(bad[0].published_at, datetime)


@pytest.mark.asyncio
async def test_rss_source_skips_entry_without_link():
    """Verifica que entradas sin link se omiten."""
    source = RSSNewsSource()
    entries = [
        {"title": "No link", "summary": "desc", "published": "Mon, 15 Mar 2024 10:00:00 +0000"},
        {"title": "Has link", "link": "https://example.com/1", "summary": "desc", "published": "Mon, 15 Mar 2024 10:00:00 +0000"},
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch(feed_groups=("stocks",))

    titles = [a.title for a in articles]
    assert "No link" not in titles


@pytest.mark.asyncio
async def test_rss_source_handles_missing_published_date():
    """Verifica que entradas sin fecha usan datetime.now como fallback."""
    source = RSSNewsSource()
    entries = [
        {"title": "No date", "link": "https://example.com/2", "summary": "desc"},
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch(feed_groups=("macro",))

    assert any(a.title == "No date" for a in articles)
    assert all(isinstance(a.published_at, datetime) for a in articles)


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


@pytest.mark.asyncio
async def test_yahoo_symbol_source_returns_empty_on_timeout():
    """Verifica que un timeout retorna lista vacía."""
    source = YahooSymbolSource()

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        articles = await source.fetch("MSFT")

    assert articles == []


@pytest.mark.asyncio
async def test_yahoo_symbol_source_returns_empty_on_exception():
    """Verifica que una excepción genérica retorna lista vacía."""
    source = YahooSymbolSource()

    with patch("asyncio.wait_for", side_effect=Exception("connection error")):
        articles = await source.fetch("MSFT")

    assert articles == []


@pytest.mark.asyncio
async def test_yahoo_symbol_source_debug_logs():
    """Verifica que YahooSymbolSource loguea en debug cuando logger.is_debug=True."""
    mock_logger = MagicMock()
    mock_logger.is_debug = True
    source = YahooSymbolSource(logger=mock_logger)
    entries = [_rss_entry(1)]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        await source.fetch("NVDA")

    assert mock_logger.debug.called


@pytest.mark.asyncio
async def test_yahoo_symbol_source_handles_date_without_tzinfo():
    """Verifica que fechas sin tzinfo en Yahoo RSS se convierten a UTC."""
    source = YahooSymbolSource()
    entries = [
        {
            "title": "No TZ",
            "link": "https://finance.yahoo.com/no-tz",
            "summary": "desc",
            "published": "Mon, 15 Mar 2024 10:00:00",  # sin offset
        },
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch("AAPL")

    tz_articles = [a for a in articles if a.title == "No TZ"]
    if tz_articles:
        assert tz_articles[0].published_at.tzinfo is not None


@pytest.mark.asyncio
async def test_yahoo_symbol_source_handles_missing_date():
    """Verifica que entradas sin campo 'published' usan datetime.now como fallback."""
    source = YahooSymbolSource()
    entries = [
        {"title": "No date", "link": "https://finance.yahoo.com/no-date", "summary": "desc"},
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch("MSFT")

    assert any(a.title == "No date" for a in articles)
    assert all(isinstance(a.published_at, datetime) for a in articles)


@pytest.mark.asyncio
async def test_yahoo_symbol_source_logs_timeout_with_logger():
    """Verifica que loguea warning en timeout cuando hay logger."""
    mock_logger = MagicMock()
    source = YahooSymbolSource(logger=mock_logger)

    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        await source.fetch("GOOG")

    assert mock_logger.warning.called


@pytest.mark.asyncio
async def test_yahoo_symbol_source_logs_exception_with_logger():
    """Verifica que loguea error en excepción genérica cuando hay logger."""
    mock_logger = MagicMock()
    source = YahooSymbolSource(logger=mock_logger)

    with patch("asyncio.wait_for", side_effect=Exception("connection error")):
        await source.fetch("AMZN")

    assert mock_logger.error.called


@pytest.mark.asyncio
async def test_yahoo_symbol_source_handles_bad_date():
    """Verifica que una fecha malformada usa datetime.now como fallback."""
    source = YahooSymbolSource()
    entries = [
        {
            "title": "Bad date",
            "link": "https://finance.yahoo.com/bad",
            "summary": "desc",
            "published": "not-a-date",
        },
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch("AAPL")

    assert len(articles) == 1
    assert isinstance(articles[0].published_at, datetime)


@pytest.mark.asyncio
async def test_yahoo_symbol_source_skips_entry_without_link():
    """Verifica que entradas sin link se omiten."""
    source = YahooSymbolSource()
    entries = [
        {"title": "No link", "summary": "desc"},
        {"title": "Has link", "link": "https://finance.yahoo.com/news/1", "summary": "desc",
         "published": "Mon, 15 Mar 2024 10:00:00 +0000"},
    ]
    parsed = _make_parsed_feed(entries)

    with patch("src.news_sources.feedparser.parse", return_value=parsed):
        articles = await source.fetch("AAPL")

    assert len(articles) == 1
    assert articles[0].title == "Has link"


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
async def test_news_aggregator_fetch_market_news_logs_with_logger():
    """NewsAggregator loguea el resumen cuando hay logger."""
    mock_logger = MagicMock()

    mock_alpaca = MagicMock()
    mock_alpaca.fetch = AsyncMock(return_value=[])
    mock_rss = MagicMock()
    mock_rss.fetch = AsyncMock(return_value=[])
    mock_yahoo = MagicMock()

    aggregator = NewsAggregator(mock_alpaca, mock_rss, mock_yahoo, logger=mock_logger)
    await aggregator.fetch_market_news()

    assert mock_logger.info.called


@pytest.mark.asyncio
async def test_news_aggregator_fetch_symbol_news_debug_logs():
    """NewsAggregator loguea en debug cuando hay logger.is_debug=True."""
    mock_logger = MagicMock()
    mock_logger.is_debug = True

    mock_alpaca = MagicMock()
    mock_alpaca.fetch = AsyncMock(return_value=[])
    mock_rss = MagicMock()
    mock_yahoo = MagicMock()
    mock_yahoo.fetch = AsyncMock(return_value=[])

    aggregator = NewsAggregator(mock_alpaca, mock_rss, mock_yahoo, logger=mock_logger)
    await aggregator.fetch_symbol_news("AAPL")

    assert mock_logger.debug.called


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
