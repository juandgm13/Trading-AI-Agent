import os
from dotenv import load_dotenv

from src.ingestion import NewsIngestionModule
from src.llm import LLMAnalysisModule
from src.storage import StorageLayer

load_dotenv()

KEYWORDS = ["stock market", "earnings", "Federal Reserve", "IPO", "merger", "acquisition"]
LANGUAGE = "en"


def main():
    api_key = os.environ["GNEWS_API_KEY"]

    ingestion = NewsIngestionModule(api_key=api_key)
    storage = StorageLayer(db_path="trading_agent.db")
    llm = LLMAnalysisModule()

    print(f"Fetching articles for: {KEYWORDS}")
    articles = ingestion.fetch_articles(keywords=KEYWORDS, language=LANGUAGE)
    print(f"Fetched {len(articles)} articles\n")

    counts = {"new": 0, "retry": 0, "analyzed": 0, "skipped": 0, "errors": 0}

    for article in articles:
        result = storage.check_and_mark(article)

        if not result.is_new and result.is_analyzed:
            print(f"[SKIP] {article.title}")
            counts["skipped"] += 1
            continue

        status = "NEW" if result.is_new else "RETRY"
        print(f"[{status}] {article.title}")
        counts["new" if result.is_new else "retry"] += 1

        try:
            llm_output = llm.analyze(title=article.title, content=article.content)
            print(
                f"  Company: {llm_output.company} ({llm_output.ticker})"
                f" | {llm_output.impact_level} {llm_output.impact_type}"
                f" | confidence: {llm_output.confidence}%"
            )
            storage.save_analysis(article, llm_output.__dict__, {"sent": False})
            counts["analyzed"] += 1
        except (ValueError, RuntimeError) as e:
            print(f"  [llm error] {e}")
            counts["errors"] += 1

    print(
        f"\nSummary: {counts['new']} new | {counts['retry']} retried"
        f" | {counts['analyzed']} analyzed | {counts['skipped']} skipped | {counts['errors']} errors"
    )


if __name__ == "__main__":
    main()
