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


TECHNICAL_SYSTEM_PROMPT = """\
You are a technical analysis expert for stock markets.

You will receive OHLCV (Open, High, Low, Close, Volume) bar data for a symbol.
Analyze the data and return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "trend": "BULLISH" | "BEARISH" | "NEUTRAL",
  "signal": "BUY" | "SELL" | "HOLD" | "WAIT",
  "target_price": <float or null>,
  "stop_loss": <float or null>,
  "confidence": <integer 0-100>,
  "summary": "<one paragraph explaining the analysis>"
}

Rules:
- Set target_price and stop_loss only when signal is BUY or SELL.
- Base your analysis on: trend direction, momentum, support/resistance levels, volume patterns.
- Be conservative: only signal BUY or SELL when evidence is clear (confidence >= 65).
- Return WAIT if the data is insufficient or the setup is unclear.
- target_price and stop_loss must be realistic (within 10-20% of current price).\
"""


async def build_technical_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el TechnicalAnalysisAgent (SimplePlanner, sin tools).

    El monitor pre-inyecta los datos OHLCV en el prompt antes de llamar a run().
    """
    agent = (
        AgentBuilder()
        .name("technical_analysis")
        .planner("simple")
        .tools([])
        .model_role("technical")
        .system_prompt(TECHNICAL_SYSTEM_PROMPT)
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
        f"Analyze {symbol} for medium-term opportunity assessment.\n"
        f"Current price: ${current_price:.2f}\n"
        f"Historical data (daily bars, most recent first):\n"
        f"{json.dumps(bars, indent=2)}\n\n"
        f"Provide a medium-term technical analysis (trend, key levels, outlook)."
    )


def build_short_term_prompt(symbol: str, current_price: float, bars: list[dict]) -> str:
    """Construye el prompt para análisis de corto plazo (15min bars)."""
    import json
    return (
        f"Analyze {symbol} for short-term entry timing.\n"
        f"Current price: ${current_price:.2f}\n"
        f"Short-term data (15-minute bars, most recent first):\n"
        f"{json.dumps(bars, indent=2)}\n\n"
        f"Determine if NOW is a good entry point. Provide specific target_price and stop_loss."
    )
