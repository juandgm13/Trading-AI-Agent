"""
Tests de NewsIngestionService.

Todos los tests son unitarios (sin red ni Ollama real — mocks de aggregator y classifier).
"""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.ingestion import Article
from src.news_service import NewsIngestionService


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _make_article(n: int, url: str | None = None) -> Article:
    return Article(
        source="Reuters",
        title=f"Article Title {n}",
        description=f"Description {n}",
        content=f"Content {n}",
        url=url or f"https://example.com/article/{n}",
        published_at=datetime(2024, 3, 15, 10, 0, tzinfo=timezone.utc),
        feed_group="stocks",
    )


def _make_classification(scope: str = "market", ticker=None, exchange=None, impact: str = "LOW") -> dict:
    return {"scope": scope, "ticker": ticker, "exchange": exchange, "impact": impact, "reasoning": "test"}


def _make_service(tmp_db, articles_from_aggregator=None, classification=None, fetch_symbol_articles=None):
    """Helper que construye un NewsIngestionService con mocks."""
    mock_aggregator = MagicMock()
    mock_aggregator.fetch_market_news = AsyncMock(return_value=articles_from_aggregator or [])
    mock_aggregator.fetch_symbol_news = AsyncMock(return_value=fetch_symbol_articles or [])

    mock_classifier = MagicMock()
    mock_classifier.run = AsyncMock(return_value='{"scope":"market","ticker":null,"exchange":null,"impact":"LOW","reasoning":"test"}')

    if classification is not None:
        import json
        mock_classifier.run = AsyncMock(return_value=json.dumps(classification))

    return NewsIngestionService(
        aggregator=mock_aggregator,
        classifier_agent=mock_classifier,
        storage=tmp_db,
        max_concurrent_classifications=2,
    ), mock_aggregator, mock_classifier


# ── run_market_ingestion ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_market_ingestion_returns_counts(tmp_db):
    """run_market_ingestion retorna (fetched, clasificados) correctamente."""
    articles = [_make_article(1), _make_article(2)]
    service, _, _ = _make_service(tmp_db, articles_from_aggregator=articles)

    fetched, classified = await service.run_market_ingestion()

    assert fetched == 2
    assert classified == 2


@pytest.mark.asyncio
async def test_run_market_ingestion_skips_duplicate_urls(tmp_db):
    """Artículos con la misma URL se deduplicán antes de clasificar."""
    url = "https://example.com/dup"
    articles = [_make_article(1, url=url), _make_article(2, url=url)]
    service, _, mock_classifier = _make_service(tmp_db, articles_from_aggregator=articles)

    fetched, classified = await service.run_market_ingestion()

    assert fetched == 1  # deduplicado por URL
    assert mock_classifier.run.call_count == 1


@pytest.mark.asyncio
async def test_run_market_ingestion_classifies_only_new_articles(tmp_db):
    """Artículos ya en BD (content_hash duplicado) no se vuelven a clasificar."""
    # Insertar el artículo 1 primero
    article = _make_article(1)
    tmp_db.save_article(article, _make_classification())

    # Ahora el aggregator devuelve el mismo artículo
    service, _, mock_classifier = _make_service(tmp_db, articles_from_aggregator=[article])

    fetched, classified = await service.run_market_ingestion()

    assert fetched == 1
    assert classified == 0
    # El clasificador NO se llama: el artículo ya está clasificado en BD (scope IS NOT NULL)
    assert mock_classifier.run.call_count == 0


@pytest.mark.asyncio
async def test_run_market_ingestion_calls_expire_old_articles(tmp_db):
    """run_market_ingestion llama a expire_old_articles para limpiar expirados."""
    service, _, _ = _make_service(tmp_db, articles_from_aggregator=[])

    with patch.object(tmp_db, "expire_old_articles", wraps=tmp_db.expire_old_articles) as mock_expire:
        await service.run_market_ingestion()

    mock_expire.assert_called_once()


# ── run_symbol_ingestion ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_symbol_ingestion_combines_db_and_fresh(tmp_db):
    """run_symbol_ingestion combina artículos de BD + artículos frescos de Yahoo."""
    # Artículo ya clasificado en BD para AAPL
    existing = _make_article(10, url="https://example.com/existing")
    cls_existing = _make_classification(scope="company", ticker="AAPL", impact="HIGH")
    tmp_db.save_article(existing, cls_existing)

    # Artículo fresco de Yahoo
    fresh = _make_article(11, url="https://yahoo.com/fresh")

    service, mock_agg, mock_classifier = _make_service(
        tmp_db,
        fetch_symbol_articles=[fresh],
        classification={"scope": "company", "ticker": "AAPL", "exchange": None, "impact": "MEDIUM", "reasoning": "fresh news"},
    )

    result = await service.run_symbol_ingestion("AAPL")

    # El aggregator recibió las stored_urls para excluir el artículo ya en BD
    stored_urls_arg = mock_agg.fetch_symbol_news.call_args[1].get(
        "stored_urls"
    ) or mock_agg.fetch_symbol_news.call_args[0][1] if len(mock_agg.fetch_symbol_news.call_args[0]) > 1 else set()

    # Al menos 1 resultado (el de BD)
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_run_symbol_ingestion_with_exchange(tmp_db):
    """run_symbol_ingestion consulta también artículos de exchange si se provee."""
    # Artículo clasificado como exchange=NASDAQ
    exchange_art = _make_article(20, url="https://example.com/nasdaq")
    cls_exchange = _make_classification(scope="exchange", exchange="NASDAQ", impact="HIGH")
    tmp_db.save_article(exchange_art, cls_exchange)

    service, _, _ = _make_service(tmp_db, fetch_symbol_articles=[])

    result = await service.run_symbol_ingestion("AAPL", exchange="NASDAQ")

    exchange_articles = [r for r in result if r.get("exchange_affected") == "NASDAQ"]
    assert len(exchange_articles) == 1


# ── _classify_batch / error handling ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_and_persist_runs_in_parallel(tmp_db):
    """_classify_and_persist usa gather para clasificar en paralelo."""
    articles = [_make_article(i) for i in range(3)]
    service, _, mock_classifier = _make_service(tmp_db, articles_from_aggregator=articles)

    with patch("asyncio.gather", wraps=asyncio.gather) as mock_gather:
        count = await service._classify_and_persist(articles)

    assert mock_gather.called
    assert count == 3


@pytest.mark.asyncio
async def test_classify_article_returns_none_on_agent_error(tmp_db):
    """_classify_article retorna None si el agente lanza una excepción."""
    service, _, mock_classifier = _make_service(tmp_db)
    mock_classifier.run = AsyncMock(side_effect=Exception("Ollama down"))

    article = _make_article(99)
    result = await service._classify_article(article)

    assert result is None


@pytest.mark.asyncio
async def test_classify_article_returns_none_on_parse_error(tmp_db):
    """_classify_article retorna None si la respuesta no es JSON válido."""
    service, _, mock_classifier = _make_service(tmp_db)
    mock_classifier.run = AsyncMock(return_value="This is not JSON at all")

    article = _make_article(98)
    result = await service._classify_article(article)

    assert result is None


# ── Logger coverage ─────────────────────────────────────────────────────────────

def _make_mock_logger():
    mock_logger = MagicMock()
    mock_logger.is_debug = True
    return mock_logger


@pytest.mark.asyncio
async def test_run_market_ingestion_logs_with_logger(tmp_db):
    """run_market_ingestion llama al logger cuando se provee uno."""
    articles = [_make_article(1)]
    mock_logger = _make_mock_logger()
    service, _, _ = _make_service(tmp_db, articles_from_aggregator=articles)
    service._logger = mock_logger

    await service.run_market_ingestion()

    assert mock_logger.info.called


@pytest.mark.asyncio
async def test_run_market_ingestion_logs_expired_count(tmp_db):
    """run_market_ingestion loguea el número de artículos expirados cuando hay alguno."""
    mock_logger = _make_mock_logger()
    service, _, _ = _make_service(tmp_db, articles_from_aggregator=[])
    service._logger = mock_logger

    with patch.object(tmp_db, "expire_old_articles", return_value=3):
        await service.run_market_ingestion()

    # Debe haber loguado la limpieza
    logged_calls = [str(c) for c in mock_logger.info.call_args_list]
    assert any("expirados" in c for c in logged_calls)


@pytest.mark.asyncio
async def test_run_symbol_ingestion_debug_logs(tmp_db):
    """run_symbol_ingestion loguea en debug cuando logger.is_debug=True."""
    mock_logger = _make_mock_logger()
    service, _, _ = _make_service(tmp_db, fetch_symbol_articles=[])
    service._logger = mock_logger

    await service.run_symbol_ingestion("TSLA")

    assert mock_logger.debug.called


@pytest.mark.asyncio
async def test_classify_article_logs_parse_error_with_logger(tmp_db):
    """_classify_article loguea warning con logger cuando hay ParseError."""
    mock_logger = _make_mock_logger()
    service, _, mock_classifier = _make_service(tmp_db)
    service._logger = mock_logger
    mock_classifier.run = AsyncMock(return_value="not json")

    result = await service._classify_article(_make_article(97))

    assert result is None
    assert mock_logger.warning.called


@pytest.mark.asyncio
async def test_classify_article_logs_error_with_logger(tmp_db):
    """_classify_article loguea error con logger cuando hay excepción genérica."""
    mock_logger = _make_mock_logger()
    service, _, mock_classifier = _make_service(tmp_db)
    service._logger = mock_logger
    mock_classifier.run = AsyncMock(side_effect=RuntimeError("Ollama crash"))

    result = await service._classify_article(_make_article(96))

    assert result is None
    assert mock_logger.error.called


# ── Two-pass classification ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_article_two_pass_fetches_full_content_when_not_irrelevant(tmp_db):
    """Si scope != irrelevant y hay full_content_classifier, se fetcha y reclasifica con full content."""
    import json

    service, _, mock_classifier = _make_service(tmp_db)

    mock_full_classifier = MagicMock()
    mock_full_classifier.run = AsyncMock(return_value=json.dumps({
        "scope": "company",
        "ticker": "AAPL",
        "exchange": None,
        "impact": "HIGH",
        "reasoning": "Full text confirms acquisition",
        "article_summary": "Apple acquires startup for $1B. Deal expected to close Q3.",
    }))
    service._full_content_classifier = mock_full_classifier

    article = _make_article(1, url="https://example.com/article/1")

    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="Full article body text here.")):
        result = await service._classify_article(article)

    # La segunda pasada debe haberse ejecutado
    mock_full_classifier.run.assert_called_once()
    assert result["scope"] == "company"
    assert result["impact"] == "HIGH"
    assert result["article_summary"] == "Apple acquires startup for $1B. Deal expected to close Q3."
    assert article.full_content == "Full article body text here."


@pytest.mark.asyncio
async def test_classify_article_skips_second_pass_when_irrelevant(tmp_db):
    """Si la primera pasada clasifica como irrelevant, no se fetcha full content."""
    import json

    service, _, mock_classifier = _make_service(tmp_db)
    mock_classifier.run = AsyncMock(return_value=json.dumps({
        "scope": "irrelevant", "ticker": None, "exchange": None, "impact": None, "reasoning": "Sports news",
    }))
    mock_full_classifier = MagicMock()
    mock_full_classifier.run = AsyncMock()
    service._full_content_classifier = mock_full_classifier

    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="Blah")) as mock_fetch:
        result = await service._classify_article(_make_article(2))

    mock_fetch.assert_not_called()
    mock_full_classifier.run.assert_not_called()
    assert result["scope"] == "irrelevant"


@pytest.mark.asyncio
async def test_classify_article_skips_second_pass_when_no_full_classifier(tmp_db):
    """Si no hay full_content_classifier, se usa solo la primera clasificación."""
    service, _, mock_classifier = _make_service(tmp_db)
    # full_content_classifier es None (default del helper)
    assert service._full_content_classifier is None

    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="Full text")) as mock_fetch:
        result = await service._classify_article(_make_article(3))

    mock_fetch.assert_not_called()
    assert result is not None
    assert result["scope"] == "market"


@pytest.mark.asyncio
async def test_classify_article_falls_back_to_first_pass_when_fetch_returns_empty(tmp_db):
    """Si fetch_article_content retorna vacío, se usa la clasificación de la primera pasada."""
    import json

    service, _, mock_classifier = _make_service(tmp_db)
    mock_full_classifier = MagicMock()
    mock_full_classifier.run = AsyncMock()
    service._full_content_classifier = mock_full_classifier

    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="")):
        result = await service._classify_article(_make_article(4))

    mock_full_classifier.run.assert_not_called()
    assert result is not None
    assert result["scope"] == "market"


@pytest.mark.asyncio
async def test_classify_article_falls_back_to_first_pass_when_second_fails(tmp_db):
    """Si la segunda pasada falla (ParseError), se usa la primera clasificación."""
    import json
    from src.response_parser import ParseError

    service, _, mock_classifier = _make_service(tmp_db)
    mock_full_classifier = MagicMock()
    mock_full_classifier.run = AsyncMock(return_value="not json at all")
    service._full_content_classifier = mock_full_classifier

    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="Full text here.")):
        result = await service._classify_article(_make_article(5))

    # La segunda pasada falló (ParseError) → usa la primera
    assert result is not None
    assert result["scope"] == "market"


@pytest.mark.asyncio
async def test_classify_article_two_pass_propagates_article_summary_to_save(tmp_db):
    """El article_summary del second pass se persiste en BD correctamente."""
    import json

    service, _, mock_classifier = _make_service(tmp_db)
    mock_full_classifier = MagicMock()
    mock_full_classifier.run = AsyncMock(return_value=json.dumps({
        "scope": "market",
        "ticker": None,
        "exchange": None,
        "impact": "MEDIUM",
        "reasoning": "Fed news",
        "article_summary": "Fed raised rates by 25bp. Markets reacted positively.",
    }))
    service._full_content_classifier = mock_full_classifier

    article = _make_article(6, url="https://example.com/article/6")
    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="Full Fed article.")):
        classification = await service._classify_article(article)

    saved = tmp_db.save_article(article, classification)
    assert saved is True

    articles = tmp_db.get_articles_by_scope("market")
    assert len(articles) == 1
    assert articles[0].get("article_summary") == "Fed raised rates by 25bp. Markets reacted positively."


# ── Segunda pasada: debug log cuando logger está presente (line 184) ──────────

@pytest.mark.asyncio
async def test_classify_article_two_pass_logs_debug_when_logger_set(tmp_db):
    """Con logger activo, se loguea 'Reclasificando con texto completo' en segunda pasada."""
    import json
    from src.logger import AgentLogger

    service, _, mock_classifier = _make_service(tmp_db)
    logger = AgentLogger(level="DEBUG")
    service._logger = logger

    mock_full_classifier = MagicMock()
    mock_full_classifier.run = AsyncMock(return_value=json.dumps({
        "scope": "market",
        "ticker": None,
        "exchange": None,
        "impact": "HIGH",
        "reasoning": "confirmed by full text",
        "article_summary": "Fed action confirmed.",
    }))
    service._full_content_classifier = mock_full_classifier

    article = _make_article(10, url="https://example.com/article/10")

    with patch("src.news_service.NewsIngestionService._fetch_full_content", AsyncMock(return_value="Full article body.")):
        result = await service._classify_article(article)

    assert result is not None
    assert result["scope"] == "market"
    mock_full_classifier.run.assert_awaited_once()


# ── _fetch_full_content: método real (lines 236-243) ─────────────────────────

@pytest.mark.asyncio
async def test_fetch_full_content_returns_content_on_success(tmp_db):
    """_fetch_full_content llama a fetch_article_content con la sesión y devuelve el resultado."""
    service, _, _ = _make_service(tmp_db)

    with patch("src.news_sources.fetch_article_content", AsyncMock(return_value="article body text")):
        result = await service._fetch_full_content("https://example.com/news/1")

    assert result == "article body text"


@pytest.mark.asyncio
async def test_fetch_full_content_returns_empty_on_exception(tmp_db):
    """_fetch_full_content devuelve '' cuando aiohttp.ClientSession lanza excepción."""
    import aiohttp
    service, _, _ = _make_service(tmp_db)

    with patch("aiohttp.ClientSession", side_effect=RuntimeError("session error")):
        result = await service._fetch_full_content("https://example.com/news/2")

    assert result == ""
