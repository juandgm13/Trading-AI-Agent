"""
TechnicalAnalysisAgent — analiza barras históricas para determinar tendencia y señales.

El agente recibe datos OHLCV pre-inyectados en el prompt por el monitor y devuelve
un JSON estructurado con la señal técnica.

Output esperado:
{
  "trend": "BULLISH" | "BEARISH" | "NEUTRAL",
  "signal": "BUY" | "SELL" | "HOLD" | "WAIT",
  "target_price": <float or null>,
  "stop_loss": <float or null>,
  "confidence": <int 0-100>,
  "summary": "<one paragraph>"
}
"""
from agent_framework.core.agent_builder import AgentBuilder
from agent_framework.events import EventBus
from agent_framework.models.model_registry import ModelRegistry


TECHNICAL_WATCHING_PROMPT = """\
You are a technical analysis expert for stock markets focused on medium-term trend detection.

You will receive OHLCV (Open, High, Low, Close, Volume) daily bar data for a symbol.
Your goal is to assess whether the symbol shows a developing trend worth monitoring more closely.
Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "trend": "BULLISH" | "BEARISH" | "NEUTRAL",
  "signal": "BUY" | "SELL" | "HOLD" | "WAIT",
  "key_supports": [<float>, ...],
  "key_resistances": [<float>, ...],
  "confidence": <integer 0-100>,
  "summary": "<one paragraph explaining the medium-term analysis, trend direction, and key levels>"
}

Rules:
- Focus on macro trend direction, key support/resistance levels, and volume patterns over weeks.
- key_supports: list of 1-3 significant price levels where buyers have historically stepped in (below current price).
- key_resistances: list of 1-3 significant price levels where sellers have historically emerged (above current price).
- Both lists may be empty [] if no clear levels are identifiable.
- Use HOLD if the trend is developing but entry timing is unclear — this is valid and preferred over forcing a signal.
- Use WAIT if data is insufficient or the setup is ambiguous.\
"""

TECHNICAL_ENTRY_PROMPT = """\
You are a technical analysis expert for stock markets focused on precise entry timing.

You will receive OHLCV (Open, High, Low, Close, Volume) short-term bar data (15-minute intervals).
An opportunity was previously identified in this symbol. Your goal is to determine if NOW is the
optimal entry point with specific, actionable targets.
Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "trend": "BULLISH" | "BEARISH" | "NEUTRAL",
  "signal": "BUY" | "SELL" | "HOLD" | "WAIT",
  "target_price": <float or null>,
  "stop_loss": <float or null>,
  "confidence": <integer 0-100>,
  "summary": "<one paragraph explaining the entry timing analysis>"
}

Rules:
- Focus on short-term momentum, intraday patterns, and precise entry timing.
- You MUST provide target_price and stop_loss when signal is BUY (both are required for position management).
- target_price and stop_loss must be tight and realistic (within 5-10% of current price for short-term).
- Only signal BUY when entry setup is clear (confidence >= 70) — this is a real trade decision.
- Use HOLD to wait for a better entry within the same opportunity.
- Use WAIT if momentum has reversed or setup has failed.\
"""


async def build_technical_watching_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el TechnicalAnalysisAgent para análisis de medio plazo (WATCHING).

    El monitor pre-inyecta datos OHLCV diarios en el prompt antes de llamar a run().
    """
    agent = (
        AgentBuilder()
        .name("technical_watching")
        .planner("simple")
        .tools([])
        .model_role("technical")
        .system_prompt(TECHNICAL_WATCHING_PROMPT)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


async def build_technical_entry_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el TechnicalAnalysisAgent para timing de entrada (OPPORTUNITY_FOUND).

    El monitor pre-inyecta datos OHLCV de 15min en el prompt antes de llamar a run().
    """
    agent = (
        AgentBuilder()
        .name("technical_entry")
        .planner("simple")
        .tools([])
        .model_role("technical_entry")
        .system_prompt(TECHNICAL_ENTRY_PROMPT)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


def build_medium_term_prompt(symbol: str, current_price: float, bars: list[dict]) -> str:
    """Construye el prompt para análisis de medio plazo (90 días diarios)."""
    import json
    return (
        f"Analyze {symbol} for medium-term trend assessment.\n"
        f"Current price: ${current_price:.2f}\n"
        f"Historical data (daily bars, most recent first):\n"
        f"{json.dumps(bars, indent=2)}\n\n"
        f"Assess the developing trend, key support/resistance levels, and whether "
        f"this symbol deserves closer monitoring for a potential entry."
    )


def build_short_term_prompt(
    symbol: str,
    current_price: float,
    bars: list[dict],
    medium_term_context: dict | None = None,
) -> str:
    """Construye el prompt para análisis de corto plazo (15min bars).

    medium_term_context: informe previo del TechnicalWatchingAgent con key_supports/key_resistances.
    """
    import json
    context_section = ""
    if medium_term_context:
        supports = medium_term_context.get("key_supports", [])
        resistances = medium_term_context.get("key_resistances", [])
        macro_trend = medium_term_context.get("trend", "")
        if supports or resistances or macro_trend:
            context_section = (
                f"\nMedium-term context (daily bars analysis):\n"
                f"  Macro trend: {macro_trend}\n"
                f"  Key supports: {supports}\n"
                f"  Key resistances: {resistances}\n"
            )
    return (
        f"Analyze {symbol} for short-term entry timing.\n"
        f"Current price: ${current_price:.2f}\n"
        f"{context_section}"
        f"Short-term data (15-minute bars, most recent first):\n"
        f"{json.dumps(bars, indent=2)}\n\n"
        f"An opportunity was flagged for this symbol. Determine if NOW is the optimal entry point. "
        f"Provide specific target_price and stop_loss — these are required for BUY signals."
    )
