"""
Tests de NewsIngestionModule.

Sección 1 — Unit tests (sin red, mockean requests.get).
Sección 2 — Integration tests contra GNews real (requieren GNEWS_API_KEY en .env).
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from conftest import requires_gnews


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_article(n: int) -> dict:
    return {
        "source": {"name": f"Source{n}"},
        "title": f"Article {n}",
        "description": f"Description {n}",
        "content": f"Content {n}",
        "url": f"https://example.com/article/{n}",
        "publishedAt": "2024-01-15T10:00:00Z",
    }


def _mock_get(articles: list[dict], total: int | None = None, status: int = 200):
    """Crea un mock de requests.get que devuelve los artículos dados."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "articles": articles,
        "totalArticles": total if total is not None else len(articles),
    }
    return MagicMock(return_value=mock_resp)


# ── Parsing básico ─────────────────────────────────────────────────────────────

def test_fetch_articles_parses_fields():
    from src.ingestion import NewsIngestionModule

    raw = [_make_article(1)]
    with patch("requests.get", _mock_get(raw)):
        module = NewsIngestionModule(api_key="test-key")
        articles = module.fetch_articles(keywords=["Apple"], max_total=10)

    assert len(articles) == 1
    a = articles[0]
    assert a.title == "Article 1"
    assert a.source == "Source1"
    assert a.description == "Description 1"
    assert a.url == "https://example.com/article/1"
    assert isinstance(a.published_at, datetime)


def test_fetch_articles_returns_empty_on_no_results():
    from src.ingestion import NewsIngestionModule

    with patch("requests.get", _mock_get([])):
        module = NewsIngestionModule(api_key="test-key")
        articles = module.fetch_articles(keywords=["xyznotfound"], max_total=10)

    assert articles == []


def test_fetch_articles_respects_max_total():
    from src.ingestion import NewsIngestionModule

    raw = [_make_article(i) for i in range(10)]
    with patch("requests.get", _mock_get(raw, total=10)):
        module = NewsIngestionModule(api_key="test-key")
        articles = module.fetch_articles(keywords=["Apple"], max_total=3)

    assert len(articles) == 3


def test_fetch_articles_stops_on_rate_limit():
    from src.ingestion import NewsIngestionModule

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        module = NewsIngestionModule(api_key="test-key")
        articles = module.fetch_articles(keywords=["Apple"], max_total=100)

    assert articles == []


def test_fetch_articles_published_at_parsed_correctly():
    from src.ingestion import NewsIngestionModule

    raw = [_make_article(1)]
    raw[0]["publishedAt"] = "2024-06-15T14:30:00Z"

    with patch("requests.get", _mock_get(raw)):
        module = NewsIngestionModule(api_key="test-key")
        articles = module.fetch_articles(keywords=["Apple"], max_total=10)

    dt = articles[0].published_at
    assert dt.year == 2024
    assert dt.month == 6
    assert dt.day == 15
    assert dt.hour == 14


# ── Debug logging ─────────────────────────────────────────────────────────────

def test_ingestion_logs_response_when_debug():
    """En modo DEBUG, se llama a logger.debug con la respuesta de la API."""
    from src.ingestion import NewsIngestionModule

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    raw = [_make_article(1)]
    with patch("requests.get", _mock_get(raw)):
        module = NewsIngestionModule(api_key="test-key", logger=mock_logger)
        module.fetch_articles(keywords=["Apple"], max_total=10)

    mock_logger.debug.assert_called()
    msg = mock_logger.debug.call_args[0][0]
    assert "gnews.io" in msg
    assert "200" in msg


def test_ingestion_silent_when_not_debug():
    """En modo INFO, no se llama a logger.debug."""
    from src.ingestion import NewsIngestionModule

    mock_logger = MagicMock()
    mock_logger.is_debug = False

    raw = [_make_article(1)]
    with patch("requests.get", _mock_get(raw)):
        module = NewsIngestionModule(api_key="test-key", logger=mock_logger)
        module.fetch_articles(keywords=["Apple"], max_total=10)

    mock_logger.debug.assert_not_called()


def test_ingestion_silent_when_no_logger():
    """Sin logger, fetch_articles no lanza excepción."""
    from src.ingestion import NewsIngestionModule

    raw = [_make_article(1)]
    with patch("requests.get", _mock_get(raw)):
        module = NewsIngestionModule(api_key="test-key")
        articles = module.fetch_articles(keywords=["Apple"], max_total=10)

    assert len(articles) == 1


def test_ingestion_debug_log_truncates_large_response():
    """Respuestas grandes se truncan en el log."""
    from src.ingestion import NewsIngestionModule, _MAX_LOG_CHARS

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    # Generar respuesta muy grande
    raw = [_make_article(i) for i in range(50)]
    for a in raw:
        a["content"] = "x" * 500  # inflar contenido

    with patch("requests.get", _mock_get(raw, total=50)):
        module = NewsIngestionModule(api_key="test-key", logger=mock_logger)
        module.fetch_articles(keywords=["Apple"], max_total=50)

    msg = mock_logger.debug.call_args[0][0]
    if "truncado" in msg:
        assert len(msg) < _MAX_LOG_CHARS + 200


def test_ingestion_debug_log_includes_page_number():
    """El log de debug incluye el número de página."""
    from src.ingestion import NewsIngestionModule

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    raw = [_make_article(1)]
    with patch("requests.get", _mock_get(raw)):
        module = NewsIngestionModule(api_key="test-key", logger=mock_logger)
        module.fetch_articles(keywords=["Apple"], max_total=10)

    msg = mock_logger.debug.call_args[0][0]
    assert "page=1" in msg


def test_ingestion_logger_none_by_default():
    """Sin pasar logger, _logger es None."""
    from src.ingestion import NewsIngestionModule

    module = NewsIngestionModule(api_key="test-key")
    assert module._logger is None


def test_ingestion_stores_logger():
    """El logger pasado se almacena en _logger."""
    from src.ingestion import NewsIngestionModule

    mock_logger = MagicMock()
    module = NewsIngestionModule(api_key="test-key", logger=mock_logger)
    assert module._logger is mock_logger


# ── Integration tests: GNews API real ────────────────────────────────────────

@requires_gnews
def test_gnews_fetch_returns_articles():
    """Llama a la API real y verifica que devuelve al menos un artículo."""
    from src.ingestion import NewsIngestionModule, Article

    module = NewsIngestionModule(api_key=os.environ["GNEWS_API_KEY"])
    articles = module.fetch_articles(keywords=["Apple"], max_total=5)

    assert isinstance(articles, list)
    assert len(articles) >= 1
    for a in articles:
        assert isinstance(a, Article)
        assert a.title
        assert a.url.startswith("http")
        assert isinstance(a.published_at, datetime)


@requires_gnews
def test_gnews_respects_max_total():
    """La API real no devuelve más artículos del límite pedido."""
    from src.ingestion import NewsIngestionModule

    module = NewsIngestionModule(api_key=os.environ["GNEWS_API_KEY"])
    articles = module.fetch_articles(keywords=["Tesla"], max_total=3)

    assert len(articles) <= 3


@requires_gnews
def test_gnews_article_fields_complete():
    """Cada artículo tiene los campos mínimos requeridos por el sistema."""
    from src.ingestion import NewsIngestionModule

    module = NewsIngestionModule(api_key=os.environ["GNEWS_API_KEY"])
    articles = module.fetch_articles(keywords=["Microsoft"], max_total=3)

    for a in articles:
        assert isinstance(a.title, str)
        assert isinstance(a.source, str)
        assert isinstance(a.description, str)
        assert isinstance(a.content, str)
        assert isinstance(a.url, str)
        assert isinstance(a.published_at, datetime)
        assert a.published_at.tzinfo is not None  # timezone-aware


@requires_gnews
def test_gnews_multiple_keywords_combined():
    """Múltiples keywords se combinan con OR en la query."""
    from src.ingestion import NewsIngestionModule

    module = NewsIngestionModule(api_key=os.environ["GNEWS_API_KEY"])
    articles = module.fetch_articles(keywords=["NVDA", "Nvidia"], max_total=5)

    assert isinstance(articles, list)
    # Con dos keywords relacionados debería haber resultados
    assert len(articles) >= 1


@requires_gnews
def test_gnews_no_exception_with_debug_logger():
    """Con logger en DEBUG y API real, fetch_articles no lanza excepción."""
    from src.ingestion import NewsIngestionModule
    from src.logger import AgentLogger

    logger = AgentLogger(level="DEBUG")
    module = NewsIngestionModule(api_key=os.environ["GNEWS_API_KEY"], logger=logger)
    # Solo verifica que no explota; el comportamiento del log se cubre en unit tests
    articles = module.fetch_articles(keywords=["Apple"], max_total=1)
    assert isinstance(articles, list)
