"""
Tests de integración para las fuentes de noticias (requieren red real).

Verifican:
  1. Que las APIs devuelven artículos con el formato esperado (Article dataclass)
  2. Que los campos obligatorios están presentes y tienen los tipos correctos
  3. Que las URLs de los artículos son HTTP/HTTPS válidas y accesibles

Para ejecutarlos:
    pytest tests/test_news_sources_integration.py -v

Se saltan automáticamente si no hay internet o credenciales Alpaca.
"""
import os
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import aiohttp
import pytest

from src.ingestion import Article
from src.news_sources import AlpacaNewsSource, RSSNewsSource, YahooSymbolSource

try:
    from conftest import requires_alpaca, requires_network
except ImportError:
    from tests.conftest import requires_alpaca, requires_network


# ── Helpers ──────────────────────────────────────────────────────────────────

VALID_FEED_GROUPS = {
    "alpaca",
    # RSS stocks
    "Reuters", "CNBC", "Yahoo Finance", "MarketWatch",
    # RSS macro
    "Federal Reserve", "Financial Times", "The Economist",
}


def _is_valid_http_url(url: str) -> bool:
    """Comprueba que la URL tiene esquema http/https y un dominio."""
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc) and bool(p.path)
    except Exception:
        return False


def _assert_article(article: Article, expected_feed_group: str | None = None):
    """Valida que un Article tiene el formato completo y correcto."""
    assert isinstance(article, Article), f"Expected Article, got {type(article)}"

    # URL válida
    assert article.url, "url debe ser no vacío"
    assert _is_valid_http_url(article.url), (
        f"URL inválida: {article.url!r} — debe empezar por http/https y tener dominio"
    )

    # Título no vacío
    assert article.title, f"title vacío en artículo de {article.url}"

    # Fecha con timezone
    assert isinstance(article.published_at, datetime), "published_at debe ser datetime"
    assert article.published_at.tzinfo is not None, (
        f"published_at debe ser timezone-aware (got naive datetime) para {article.url}"
    )

    # La fecha no debe ser absurda (entre 2000 y 5 años en el futuro)
    min_date = datetime(2000, 1, 1, tzinfo=timezone.utc)
    max_date = datetime.now(timezone.utc) + timedelta(days=365 * 5)
    assert min_date <= article.published_at <= max_date, (
        f"published_at fuera de rango: {article.published_at}"
    )

    # source no vacío
    assert article.source, f"source vacío para {article.url}"

    # feed_group conocido
    assert article.feed_group in VALID_FEED_GROUPS, (
        f"feed_group desconocido: {article.feed_group!r} — esperados: {VALID_FEED_GROUPS}"
    )

    # feed_group esperado (si se indica)
    if expected_feed_group is not None:
        assert article.feed_group == expected_feed_group, (
            f"feed_group esperado {expected_feed_group!r}, got {article.feed_group!r}"
        )


async def _url_is_reachable(url: str, timeout: float = 6.0) -> bool:
    """
    HEAD request a la URL. Acepta cualquier respuesta HTTP (incluso 4xx/redirect)
    como señal de que el servidor existe y la URL es alcanzable.
    Solo falla si hay error de conexión/DNS.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as resp:
                return resp.status < 600  # cualquier respuesta HTTP válida
    except Exception:
        return False


# ── Alpaca News ───────────────────────────────────────────────────────────────

@requires_alpaca
@pytest.mark.asyncio
async def test_alpaca_news_general_returns_articles():
    """Alpaca News sin filtro devuelve artículos con formato correcto."""
    source = AlpacaNewsSource(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    articles = await source.fetch(limit=10)

    assert isinstance(articles, list), "fetch() debe devolver una lista"
    assert len(articles) > 0, "Alpaca News debería devolver al menos 1 artículo general"

    for article in articles:
        _assert_article(article, expected_feed_group="alpaca")


@requires_alpaca
@pytest.mark.asyncio
async def test_alpaca_news_by_symbol_returns_articles():
    """Alpaca News filtrado por símbolo devuelve artículos para ese símbolo."""
    source = AlpacaNewsSource(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    articles = await source.fetch(symbols=["AAPL"], limit=10)

    assert isinstance(articles, list)
    # AAPL es tan activo que casi siempre tiene noticias en los últimos 3 días
    # Si no hay artículos, el test pasa igual (posible en festivos)
    for article in articles:
        _assert_article(article, expected_feed_group="alpaca")


@requires_alpaca
@pytest.mark.asyncio
async def test_alpaca_news_urls_are_reachable():
    """Las URLs de los primeros artículos de Alpaca son HTTP/HTTPS válidas y accesibles."""
    source = AlpacaNewsSource(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )
    articles = await source.fetch(limit=5)

    if not articles:
        pytest.skip("No hay artículos Alpaca disponibles ahora mismo")

    # Comprobar las primeras 3 URLs (máximo) para no tardar demasiado
    sample = articles[:3]
    results = await asyncio.gather(*[_url_is_reachable(a.url) for a in sample])

    unreachable = [
        articles[i].url for i, ok in enumerate(results) if not ok
    ]
    assert not unreachable, (
        f"Las siguientes URLs de Alpaca no son alcanzables:\n" +
        "\n".join(f"  - {u}" for u in unreachable)
    )


# ── RSS Feeds ─────────────────────────────────────────────────────────────────

@requires_network
@pytest.mark.asyncio
async def test_rss_stocks_returns_articles():
    """El grupo 'stocks' de RSS devuelve artículos con formato correcto."""
    source = RSSNewsSource()
    articles = await source.fetch(feed_groups=("stocks",))

    assert isinstance(articles, list)
    assert len(articles) > 0, (
        "Los feeds RSS de stocks (Reuters/CNBC/Yahoo/MarketWatch) deben devolver artículos"
    )
    for article in articles:
        _assert_article(article)


@requires_network
@pytest.mark.asyncio
async def test_rss_macro_returns_articles():
    """El grupo 'macro' de RSS devuelve artículos con formato correcto."""
    source = RSSNewsSource()
    articles = await source.fetch(feed_groups=("macro",))

    assert isinstance(articles, list)
    assert len(articles) > 0, (
        "Los feeds RSS macro (Fed Reserve/FT/Economist) deben devolver artículos"
    )
    for article in articles:
        _assert_article(article)


@requires_network
@pytest.mark.asyncio
async def test_rss_article_urls_are_valid_format():
    """Todos los artículos RSS tienen URLs bien formadas (http/https con dominio)."""
    source = RSSNewsSource()
    articles = await source.fetch(feed_groups=("stocks", "macro"))

    assert len(articles) > 0, "Se necesitan artículos para validar URLs"

    invalid = [
        (a.url, a.source)
        for a in articles
        if not _is_valid_http_url(a.url)
    ]
    assert not invalid, (
        "Artículos con URLs inválidas:\n" +
        "\n".join(f"  [{src}] {url!r}" for url, src in invalid)
    )


@requires_network
@pytest.mark.asyncio
async def test_rss_individual_feeds_format():
    """Cada feed RSS individual produce artículos con el formato correcto."""
    source = RSSNewsSource()

    all_feeds = source.FEEDS_STOCKS + source.FEEDS_MACRO
    loop = asyncio.get_event_loop()

    successes = []
    for feed_name, feed_url, feed_group in [
        (src, url, "stocks") for src, url in source.FEEDS_STOCKS
    ] + [
        (src, url, "macro") for src, url in source.FEEDS_MACRO
    ]:
        articles = await source._fetch_feed(loop, feed_name, feed_url, feed_group)
        if articles:
            # Al menos 1 artículo por feed que responde
            for a in articles[:5]:
                _assert_article(a, expected_feed_group=feed_name)
            successes.append(feed_name)

    assert len(successes) >= 2, (
        f"Al menos 2 feeds RSS deberían responder, solo respondieron: {successes}"
    )


@requires_network
@pytest.mark.asyncio
async def test_rss_stocks_urls_are_reachable():
    """Una muestra de URLs de RSS stocks son accesibles."""
    source = RSSNewsSource()
    articles = await source.fetch(feed_groups=("stocks",))

    if not articles:
        pytest.skip("No hay artículos RSS de stocks disponibles")

    # Tomar 1 URL por feed para no sobrecargar
    seen_domains: set[str] = set()
    sample: list[str] = []
    for a in articles:
        domain = urlparse(a.url).netloc
        if domain not in seen_domains:
            seen_domains.add(domain)
            sample.append(a.url)
        if len(sample) >= 4:
            break

    results = await asyncio.gather(*[_url_is_reachable(u) for u in sample])
    unreachable = [sample[i] for i, ok in enumerate(results) if not ok]

    # Permitir hasta 1 fallo (algunos sitios bloquean HEAD o tienen paywall)
    assert len(unreachable) <= 1, (
        f"Demasiadas URLs RSS inaccesibles ({len(unreachable)}/{len(sample)}):\n" +
        "\n".join(f"  - {u}" for u in unreachable)
    )


# ── Yahoo Finance RSS ─────────────────────────────────────────────────────────

@requires_network
@pytest.mark.asyncio
async def test_yahoo_symbol_returns_articles():
    """Yahoo Finance RSS para AAPL devuelve artículos con formato correcto."""
    source = YahooSymbolSource()
    articles = await source.fetch("AAPL")

    assert isinstance(articles, list)
    assert len(articles) > 0, "Yahoo Finance RSS para AAPL debería tener artículos"

    for article in articles:
        _assert_article(article, expected_feed_group="Yahoo Finance")
        assert article.source == "Yahoo Finance"


@requires_network
@pytest.mark.asyncio
async def test_yahoo_symbol_urls_are_valid_format():
    """Todos los artículos de Yahoo tienen URLs bien formadas."""
    source = YahooSymbolSource()
    articles = await source.fetch("MSFT")

    assert len(articles) > 0

    invalid = [a.url for a in articles if not _is_valid_http_url(a.url)]
    assert not invalid, (
        "URLs inválidas en Yahoo Finance:\n" + "\n".join(f"  {u!r}" for u in invalid)
    )


@requires_network
@pytest.mark.asyncio
async def test_yahoo_symbol_urls_have_public_domains():
    """
    Las URLs de Yahoo Finance tienen dominios públicos (no localhost, no IPs privadas).

    Yahoo Finance bloquea HEAD requests de bots, así que en lugar de verificar
    accesibilidad HTTP verificamos que las URLs sean externos y bien formados.
    """
    source = YahooSymbolSource()
    articles = await source.fetch("AAPL")

    if not articles:
        pytest.skip("No hay artículos Yahoo disponibles ahora mismo")

    bad_urls: list[str] = []
    for article in articles:
        parsed = urlparse(article.url)
        netloc = parsed.netloc.lower().split(":")[0]  # quitar puerto si existe

        # Rechazar URLs locales o sin dominio real
        is_bad = (
            not netloc                         # sin dominio
            or netloc == "localhost"
            or netloc.startswith("127.")
            or netloc.startswith("192.168.")
            or netloc.startswith("10.")
            or "." not in netloc               # sin TLD (ej. "myserver")
        )
        if is_bad:
            bad_urls.append(article.url)

    assert not bad_urls, (
        "URLs con dominios inválidos o locales en Yahoo Finance:\n" +
        "\n".join(f"  - {u!r}" for u in bad_urls)
    )


@requires_network
@pytest.mark.asyncio
async def test_yahoo_symbol_published_at_is_recent():
    """Los artículos de Yahoo son recientes (últimos 30 días)."""
    source = YahooSymbolSource()
    articles = await source.fetch("AAPL")

    if not articles:
        pytest.skip("No hay artículos Yahoo disponibles")

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    old_articles = [a for a in articles if a.published_at < cutoff]

    # Permitir algún artículo antiguo pero no la mayoría
    assert len(old_articles) < len(articles) * 0.5, (
        f"Más de la mitad de los artículos de Yahoo son de hace más de 30 días: "
        f"{len(old_articles)}/{len(articles)}"
    )
