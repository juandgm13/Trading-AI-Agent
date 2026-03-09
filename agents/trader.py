"""
ReAct Trading Agent — toma decisiones de compra/venta basadas en precios y noticias.

Expone build_trading_agent() que construye el agente con todas las tools inyectadas.
"""
from agent_framework.core.agent_builder import AgentBuilder
from agent_framework.events import EventBus
from agent_framework.models.model_registry import ModelRegistry
from agent_framework.tools.tool_registry import ToolRegistry

from .tools.price_tool import GetPriceTool
from .tools.news_tool import SearchNewsTool
from .tools.portfolio_tool import GetPortfolioTool
from .tools.order_tool import PlaceOrderTool


TRADING_SYSTEM_PROMPT = """\
You are a disciplined algorithmic trading assistant operating on paper (simulated) trading.

Your objective: analyze price movements and news to make informed, conservative trading decisions.

Trading rules:
- ALWAYS gather information before deciding. Do NOT place orders without first checking price and news.
- Use get_price to understand the current price and recent trend.
- Use search_news to find recent news for the affected company.
- Use get_portfolio to check available cash and existing positions.
- Only place an order when you have HIGH confidence based on BOTH price trend AND news signals.
- Maximum 10 shares per order.
- NEVER sell what you don't own — verify position before selling.
- When signals conflict or are unclear → HOLD (do nothing), explain why.
- Always provide a concise reason for any trade placed.

Decision framework:
- Strong POSITIVE news + price dip  → consider BUY (buy the dip)
- Strong NEGATIVE news + price rise → consider SELL (take profits / cut losses)
- Mixed signals                      → HOLD, summarize your analysis
- Insufficient data                  → HOLD, request more information

After completing your analysis, always end with a clear Final Answer summarizing:
1. What you found (price trend + news sentiment)
2. What decision you made (BUY / SELL / HOLD) and why\
"""


async def build_trading_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
    alpaca_client,
    storage,
    gnews_api_key: str,
    max_qty_per_order: int = 10,
    max_iterations: int = 8,
    alerter=None,
):
    """
    Construye el ReAct trading agent con todas las tools inyectadas.

    Args:
        config: FrameworkConfig cargado del YAML
        bus: EventBus compartido
        model_registry: Registro con el modelo "trader" registrado
        alpaca_client: AlpacaClient instanciado
        storage: StorageLayer instanciado
        gnews_api_key: API key para GNews
        max_qty_per_order: Límite de acciones por orden (seguridad)
        max_iterations: Máximo de pasos ReAct antes de terminar forzosamente
        alerter: TradeAlerter opcional para notificaciones email al operar
    """
    tool_registry = ToolRegistry()
    tool_registry.register(GetPriceTool(alpaca_client))
    tool_registry.register(SearchNewsTool(gnews_api_key))
    tool_registry.register(GetPortfolioTool(alpaca_client))
    tool_registry.register(PlaceOrderTool(alpaca_client, storage, max_qty_per_order, alerter))

    agent = (
        AgentBuilder()
        .name("trading_decision_maker")
        .planner("react")
        .tools(["get_price", "search_news", "get_portfolio", "place_order"])
        .model_role("trader")
        .system_prompt(TRADING_SYSTEM_PROMPT)
        .max_iterations(max_iterations)
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .with_tool_registry(tool_registry)
        .build()
    )
    return agent
