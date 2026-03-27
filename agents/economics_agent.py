"""
EconomicsAgent — analiza noticias de mercado global y noticias específicas de empresa.

Dos agentes con prompts separados:
- MarketContextAgent: holistic macro view → { market_sentiment, risk_appetite, dominant_themes, macro_risks, confidence, summary }
- CompanyAnalysisAgent: company-specific risk → { sentiment, key_events, entry_risk, confidence, summary }

El agente recibe noticias pre-fetched por el monitor e inyectadas en el prompt.
"""
from agent_framework.core.agent_builder import AgentBuilder
from agent_framework.events import EventBus
from agent_framework.models.model_registry import ModelRegistry


ECONOMICS_SYSTEM_PROMPT = """\
You are an economics and financial news analyst.

You will receive recent news articles about a specific stock and general market news.
Analyze the sentiment, identify key events, and assess market context.
Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
  "market_context": "RISK_ON" | "RISK_OFF" | "NEUTRAL",
  "key_events": ["<brief description of key event>", ...],
  "confidence": <integer 0-100>,
  "summary": "<one paragraph explaining the overall assessment>"
}

Rules:
- sentiment reflects the company-specific news outlook.
- market_context reflects the broader market environment (RISK_ON = investors buying, RISK_OFF = selling).
- key_events: list of 1-5 most important events that could affect the stock price.
- confidence: how certain you are given the available information (low if news is sparse or mixed).
- Return NEUTRAL if news is absent or contradictory.\
"""


async def build_economics_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el EconomicsAgent (SimplePlanner, sin tools).

    El monitor pre-fetches las noticias e inyecta los datos en el prompt.
    """
    agent = (
        AgentBuilder()
        .name("economics_analysis")
        .planner("simple")
        .tools([])
        .model_role("economics")
        .system_prompt(ECONOMICS_SYSTEM_PROMPT)
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


MARKET_CONTEXT_SYSTEM_PROMPT = """\
You are a macroeconomic analyst specializing in global market conditions.

You will receive recent general market and financial news articles.
Synthesize them into a holistic assessment of the current market environment.
Do NOT analyze each article individually — focus on the overall picture.
Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "market_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "risk_appetite": "RISK_ON" | "RISK_OFF" | "NEUTRAL",
  "dominant_themes": ["<theme1>", "<theme2>", ...],
  "macro_risks": ["<risk1>", ...],
  "confidence": <integer 0-100>,
  "summary": "<one paragraph describing the current global market context>"
}

Rules:
- market_sentiment: overall equity market direction (BULLISH = broadly rising, BEARISH = broadly falling).
- risk_appetite: investor behavior (RISK_ON = buying equities, RISK_OFF = fleeing to safe assets).
- dominant_themes: 2-5 macro themes driving sentiment.
- macro_risks: 1-4 key risks that could cause broad market moves.
- Return NEUTRAL across fields if news is insufficient or contradictory.\
"""

COMPANY_ANALYSIS_SYSTEM_PROMPT = """\
You are a financial news analyst specializing in company-specific news.

You will receive recent news articles about a specific stock.
Analyze the company-specific news to assess the risk of opening a position now.
Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
  "key_events": ["<event1>", ...],
  "entry_risk": "LOW" | "MEDIUM" | "HIGH",
  "confidence": <integer 0-100>,
  "summary": "<2-3 sentences on company news relevant to entry timing>"
}

Rules:
- sentiment: how the news affects the near-term price outlook.
- key_events: 1-4 most important recent company events.
- entry_risk: LOW = news supports entering, MEDIUM = mixed signals, HIGH = news warns against entry.
- Return NEUTRAL / MEDIUM / low confidence if company news is absent or contradictory.\
"""


async def build_market_context_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el agente para el informe holístico de mercado global.
    Usa el mismo modelo role 'economics' con un system prompt especializado.
    """
    agent = (
        AgentBuilder()
        .name("market_context_analysis")
        .planner("simple")
        .tools([])
        .model_role("economics")
        .system_prompt(MARKET_CONTEXT_SYSTEM_PROMPT)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


async def build_company_analysis_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el agente para el informe breve de empresa al momento de entrada.
    Usa el mismo modelo role 'economics' con un system prompt especializado.
    """
    agent = (
        AgentBuilder()
        .name("company_analysis")
        .planner("simple")
        .tools([])
        .model_role("economics")
        .system_prompt(COMPANY_ANALYSIS_SYSTEM_PROMPT)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


_ARTICLE_PROMPT_FIELDS = frozenset([
    "title", "source", "published_at", "scope", "ticker",
    "impact", "article_summary", "reasoning",
])


def _clean_articles(articles: list[dict]) -> list[dict]:
    """Filtra cada artículo a solo los campos relevantes para el prompt."""
    return [
        {k: v for k, v in a.items() if k in _ARTICLE_PROMPT_FIELDS and v is not None}
        for a in articles
    ]


def build_market_context_prompt(market_news: list[dict]) -> str:
    """Prompt para el informe holístico de contexto global de mercado."""
    import json
    clean = _clean_articles(market_news)
    market_section = (
        json.dumps(clean, indent=2) if clean else "No market news available."
    )
    return (
        "Analyze the following general market and financial news articles "
        "to produce a holistic assessment of the current global market context.\n\n"
        f"--- General market news ---\n{market_section}\n\n"
        "Provide your macroeconomic context assessment."
    )


def build_company_analysis_prompt(symbol: str, company_news: list[dict]) -> str:
    """Prompt para el informe breve de empresa en el momento de intento de entrada."""
    import json
    clean = _clean_articles(company_news)
    company_section = (
        json.dumps(clean, indent=2) if clean else "No company news available."
    )
    return (
        f"Analyze the following company-specific news for {symbol} "
        f"to assess the risk of opening a position right now.\n\n"
        f"--- Company news for {symbol} ---\n{company_section}\n\n"
        "Provide your company-specific sentiment and entry risk assessment."
    )


NEWS_CLASSIFIER_SYSTEM_PROMPT = """\
You are a financial news classifier.

You will receive ONE news article (title, description, source, published date).
Classify it and return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "scope": "company" | "market" | "exchange" | "irrelevant",
  "ticker": "<SYMBOL>" | null,
  "exchange": "<EXCHANGE_NAME>" | null,
  "impact": "HIGH" | "MEDIUM" | "LOW" | null,
  "reasoning": "<one sentence>"
}

Rules:
- scope="company": news primarily affects one specific publicly-traded company → set ticker (e.g. "AAPL").
- scope="market": news affects the broad equity market (Fed decisions, macro data, geopolitics, indices).
- scope="exchange": news primarily affects one exchange or sector (e.g. NASDAQ tech selloff) → set exchange.
- scope="irrelevant": sports, entertainment, non-financial content, or clearly unrelated to financial markets.
- impact=null ONLY when scope="irrelevant".
- impact="HIGH": major earnings surprise, acquisition, regulatory action, Fed rate decision, market crash.
- impact="MEDIUM": analyst upgrades/downgrades, product launches, minor macro data releases.
- impact="LOW": routine news, small events with marginal financial relevance.\
"""

NEWS_CLASSIFIER_FULL_SYSTEM_PROMPT = """\
You are a financial news classifier and summarizer.

You will receive ONE news article with its title, description, source, published date, and full text.
Classify it and produce a concise summary. Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "scope": "company" | "market" | "exchange" | "irrelevant",
  "ticker": "<SYMBOL>" | null,
  "exchange": "<EXCHANGE_NAME>" | null,
  "impact": "HIGH" | "MEDIUM" | "LOW" | null,
  "reasoning": "<one sentence>",
  "article_summary": "<2-3 sentence summary of the key facts in the full article>" | null
}

Rules:
- scope="company": news primarily affects one specific publicly-traded company → set ticker (e.g. "AAPL").
- scope="market": news affects the broad equity market (Fed decisions, macro data, geopolitics, indices).
- scope="exchange": news primarily affects one exchange or sector (e.g. NASDAQ tech selloff) → set exchange.
- scope="irrelevant": sports, entertainment, non-financial content, or clearly unrelated to financial markets.
- impact=null ONLY when scope="irrelevant".
- impact="HIGH": major earnings surprise, acquisition, regulatory action, Fed rate decision, market crash.
- impact="MEDIUM": analyst upgrades/downgrades, product launches, minor macro data releases.
- impact="LOW": routine news, small events with marginal financial relevance.
- article_summary: 2-3 sentences summarizing the key financial facts from the full text. Set null if scope="irrelevant".\
"""


async def build_news_classifier_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
    full_content: bool = False,
) -> object:
    """
    Construye el NewsClassifierAgent — clasifica artículos individuales.

    scope → company | market | exchange | irrelevant
    impact → HIGH | MEDIUM | LOW | null

    Si full_content=True, usa el system prompt extendido que también solicita article_summary.
    """
    system_prompt = NEWS_CLASSIFIER_FULL_SYSTEM_PROMPT if full_content else NEWS_CLASSIFIER_SYSTEM_PROMPT
    agent = (
        AgentBuilder()
        .name("news_classifier_full" if full_content else "news_classifier")
        .planner("simple")
        .tools([])
        .model_role("news_classifier")
        .system_prompt(system_prompt)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


def build_news_classifier_prompt(article, full_content: bool = False) -> str:
    """
    Prompt para clasificar un artículo individual con el NewsClassifierAgent.

    Si full_content=True incluye el texto completo del artículo (article.full_content)
    y solicita también un article_summary en el JSON de salida.
    """
    base = (
        "Classify the following news article:\n\n"
        f"Title: {article.title}\n"
        f"Source: {article.source}\n"
        f"Published: {article.published_at.isoformat()}\n"
        f"Description: {article.description}\n"
    )
    if full_content and getattr(article, "full_content", ""):
        base += f"Full article text: {article.full_content}\n"
        base += "\nReturn your classification and summary as JSON."
    else:
        base += "\nReturn your classification as JSON."
    return base


def build_economics_prompt(
    symbol: str,
    company_news: list[dict],
    market_news: list[dict],
) -> str:
    """Construye el prompt para el EconomicsAgent."""
    import json

    company_section = (
        json.dumps(company_news, indent=2) if company_news else "No company news available."
    )
    market_section = (
        json.dumps(market_news, indent=2) if market_news else "No market news available."
    )

    return (
        f"Analyze news for {symbol} and the overall market context.\n\n"
        f"--- Company news for {symbol} ---\n{company_section}\n\n"
        f"--- General market news ---\n{market_section}\n\n"
        f"Provide your economic and sentiment analysis."
    )
