"""
EconomicsAgent — analiza noticias del símbolo y contexto de mercado general.

El agente recibe noticias pre-fetched por el monitor e inyectadas en el prompt.

Output esperado:
{
  "sentiment": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
  "market_context": "RISK_ON" | "RISK_OFF" | "NEUTRAL",
  "key_events": ["<event1>", "<event2>", ...],
  "confidence": <int 0-100>,
  "summary": "<one paragraph>"
}
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
