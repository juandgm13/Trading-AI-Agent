"""Tools: obtiene y clasifica noticias de mercado y de empresa desde la BD + fuentes externas."""
import json
from agent_framework.tools.base_tool import BaseTool


class FetchMarketNewsTool(BaseTool):
    name = "fetch_market_news"
    description = (
        "Fetch recent general market news (macro, Fed, indices, geopolitics). "
        "Triggers a fresh ingestion from all news sources, then returns classified "
        "articles from the database. Use this before analysing overall market sentiment."
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of articles to return (default: 20)",
                "default": 20,
            },
            "min_impact": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
                "description": "Minimum impact level to include. MEDIUM filters out routine news.",
                "default": "MEDIUM",
            },
        },
        "required": [],
    }

    def __init__(self, news_service, storage):
        self._news_service = news_service
        self._storage = storage

    async def execute(self, limit: int = 20, min_impact: str = "MEDIUM") -> str:
        # Trigger fresh ingestion (deduplication is handled internally)
        try:
            fetched, classified = await self._news_service.run_market_ingestion()
        except Exception as e:
            # Don't fail — return whatever is in the DB already
            fetched, classified = 0, 0

        articles = self._storage.get_articles_by_scope(
            "market", limit=limit, min_impact=min_impact
        )

        return json.dumps({
            "source": "market",
            "fetched_new": fetched,
            "classified_new": classified,
            "articles_returned": len(articles),
            "articles": [
                {
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "published_at": a.get("published_at", ""),
                    "impact": a.get("impact"),
                    "scope": a.get("scope"),
                    "summary": a.get("article_summary") or a.get("description", "")[:200],
                    "reasoning": a.get("reasoning", ""),
                }
                for a in articles
            ],
        })


class FetchCompanyNewsTool(BaseTool):
    name = "fetch_company_news"
    description = (
        "Fetch recent news for a specific stock symbol. "
        "Triggers a fresh ingestion from Yahoo Finance and Alpaca News for the symbol, "
        "then returns classified company-specific articles from the database."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
            "exchange": {
                "type": "string",
                "description": "Exchange name (e.g. NASDAQ, NYSE) — used to include exchange-level news",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of articles to return (default: 15)",
                "default": 15,
            },
        },
        "required": ["symbol"],
    }

    def __init__(self, news_service, storage):
        self._news_service = news_service
        self._storage = storage

    async def execute(self, symbol: str, exchange: str | None = None, limit: int = 15) -> str:
        symbol = symbol.upper().strip()

        # Trigger fresh fetch + classify + persist for this symbol
        try:
            articles = await self._news_service.run_symbol_ingestion(symbol, exchange=exchange)
        except Exception as e:
            # Fall back to DB only
            articles = self._storage.get_articles_by_scope("company", ticker=symbol, limit=limit)

        # Trim to limit
        articles = articles[:limit]

        return json.dumps({
            "symbol": symbol,
            "articles_returned": len(articles),
            "articles": [
                {
                    "title": a.get("title", ""),
                    "source": a.get("source", ""),
                    "published_at": a.get("published_at", ""),
                    "impact": a.get("impact"),
                    "scope": a.get("scope"),
                    "summary": a.get("article_summary") or a.get("description", "")[:200],
                    "reasoning": a.get("reasoning", ""),
                }
                for a in articles
            ],
        })
