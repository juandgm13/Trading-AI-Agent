"""Tool: busca noticias recientes en GNews para un ticker o empresa."""
import json
from agent_framework.tools.base_tool import BaseTool
from src.ingestion import NewsIngestionModule


class SearchNewsTool(BaseTool):
    name = "search_news"
    description = (
        "Search recent financial news for a specific stock symbol or company name. "
        "Returns titles, descriptions and publication dates of recent articles."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Company name or stock ticker to search (e.g. 'Apple' or 'AAPL')",
            },
        },
        "required": ["query"],
    }

    def __init__(self, gnews_api_key: str):
        self._ingestion = NewsIngestionModule(api_key=gnews_api_key)

    async def execute(self, query: str) -> str:
        articles = self._ingestion.fetch_articles(keywords=[query], max_total=10)

        if not articles:
            return json.dumps({"query": query, "articles": [], "count": 0})

        return json.dumps({
            "query": query,
            "count": len(articles),
            "articles": [
                {
                    "title": a.title,
                    "description": a.description,
                    "source": a.source,
                    "published_at": a.published_at.isoformat(),
                }
                for a in articles[:10]
            ],
        })
