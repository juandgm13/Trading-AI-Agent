"""
Tests de integración del ReAct Trading Agent.
Requieren Alpaca + GNews + Ollama corriendo localmente.
"""
import json
import os
import pytest
from conftest import requires_all


pytestmark = pytest.mark.asyncio


@requires_all
async def test_build_trading_agent_succeeds(alpaca, tmp_db):
    """El agente se construye sin errores con todas las dependencias."""
    from pathlib import Path
    from agent_framework.config.config_loader import load_from_yaml
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.providers import OllamaProvider
    from agents.trader import build_trading_agent

    config = load_from_yaml(Path("agents/trading_agent.yaml"))
    bus = EventBus()
    registry = ModelRegistry()
    registry.register("trader", OllamaProvider("qwen2.5:7b"))

    agent = await build_trading_agent(
        config=config, bus=bus, model_registry=registry,
        alpaca_client=alpaca, storage=tmp_db,
        gnews_api_key=os.environ["GNEWS_API_KEY"],
    )

    assert agent is not None
    assert agent._context.agent_name == "trading_decision_maker"
    assert len(agent._context.tools) == 4


@requires_all
async def test_agent_responds_to_hold_signal(alpaca, tmp_db):
    """
    El agente recibe un prompt con señales mixtas y responde con un análisis.
    Verifica que complete sin errores y devuelva texto.
    No necesariamente ejecuta una orden (depende del LLM).
    """
    from pathlib import Path
    from agent_framework.config.config_loader import load_from_yaml
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.providers import OllamaProvider
    from agents.trader import build_trading_agent

    config = load_from_yaml(Path("agents/trading_agent.yaml"))
    bus = EventBus()
    registry = ModelRegistry()
    registry.register("trader", OllamaProvider("qwen2.5:7b"))

    agent = await build_trading_agent(
        config=config, bus=bus, model_registry=registry,
        alpaca_client=alpaca, storage=tmp_db,
        gnews_api_key=os.environ["GNEWS_API_KEY"],
    )

    result = await agent.run(
        "PRICE ALERT: AAPL has dropped 2.5% from $180.00 to $175.50. "
        "Analyze using available tools and decide: BUY, SELL, or HOLD."
    )

    assert isinstance(result, str)
    assert len(result) > 20
    # El agente debe mencionar el símbolo en su análisis
    assert "AAPL" in result.upper() or "Apple" in result


@requires_all
async def test_agent_uses_get_price_tool(alpaca, tmp_db):
    """Verifica que el agente invoca la tool get_price durante el análisis."""
    from pathlib import Path
    from agent_framework.config.config_loader import load_from_yaml
    from agent_framework.events import EventBus, EventType
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.providers import OllamaProvider
    from agents.trader import build_trading_agent

    config = load_from_yaml(Path("agents/trading_agent.yaml"))
    bus = EventBus()
    tools_called = []

    async def capture_tools(event):
        if event.type == EventType.TOOL_CALLED:
            tools_called.append(event.data.get("tool_name"))

    bus.subscribe("*", capture_tools)

    registry = ModelRegistry()
    registry.register("trader", OllamaProvider("qwen2.5:7b"))

    agent = await build_trading_agent(
        config=config, bus=bus, model_registry=registry,
        alpaca_client=alpaca, storage=tmp_db,
        gnews_api_key=os.environ["GNEWS_API_KEY"],
    )

    await agent.run(
        "PRICE ALERT: MSFT has risen 3.0% from $310.00 to $319.30. "
        "Analyze and decide: BUY, SELL, or HOLD."
    )

    assert "get_price" in tools_called, (
        f"Se esperaba que el agente llamara a get_price. Tools llamadas: {tools_called}"
    )
