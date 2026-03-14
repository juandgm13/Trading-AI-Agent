"""
TradingAgent — orquestador que evalúa informes de sub-agentes y decide operaciones.

Reemplaza al ReAct agent de trader.py como punto de decisión final.
Recibe los informes de TechnicalAnalysisAgent y EconomicsAgent como contexto
y decide si abrir, mantener o rechazar una posición.

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


TRADING_ORCHESTRATOR_PROMPT = """\
You are a disciplined algorithmic trading decision maker operating in paper (simulated) mode.

You receive technical analysis and economic analysis reports from specialized sub-agents.
Based on these reports, you decide whether to open a position, hold, or reject the opportunity.

Return ONLY a valid JSON object — no markdown, no explanation outside JSON.

Required JSON structure:
{
  "decision": "OPEN_POSITION" | "HOLD" | "REJECT",
  "symbol": "<ticker>",
  "qty": <integer shares, or null if not opening>,
  "reasoning": "<one paragraph explaining the decision>"
}

Decision rules:
- OPEN_POSITION: technical signal is BUY AND global market is RISK_ON or NEUTRAL
  AND company entry_risk is LOW or MEDIUM AND technical confidence >= 60.
- HOLD: signals conflict, confidence is low, market is RISK_OFF but not severely, or more info needed.
- REJECT: signals clearly unfavorable, entry_risk is HIGH, or market is strongly BEARISH.
- Maximum qty per decision: respect the max_qty limit provided in the prompt.
- Never recommend qty = 0 for OPEN_POSITION — always provide a valid integer.\
"""


async def build_trading_orchestrator(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
) -> object:
    """
    Construye el TradingAgent orquestador (SimplePlanner, sin tools directas).

    El monitor inyecta los informes de sub-agentes en el prompt.
    La ejecución real de órdenes la realiza el monitor directamente.
    """
    agent = (
        AgentBuilder()
        .name("trading_orchestrator")
        .planner("simple")
        .tools([])
        .model_role("trader")
        .system_prompt(TRADING_ORCHESTRATOR_PROMPT)
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
    max_qty: int,
) -> str:
    """Prompt para evaluar si hay oportunidad (WATCHING → OPPORTUNITY_FOUND)."""
    import json
    market_section = (
        json.dumps(market_context, indent=2)
        if market_context
        else "Not available — proceed based on technical analysis alone."
    )
    return (
        f"Evaluate whether {symbol} presents a trading opportunity.\n"
        f"Current price: ${current_price:.2f}\n"
        f"Maximum shares per order: {max_qty}\n\n"
        f"Technical Analysis (medium-term):\n{json.dumps(tech_report, indent=2)}\n\n"
        f"Global Market Context:\n{market_section}\n\n"
        f"Should we monitor this symbol more closely for an entry? "
        f"Use OPEN_POSITION only if signals are very strong NOW, "
        f"HOLD if we should watch for entry timing, REJECT if not interesting."
    )


def build_entry_prompt(
    symbol: str,
    current_price: float,
    tech_report: dict,
    company_report: dict | None,
    max_qty: int,
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
        f"Maximum shares per order: {max_qty}\n\n"
        f"Short-term Technical Analysis:\n{json.dumps(tech_report, indent=2)}\n\n"
        f"Company News Analysis:\n{company_section}\n\n"
        f"The opportunity was flagged previously. Is this the right moment to enter? "
        f"Use OPEN_POSITION to execute the trade, HOLD to wait, REJECT to abandon."
    )
