"""
Tests del ReAct Trading Agent.

- test_build_trading_agent_succeeds: verifica que el agente se construye
  correctamente con todas las dependencias (requiere Alpaca + GNews).

- test_agent_responds_to_hold_signal: end-to-end con el LLM real,
  verifica que el agente devuelve una respuesta (requiere Alpaca + GNews + Ollama).

- test_agent_uses_get_price_tool: usa ScriptedMockProvider para garantizar
  que la tool get_price se invoca cuando el LLM produce un paso ReAct correcto.
  No requiere credenciales externas.
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from conftest import requires_alpaca, requires_gnews, requires_all


pytestmark = pytest.mark.asyncio


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_alpaca(symbol="AAPL", price=175.0):
    """AlpacaClient mock con respuestas razonables."""
    alpaca = MagicMock()
    alpaca.get_bars = AsyncMock(return_value=[
        {"timestamp": "2024-01-01T10:00:00Z", "open": price, "high": price + 1,
         "low": price - 1, "close": price, "volume": 1_000_000}
    ])
    alpaca.get_account = AsyncMock(return_value={
        "cash": 100_000.0, "equity": 100_000.0,
        "portfolio_value": 100_000.0, "buying_power": 200_000.0,
    })
    alpaca.get_positions = AsyncMock(return_value=[])
    alpaca.get_position = AsyncMock(return_value=None)
    alpaca.place_order = AsyncMock(return_value={
        "order_id": "mock-order-id", "status": "accepted",
        "symbol": symbol, "side": "buy", "qty": 1.0,
    })
    return alpaca


async def _build_agent_with_mock_llm(responses: list[str], alpaca=None, tmp_db=None, gnews_key=None):
    """
    Construye el trading agent con un ScriptedMockProvider en lugar del LLM real.
    Permite controlar exactamente qué responde el modelo.
    """
    from agent_framework.config.config_loader import load_from_yaml
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.providers.mock_provider import ScriptedMockProvider
    from agents.trader import build_trading_agent

    config = load_from_yaml(Path("agents/trading_agent.yaml"))
    bus = EventBus()
    registry = ModelRegistry()
    registry.register("trader", ScriptedMockProvider(responses))

    agent = await build_trading_agent(
        config=config,
        bus=bus,
        model_registry=registry,
        alpaca_client=alpaca or _make_mock_alpaca(),
        storage=tmp_db,
        gnews_api_key=gnews_key or "mock-key",
    )
    return agent, bus


# ── Tests con LLM mockeado (no requieren credenciales) ───────────────────────

async def test_agent_uses_get_price_tool(tmp_db):
    """
    Verifica que el agente invoca get_price cuando el LLM produce
    un paso ReAct válido con Action: get_price.
    """
    from agent_framework.events import EventType

    # El mock LLM produce: primero llama get_price, luego Final Answer
    responses = [
        (
            "Thought: I should check the current price of AAPL.\n"
            'Action: get_price\n'
            'Action Input: {"symbol": "AAPL"}\n'
        ),
        "Final Answer: HOLD — price data retrieved successfully.",
    ]

    agent, bus = await _build_agent_with_mock_llm(responses, tmp_db=tmp_db)

    tools_called = []

    async def capture(event):
        if event.type == EventType.TOOL_CALLED:
            tools_called.append(event.data.get("tool"))

    bus.subscribe("*", capture)

    result = await agent.run(
        "PRICE ALERT: AAPL has dropped 2.5% from $180.00 to $175.50. "
        "Analyze and decide: BUY, SELL, or HOLD."
    )

    assert "get_price" in tools_called, (
        f"Expected agent to call get_price. Tools called: {tools_called}"
    )
    assert isinstance(result, str) and len(result) > 5


async def test_agent_calls_search_news_after_price(tmp_db):
    """El agente llama search_news en el segundo paso ReAct."""
    from agent_framework.events import EventType

    responses = [
        (
            "Thought: First I'll check the price.\n"
            'Action: get_price\n'
            'Action Input: {"symbol": "MSFT"}\n'
        ),
        (
            "Thought: Now I'll search for news.\n"
            'Action: search_news\n'
            'Action Input: {"query": "Microsoft"}\n'
        ),
        "Final Answer: HOLD — mixed signals.",
    ]

    agent, bus = await _build_agent_with_mock_llm(responses, tmp_db=tmp_db)
    tools_called = []

    async def capture(event):
        if event.type == EventType.TOOL_CALLED:
            tools_called.append(event.data.get("tool"))

    bus.subscribe("*", capture)
    await agent.run("PRICE ALERT: MSFT has risen 3%. Analyze and decide.")

    assert "get_price" in tools_called
    assert "search_news" in tools_called


async def test_agent_place_order_tool_called(tmp_db):
    """El agente llama place_order cuando el LLM decide comprar."""
    from agent_framework.events import EventType

    mock_alpaca = _make_mock_alpaca("AAPL", 175.0)

    responses = [
        (
            "Thought: Checking price first.\n"
            'Action: get_price\n'
            'Action Input: {"symbol": "AAPL"}\n'
        ),
        (
            "Thought: Strong buy signal. Placing order.\n"
            'Action: place_order\n'
            'Action Input: {"symbol": "AAPL", "side": "buy", "qty": 1, '
            '"reason": "Strong earnings beat confirmed by price action."}\n'
        ),
        "Final Answer: BUY order placed for AAPL.",
    ]

    agent, bus = await _build_agent_with_mock_llm(responses, alpaca=mock_alpaca, tmp_db=tmp_db)
    tools_called = []

    async def capture(event):
        if event.type == EventType.TOOL_CALLED:
            tools_called.append(event.data.get("tool"))

    bus.subscribe("*", capture)
    await agent.run("PRICE ALERT: AAPL dropped 3%. Analyze and decide.")

    assert "place_order" in tools_called
    mock_alpaca.place_order.assert_awaited_once()
    trades = tmp_db.get_trades()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["side"] == "buy"


async def test_agent_order_safety_no_sell_without_position(tmp_db):
    """
    Cuando el LLM intenta vender sin posición abierta,
    place_order devuelve error y no llama a Alpaca.
    """
    mock_alpaca = _make_mock_alpaca("AAPL", 175.0)
    mock_alpaca.get_position = AsyncMock(return_value=None)  # sin posición

    responses = [
        (
            "Thought: Selling.\n"
            'Action: place_order\n'
            'Action Input: {"symbol": "AAPL", "side": "sell", "qty": 5, '
            '"reason": "Taking profit."}\n'
        ),
        "Final Answer: Order rejected — no position held.",
    ]

    agent, _ = await _build_agent_with_mock_llm(responses, alpaca=mock_alpaca, tmp_db=tmp_db)
    result = await agent.run("Sell AAPL.")

    mock_alpaca.place_order.assert_not_called()
    assert tmp_db.get_trades() == []


# ── Tests con LLM real (requieren Alpaca + GNews + Ollama) ────────────────────

@requires_all
async def test_build_trading_agent_succeeds(alpaca, tmp_db):
    """El agente se construye sin errores con todas las dependencias reales."""
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
async def test_agent_real_llm_returns_response(alpaca, tmp_db):
    """
    End-to-end con LLM real: verifica que el agente completa sin errores
    y devuelve una respuesta de texto. El contenido depende del modelo.
    """
    from agent_framework.config.config_loader import load_from_yaml
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.providers import OllamaProvider
    from agents.trader import build_trading_agent

    config = load_from_yaml(Path("agents/trading_agent.yaml"))
    registry = ModelRegistry()
    registry.register("trader", OllamaProvider("qwen2.5:7b"))

    agent = await build_trading_agent(
        config=config, bus=EventBus(), model_registry=registry,
        alpaca_client=alpaca, storage=tmp_db,
        gnews_api_key=os.environ["GNEWS_API_KEY"],
    )

    result = await agent.run(
        "PRICE ALERT: AAPL has dropped 2.5% from $180.00 to $175.50. "
        "Analyze this and decide: BUY, SELL, or HOLD."
    )

    assert isinstance(result, str)
    assert len(result) > 10
