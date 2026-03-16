"""
TradingAgent — orquestador que evalúa informes de sub-agentes y decide operaciones.

Dos agentes con prompts separados según el estado de la máquina:
- trading_opportunity_agent: WATCHING → ¿merece vigilancia más cercana?
- trading_entry_agent:       OPPORTUNITY_FOUND → ¿entrar AHORA?

Output esperado:
{
  "decision": "OPEN_POSITION" | "HOLD" | "REJECT",
  "symbol": "<ticker>",
  "qty": <int or null>,
  "reasoning": "<explanation>"
}
"""
from agent_framework.core.agent_builder import AgentBuilder
from agent_framework.events import EventBus
from agent_framework.models.model_registry import ModelRegistry


TRADING_OPPORTUNITY_PROMPT = """\
You are a disciplined algorithmic trading scout operating in paper (simulated) mode.

You receive a medium-term technical analysis report and global market context.
Your goal is to decide whether this symbol deserves closer monitoring for a potential trade entry.

Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "decision": "OPEN_POSITION" | "HOLD" | "REJECT",
  "symbol": "<ticker>",
  "qty": null,
  "reasoning": "<one paragraph explaining the decision>"
}

Decision rules:
- OPEN_POSITION: Only if the technical signal is BUY with high confidence (>= 70) AND market is RISK_ON.
  This is rare — reserve for exceptionally clear opportunities.
- HOLD: The trend looks promising but entry timing is not confirmed yet. Watch more closely.
  Use this when trend is BULLISH, confidence is moderate (50-70), or market is NEUTRAL.
  HOLD is the preferred outcome when signals are mixed or developing.
- REJECT: Trend is BEARISH, confidence is very low (< 40), or market is strongly RISK_OFF.

Important: qty must always be null in this phase — position sizing happens at entry.
Bias toward HOLD over REJECT when signals are ambiguous — patience is a virtue.\
"""

TRADING_ENTRY_PROMPT = """\
You are a disciplined algorithmic trading decision maker operating in paper (simulated) mode.

You receive a short-term technical analysis and company news analysis.
An opportunity was previously flagged. Your goal is to decide whether to execute a trade RIGHT NOW.

Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "decision": "OPEN_POSITION" | "HOLD" | "REJECT",
  "symbol": "<ticker>",
  "qty": null,
  "reasoning": "<one paragraph explaining the decision>"
}

Decision rules:
- OPEN_POSITION: Short-term signal is BUY with high confidence (>= 70), market is RISK_ON or NEUTRAL,
  entry_risk is LOW or MEDIUM. All conditions must be met. qty is always null (sizing handled externally).
- HOLD: Conditions are close but not ideal. Wait for a better entry within the same opportunity.
  Use when: signal is HOLD/WAIT, confidence is 50-70, or entry_risk is MEDIUM with mixed signals.
- REJECT: Setup has deteriorated, signal reversed to BEARISH, entry_risk is HIGH, or market turned RISK_OFF.
  Rejecting returns the symbol to WATCHING state.

Be strict: this is a real trade execution decision. When in doubt, HOLD rather than REJECT.
Never recommend qty — position sizing is calculated externally based on max_position_value.\
"""


async def build_trading_opportunity_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el TradingAgent para evaluar oportunidades (WATCHING → OPPORTUNITY_FOUND).

    Temperatura más alta → más apertura a HOLD, menos presión a decidir rápido.
    """
    agent = (
        AgentBuilder()
        .name("trading_opportunity")
        .planner("simple")
        .tools([])
        .model_role("trader_opportunity")
        .system_prompt(TRADING_OPPORTUNITY_PROMPT)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


async def build_trading_entry_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el TradingAgent para decidir entrada (OPPORTUNITY_FOUND → POSITION_OPEN).

    Temperatura más baja → más estricto, requiere señal clara para OPEN_POSITION.
    """
    agent = (
        AgentBuilder()
        .name("trading_entry")
        .planner("simple")
        .tools([])
        .model_role("trader_entry")
        .system_prompt(TRADING_ENTRY_PROMPT)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .build()
    )
    return agent


def build_opportunity_prompt(
    symbol: str,
    current_price: float,
    tech_report: dict,
    market_context: dict | None,
    max_position_value: float,
) -> str:
    """Prompt para evaluar si hay oportunidad (WATCHING → OPPORTUNITY_FOUND)."""
    import json
    market_section = (
        json.dumps(market_context, indent=2)
        if market_context
        else "Not available — proceed based on technical analysis alone."
    )
    return (
        f"Evaluate whether {symbol} deserves closer monitoring for a trade entry.\n"
        f"Current price: ${current_price:.2f}\n"
        f"Max position value: ${max_position_value:.0f} USD\n\n"
        f"Technical Analysis (medium-term daily bars):\n{json.dumps(tech_report, indent=2)}\n\n"
        f"Global Market Context:\n{market_section}\n\n"
        f"Is this symbol worth moving to closer monitoring? "
        f"HOLD means 'yes, watch more closely'. REJECT means 'not interesting'. "
        f"OPEN_POSITION only if the signal is exceptionally strong RIGHT NOW."
    )


def build_entry_prompt(
    symbol: str,
    current_price: float,
    tech_report: dict,
    company_report: dict | None,
    max_position_value: float,
) -> str:
    """Prompt para decidir entrada (OPPORTUNITY_FOUND → POSITION_OPEN)."""
    import json
    company_section = (
        json.dumps(company_report, indent=2)
        if company_report
        else "Not available — proceed based on technical analysis alone."
    )
    return (
        f"Decide whether to execute a trade for {symbol} RIGHT NOW.\n"
        f"Current price: ${current_price:.2f}\n"
        f"Max position value: ${max_position_value:.0f} USD "
        f"(approximately {int(max_position_value / current_price)} shares at current price)\n\n"
        f"Short-term Technical Analysis (15-minute bars):\n{json.dumps(tech_report, indent=2)}\n\n"
        f"Company News Analysis:\n{company_section}\n\n"
        f"This opportunity was flagged in a previous cycle. Is this the right moment to enter? "
        f"OPEN_POSITION to execute the trade, HOLD to wait for better timing, REJECT to abandon."
    )
