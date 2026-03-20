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
        max_concurrent_classifications: int = 5,
    ):
        self._aggregator = aggregator
        self._classifier = classifier_agent
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

        classified_count = await self._classify_and_persist(unique_new)

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
        Clasifica una lista de artículos en paralelo y persiste los nuevos.
        Retorna el número de artículos clasificados exitosamente.
        """
        if not articles:
            return 0

        pairs = await self._classify_batch(articles)
        classified = 0
        for article, classification in pairs:
            saved = self._storage.save_article(article, classification)
            if saved:
                classified += 1
        return classified

    async def _classify_batch(
        self, articles: list[Article]
    ) -> list[tuple[Article, dict | None]]:
        """Clasifica N artículos en paralelo con semáforo max_concurrent."""
        sem = asyncio.Semaphore(self._max_concurrent)

        async def _classify_one(article: Article) -> tuple[Article, dict | None]:
            async with sem:
                classification = await self._classify_article(article)
            return article, classification

        return list(await asyncio.gather(*[_classify_one(a) for a in articles]))

    async def _classify_article(self, article: Article) -> dict | None:
        """
        Clasifica un artículo individual con el NewsClassifierAgent.
        Retorna None si el agente falla o la respuesta no es parseable.
        """
        from agents.economics_agent import build_news_classifier_prompt
        from src.response_parser import validate_news_classification, ParseError

        try:
            prompt = build_news_classifier_prompt(article)
            response = await self._classifier.run(prompt)
            return validate_news_classification(response)
        except ParseError as exc:
            if self._logger:
                self._logger.warning(
                    f"ParseError clasificando '{article.title[:60]}': {exc}",
                    source="news_service",
                )
            return None
        except Exception as exc:
            if self._logger:
                self._logger.error(
                    f"Error clasificando artículo: {exc}",
                    source="news_service",
                    exc_info=True,
                )
            return None
