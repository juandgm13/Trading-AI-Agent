"""
NewsIngestionService — orquesta la obtención, clasificación y persistencia de noticias.

Flujo:
  1. Fetch de todas las fuentes (NewsAggregator)
  2. Deduplicación contra BD (por url / content_hash)
  3. Clasificación de artículos nuevos en paralelo (NewsClassifierAgent)
  4. Persistencia con expires_at calculado según impact
  5. Limpieza de artículos expirados

Uso en el monitor:
  service = NewsIngestionService(aggregator, classifier_agent, storage, logger)
  fetched, classified = await service.run_market_ingestion()
  articles = await service.run_symbol_ingestion("AAPL", exchange="NASDAQ")
"""
import asyncio
import json

from src.ingestion import Article
from src.storage import StorageLayer


class NewsIngestionService:
    def __init__(
        self,
        aggregator,  # NewsAggregator
        classifier_agent,  # NewsClassifierAgent (agent_framework agent)
        storage: StorageLayer,
        logger=None,
        max_concurrent_classifications: int = 1,
        full_content_classifier_agent=None,  # Agente con prompt extendido para second-pass
    ):
        self._aggregator = aggregator
        self._classifier = classifier_agent
        self._full_content_classifier = full_content_classifier_agent
        self._storage = storage
        self._logger = logger
        self._max_concurrent = max_concurrent_classifications

    async def run_market_ingestion(self) -> tuple[int, int]:
        """
        Fetch noticias de mercado general (RSS + Alpaca general).
        Clasifica artículos nuevos en paralelo.
        Persiste resultados y limpia artículos expirados.

        Retorna (nuevos_fetched, clasificados).
        """
        articles = await self._aggregator.fetch_market_news()

        new_articles = [a for a in articles if a.url]
        # Deduplicar por url antes de clasificar
        seen: set[str] = set()
        unique_new: list[Article] = []
        for article in new_articles:
            if article.url not in seen:
                seen.add(article.url)
                unique_new.append(article)

        # Omitir artículos ya clasificados en BD (scope IS NOT NULL) para evitar llamadas LLM innecesarias
        already_classified = self._storage.get_classified_article_urls([a.url for a in unique_new])
        to_classify = [a for a in unique_new if a.url not in already_classified]

        classified_count = await self._classify_and_persist(to_classify)

        expired = self._storage.expire_old_articles()
        if self._logger and expired > 0:
            self._logger.info(
                f"Limpieza: {expired} artículos expirados eliminados", source="news_service"
            )

        if self._logger:
            self._logger.info(
                f"run_market_ingestion: {len(unique_new)} artículos únicos fetched, "
                f"{classified_count} clasificados",
                source="news_service",
            )
        return len(unique_new), classified_count

    async def run_symbol_ingestion(
        self,
        symbol: str,
        exchange: str | None = None,
    ) -> list[dict]:
        """
        Para OPPORTUNITY_FOUND:
        1. Lee artículos clasificados de BD para el símbolo (scope=company)
        2. Lee artículos clasificados de BD para el exchange del símbolo (scope=exchange)
        3. Fetch fresco de Yahoo Finance + Alpaca por símbolo
        4. Clasifica y persiste los artículos nuevos
        5. Retorna todos los artículos relevantes (BD + nuevos)

        Retorna lista de dicts listos para inyectar en build_company_analysis_prompt().
        """
        # 1+2: artículos ya clasificados en BD
        company_articles = self._storage.get_articles_by_scope("company", ticker=symbol.upper())
        exchange_articles: list[dict] = []
        if exchange:
            exchange_articles = self._storage.get_articles_by_scope(
                "exchange", exchange=exchange.upper()
            )

        # URLs ya presentes en BD (para evitar refetch)
        stored_urls = {a["url"] for a in company_articles + exchange_articles if a.get("url")}

        # 3: fetch fresco de Yahoo RSS + Alpaca por símbolo
        fresh_articles = await self._aggregator.fetch_symbol_news(symbol, stored_urls=stored_urls)

        # 4: clasificar y persistir los nuevos
        if fresh_articles:
            await self._classify_and_persist(fresh_articles)

        # 5: leer de nuevo los de empresa (ahora incluye los recién clasificados)
        fresh_company = self._storage.get_articles_by_scope("company", ticker=symbol.upper())
        fresh_exchange: list[dict] = []
        if exchange:
            fresh_exchange = self._storage.get_articles_by_scope(
                "exchange", exchange=exchange.upper()
            )

        all_articles = fresh_company + fresh_exchange

        if self._logger and self._logger.is_debug:
            self._logger.debug(
                f"run_symbol_ingestion({symbol}): {len(all_articles)} artículos relevantes "
                f"(company={len(fresh_company)}, exchange={len(fresh_exchange)})",
                source="news_service",
            )
        return all_articles

    async def _classify_and_persist(self, articles: list[Article]) -> int:
        """
        Clasifica artículos en paralelo y persiste cada uno en BD inmediatamente al clasificarlo.
        Retorna el número de artículos guardados exitosamente.
        """
        if not articles:
            return 0

        sem = asyncio.Semaphore(self._max_concurrent)

        async def _process_one(article: Article) -> bool:
            async with sem:
                classification = await self._classify_article(article)
            return self._storage.save_article(article, classification)

        results = await asyncio.gather(*[_process_one(a) for a in articles])
        return sum(1 for r in results if r)

    async def _classify_article(self, article: Article) -> dict | None:
        """
        Clasifica un artículo en uno o dos pasos:

        1. Primera pasada (title + description): clasificación rápida.
        2. Si scope != 'irrelevant' y hay full_content_classifier disponible:
           - Fetcha el texto completo del artículo desde su URL.
           - Segunda pasada (full_content): reclasifica con mayor contexto
             y genera article_summary.
        """
        from agents.economics_agent import build_news_classifier_prompt
        from src.response_parser import validate_news_classification, ParseError

        if self._logger:
            self._logger.debug(
                f"Clasificando: '{article.title[:80]}'", source="news_service"
            )

        # ── Primera pasada (solo title + description) ─────────────────────────
        classification = await self._run_classification(
            self._classifier,
            build_news_classifier_prompt(article, full_content=False),
            article,
        )
        if classification is None:
            return None

        # ── Segunda pasada (texto completo + article_summary) ─────────────────
        if (
            classification.get("scope") != "irrelevant"
            and self._full_content_classifier is not None
            and article.url
        ):
            article.full_content = await self._fetch_full_content(article.url)
            if article.full_content:
                if self._logger:
                    self._logger.debug(
                        f"Reclasificando con texto completo: '{article.title[:60]}'",
                        source="news_service",
                    )
                second = await self._run_classification(
                    self._full_content_classifier,
                    build_news_classifier_prompt(article, full_content=True),
                    article,
                )
                if second is not None:
                    classification = second

        return classification

    async def _run_classification(self, agent, prompt: str, article: Article) -> dict | None:
        """Ejecuta una clasificación con reintentos. Retorna dict o None."""
        from src.response_parser import validate_news_classification, ParseError

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = await agent.run(prompt)
                return validate_news_classification(response)
            except ParseError as exc:
                if self._logger:
                    self._logger.warning(
                        f"ParseError clasificando '{article.title[:60]}': {exc}",
                        source="news_service",
                    )
                return None
            except Exception as exc:
                if attempt < max_attempts:
                    wait = attempt * 5
                    if self._logger:
                        self._logger.warning(
                            f"Error clasificando (intento {attempt}/{max_attempts}), "
                            f"reintentando en {wait}s: {exc}",
                            source="news_service",
                        )
                    await asyncio.sleep(wait)
                else:
                    if self._logger:
                        self._logger.error(
                            f"Error clasificando '{article.title[:60]}' tras "
                            f"{max_attempts} intentos: {exc}",
                            source="news_service",
                            exc_info=True,
                        )
                    return None

    async def _fetch_full_content(self, url: str) -> str:
        """Fetcha el texto completo de un artículo desde su URL."""
        import aiohttp
        from src.news_sources import fetch_article_content

        try:
            async with aiohttp.ClientSession() as session:
                return await fetch_article_content(url, session, logger=self._logger)
        except Exception:
            return ""
