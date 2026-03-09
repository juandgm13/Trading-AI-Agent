"""
Trading analysis pipeline using Agent Framework.

Replaces src/llm.py — uses AgentBuilder + OllamaProvider + EventBus
following the pattern from Agent-Framework.
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent_framework.config.config_loader import load_from_yaml
from agent_framework.core.agent_builder import AgentBuilder
from agent_framework.events import EventBus, EventType
from agent_framework.models.model_registry import ModelRegistry
from agent_framework.providers import OllamaProvider

from src.ingestion import NewsIngestionModule
from src.storage import StorageLayer

KEYWORDS = ["stock market", "earnings", "Federal Reserve", "IPO", "merger", "acquisition"]
LANGUAGE = "en"

VALID_IMPACT_LEVELS = {"LOW", "MEDIUM", "HIGH"}
VALID_IMPACT_TYPES = {"POSITIVE", "NEGATIVE", "NEUTRAL"}

# System prompt embedded in user message since SimplePlanner passes input directly
SYSTEM_PROMPT = (
    "You are a financial news analyst. "
    "Analyze the article and return ONLY a valid JSON object, no explanation.\n\n"
    "Rules for the 'company' field:\n"
    "- Use the company name when the news is primarily about a specific company.\n"
    "- Use 'MARKET' when the news affects the general market (e.g. Fed rate decisions, "
    "inflation data, GDP reports, stock index movements, global economic events).\n"
    "- Use 'SECTOR:<name>' when the news affects an entire sector but not a specific company "
    "(e.g. 'SECTOR:ENERGY', 'SECTOR:TECH', 'SECTOR:BANKING').\n"
    "- Use 'Unknown' only when the affected entity truly cannot be determined.\n\n"
    "Rules for the 'ticker' field:\n"
    "- Use the stock exchange ticker when company is a specific company.\n"
    "- Use 'N/A' when company is MARKET, SECTOR:*, or Unknown.\n\n"
    "Schema:\n"
    '{\n'
    '  "company": "<company name | MARKET | SECTOR:<name> | Unknown>",\n'
    '  "ticker": "<ticker or \'N/A\'>",\n'
    '  "impact_level": "<LOW|MEDIUM|HIGH>",\n'
    '  "impact_type": "<POSITIVE|NEGATIVE|NEUTRAL>",\n'
    '  "confidence": <integer 0-100>,\n'
    '  "reason": "<one sentence explaining the impact assessment>"\n'
    '}'
)


@dataclass
class LLMOutput:
    company: str
    ticker: str
    impact_level: str
    impact_type: str
    confidence: int
    reason: str


def _parse_llm_output(raw: str) -> LLMOutput:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"[agent] No JSON found in model output:\n{raw}")

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        raise ValueError(f"[agent] Invalid JSON in model output: {e}\nRaw output:\n{raw}") from e

    impact_level = str(data.get("impact_level", "")).upper()
    impact_type = str(data.get("impact_type", "")).upper()

    if impact_level not in VALID_IMPACT_LEVELS:
        raise ValueError(f"[agent] Invalid impact_level '{impact_level}'. Raw output:\n{raw}")
    if impact_type not in VALID_IMPACT_TYPES:
        raise ValueError(f"[agent] Invalid impact_type '{impact_type}'. Raw output:\n{raw}")

    return LLMOutput(
        company=str(data.get("company", "Unknown")),
        ticker=str(data.get("ticker", "N/A")),
        impact_level=impact_level,
        impact_type=impact_type,
        confidence=int(data.get("confidence", 0)),
        reason=str(data.get("reason", "")),
    )


async def run_pipeline(on_log=None) -> None:
    def log(msg: str):
        if on_log:
            on_log(msg)
        else:
            print(msg)

    api_key = os.environ.get("GNEWS_API_KEY")
    if not api_key:
        log("[error] GNEWS_API_KEY no encontrada en .env")
        return

    # Load YAML config
    config_path = Path(__file__).parent / "trading_agent.yaml"
    config = load_from_yaml(config_path)
    model_cfg = config.models["analyzer"]

    # Build model registry with OllamaProvider
    model_registry = ModelRegistry()
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model_registry.register("analyzer", OllamaProvider(model_cfg.model, base_url=ollama_url))
    log(f"[agent] Using Ollama model: {model_cfg.model}")

    # Setup EventBus with logging handlers
    bus = EventBus()

    async def on_event(event):
        if event.type == EventType.MODEL_CALLED:
            log(f"[agent] Calling model ({model_cfg.model})...")

    bus.subscribe("*", on_event)

    # Build agent using AgentBuilder (SimplePlanner — deterministic, no tool loop)
    agent = (
        AgentBuilder()
        .name("trading_analyzer")
        .planner("simple")
        .tools([])
        .model_role("analyzer")
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )

    # Ingestion + storage
    ingestion = NewsIngestionModule(api_key=api_key)
    storage = StorageLayer(db_path="trading_agent.db")

    log(f"Fetching articles for: {KEYWORDS}")
    articles = ingestion.fetch_articles(keywords=KEYWORDS, language=LANGUAGE)
    log(f"Fetched {len(articles)} articles\n")

    counts = {"new": 0, "retry": 0, "analyzed": 0, "skipped": 0, "errors": 0}

    for article in articles:
        result = storage.check_and_mark(article)

        if not result.is_new and result.is_analyzed:
            log(f"[SKIP] {article.title}")
            counts["skipped"] += 1
            continue

        status = "NEW" if result.is_new else "RETRY"
        log(f"[{status}] {article.title}")
        counts["new" if result.is_new else "retry"] += 1

        # Clear memory between articles so each analysis is independent
        await agent._context.memory.clear()

        try:
            prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"Title: {article.title}\n"
                f"Content: {article.content}"
            )
            raw = await agent.run(prompt)
            llm_output = _parse_llm_output(raw)
            log(
                f"  Company: {llm_output.company} ({llm_output.ticker})"
                f" | {llm_output.impact_level} {llm_output.impact_type}"
                f" | confidence: {llm_output.confidence}%"
            )
            storage.save_analysis(article, llm_output.__dict__, {"sent": False})
            counts["analyzed"] += 1
        except (ValueError, RuntimeError) as e:
            log(f"  [agent error] {e}")
            counts["errors"] += 1

    log(
        f"\nSummary: {counts['new']} new | {counts['retry']} retried"
        f" | {counts['analyzed']} analyzed | {counts['skipped']} skipped"
        f" | {counts['errors']} errors"
    )


def run_pipeline_sync(on_log=None) -> None:
    """Synchronous wrapper for Tkinter thread."""
    asyncio.run(run_pipeline(on_log=on_log))
