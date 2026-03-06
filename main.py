import os
from dotenv import load_dotenv

from src.ingestion import NewsIngestionModule
from src.storage import StorageLayer

load_dotenv()

KEYWORDS = ["stock market", "earnings", "Federal Reserve", "IPO", "merger", "acquisition"]
LANGUAGE = "en"


def main():
    api_key = os.environ["GNEWS_API_KEY"]

    ingestion = NewsIngestionModule(api_key=api_key)
    storage = StorageLayer(db_path="trading_agent.db")

    print(f"Fetching articles for: {KEYWORDS}")
    articles = ingestion.fetch_articles(keywords=KEYWORDS, language=LANGUAGE)
    print(f"Fetched {len(articles)} articles\n")

    new_count = 0
    for article in articles:
        result = storage.check_and_mark(article)
        status = "NEW" if result.is_new else "DUPLICATE"
        print(f"[{status}] {article.title}")
        if result.is_new:
            new_count += 1

    print(f"\nSummary: {new_count} new / {len(articles) - new_count} duplicates")


if __name__ == "__main__":
    main()
