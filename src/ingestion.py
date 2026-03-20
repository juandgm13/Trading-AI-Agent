import json
import requests
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    source: str
    title: str
    description: str
    content: str
    url: str
    published_at: datetime
    feed_group: str = "unknown"  # "stocks"|"macro"|"alpaca"|"yahoo_symbol"


_MAX_LOG_CHARS = 4000


class NewsIngestionModule:
    BASE_URL = "https://gnews.io/api/v4/search"

    def __init__(self, api_key: str, logger=None):
        self.api_key = api_key
        self._logger = logger

    MAX_PER_PAGE = 100
    MAX_TOTAL = 1000

    def fetch_articles(
        self,
        keywords: list[str],
        language: str = "en",
        max_total: int = MAX_TOTAL,
    ) -> list[Article]:
        query = " OR ".join(keywords)
        per_page = min(self.MAX_PER_PAGE, max_total)
        articles = []
        page = 1

        while len(articles) < max_total:
            params = {
                "q": query,
                "lang": language,
                "max": per_page,
                "page": page,
                "apikey": self.api_key,
            }

            response = requests.get(self.BASE_URL, params=params, timeout=10)
            if response.status_code == 429:
                print(f"[ingestion] Rate limit reached after page {page - 1}, stopping pagination.")
                break
            response.raise_for_status()
            data = response.json()
            if self._logger is not None and self._logger.is_debug:
                raw = json.dumps(data, ensure_ascii=False)
                if len(raw) > _MAX_LOG_CHARS:
                    raw = raw[:_MAX_LOG_CHARS] + f"… [truncado {len(raw)} chars]"
                self._logger.debug(
                    f"GET {self.BASE_URL} page={page} → {response.status_code} | {raw}",
                    source="gnews",
                )

            batch = data.get("articles", [])
            if not batch:
                break

            for item in batch:
                articles.append(
                    Article(
                        source=item.get("source", {}).get("name", ""),
                        title=item.get("title", ""),
                        description=item.get("description", ""),
                        content=item.get("content", ""),
                        url=item.get("url", ""),
                        published_at=datetime.fromisoformat(
                            item.get("publishedAt", "").replace("Z", "+00:00")
                        ),
                    )
                )

            total_available = data.get("totalArticles", 0)
            if len(articles) >= total_available:
                break

            page += 1

        return articles[:max_total]
