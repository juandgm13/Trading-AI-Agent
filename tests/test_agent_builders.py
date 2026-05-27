"""
Tests de las funciones build_* y prompt builders de los agentes.

Cubren:
  - build_technical_watching_agent, build_technical_entry_agent
  - build_market_context_agent, build_company_analysis_agent
  - build_trading_opportunity_agent, build_trading_entry_agent
  - Prompt builders legacy: build_medium_term_prompt, build_short_term_prompt,
    build_market_context_prompt, build_company_analysis_prompt,
    build_economics_prompt, build_opportunity_prompt, build_entry_prompt
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Helper: mock de AgentBuilder encadenable ──────────────────────────────────

def _make_builder_mock():
    """
    Devuelve (mock_builder, mock_agent).
    mock_builder soporta encadenamiento fluent y .build() devuelve mock_agent.
    """
    mock_agent = MagicMock()
    mock_builder = MagicMock()
    for method in [
        "name", "planner", "tools", "with_tools", "model_role", "system_prompt",
        "stateless", "max_iterations", "with_config", "with_event_bus",
        "with_model_registry",
    ]:
        getattr(mock_builder, method).return_value = mock_builder
    mock_builder.build.return_value = mock_agent
    return mock_builder, mock_agent


def _make_deps():
    """Devuelve (config, bus, model_registry) como MagicMocks."""
    return MagicMock(), MagicMock(), MagicMock()


def _make_extra_deps():
    """Devuelve (alpaca_client, storage, news_service) como MagicMocks."""
    return MagicMock(), MagicMock(), MagicMock()


# ── build_technical_watching_agent ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_technical_agent_returns_agent():
    from agents.technical_agent import build_technical_watching_agent

    mock_builder, mock_agent = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        agent = await build_technical_watching_agent(config, bus, registry, alpaca, storage)

    assert agent is mock_agent


@pytest.mark.asyncio
async def test_build_technical_agent_uses_toolcall_planner():
    from agents.technical_agent import build_technical_watching_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        await build_technical_watching_agent(config, bus, registry, alpaca, storage)

    mock_builder.planner.assert_called_once_with("toolcall")


@pytest.mark.asyncio
async def test_build_technical_agent_uses_correct_role():
    from agents.technical_agent import build_technical_watching_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        await build_technical_watching_agent(config, bus, registry, alpaca, storage)

    mock_builder.model_role.assert_called_once_with("technical")
    mock_builder.stateless.assert_called_once()


@pytest.mark.asyncio
async def test_build_technical_agent_registers_tools():
    from agents.technical_agent import build_technical_watching_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        await build_technical_watching_agent(config, bus, registry, alpaca, storage)

    mock_builder.with_tools.assert_called_once()
    tools_arg = mock_builder.with_tools.call_args[0][0]
    tool_names = [t.name for t in tools_arg]
    assert "get_historical_bars" in tool_names
    assert "save_agent_report" in tool_names


# ── build_technical_entry_agent ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_technical_entry_agent_returns_agent():
    from agents.technical_agent import build_technical_entry_agent

    mock_builder, mock_agent = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        agent = await build_technical_entry_agent(config, bus, registry, alpaca, storage)

    assert agent is mock_agent


@pytest.mark.asyncio
async def test_build_technical_entry_agent_uses_toolcall_planner():
    from agents.technical_agent import build_technical_entry_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        await build_technical_entry_agent(config, bus, registry, alpaca, storage)

    mock_builder.planner.assert_called_once_with("toolcall")


@pytest.mark.asyncio
async def test_build_technical_entry_agent_uses_correct_role():
    from agents.technical_agent import build_technical_entry_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.technical_agent.AgentBuilder", return_value=mock_builder):
        await build_technical_entry_agent(config, bus, registry, alpaca, storage)

    mock_builder.model_role.assert_called_once_with("technical_entry")
    mock_builder.stateless.assert_called_once()


# ── build_market_context_agent ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_market_context_agent_returns_agent():
    from agents.economics_agent import build_market_context_agent

    mock_builder, mock_agent = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        agent = await build_market_context_agent(config, bus, registry, news_service, storage)

    assert agent is mock_agent


@pytest.mark.asyncio
async def test_build_market_context_agent_uses_toolcall_planner():
    from agents.economics_agent import build_market_context_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        await build_market_context_agent(config, bus, registry, news_service, storage)

    mock_builder.planner.assert_called_once_with("toolcall")


@pytest.mark.asyncio
async def test_build_market_context_agent_uses_economics_role():
    from agents.economics_agent import build_market_context_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        await build_market_context_agent(config, bus, registry, news_service, storage)

    mock_builder.model_role.assert_called_once_with("economics")
    mock_builder.stateless.assert_called_once()


@pytest.mark.asyncio
async def test_build_market_context_agent_registers_tools():
    from agents.economics_agent import build_market_context_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        await build_market_context_agent(config, bus, registry, news_service, storage)

    mock_builder.with_tools.assert_called_once()
    tools_arg = mock_builder.with_tools.call_args[0][0]
    tool_names = [t.name for t in tools_arg]
    assert "fetch_market_news" in tool_names
    assert "save_market_context" in tool_names


# ── build_company_analysis_agent ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_company_analysis_agent_returns_agent():
    from agents.economics_agent import build_company_analysis_agent

    mock_builder, mock_agent = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        agent = await build_company_analysis_agent(config, bus, registry, news_service, storage)

    assert agent is mock_agent


@pytest.mark.asyncio
async def test_build_company_analysis_agent_uses_toolcall_planner():
    from agents.economics_agent import build_company_analysis_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        await build_company_analysis_agent(config, bus, registry, news_service, storage)

    mock_builder.planner.assert_called_once_with("toolcall")


@pytest.mark.asyncio
async def test_build_company_analysis_agent_uses_economics_role():
    from agents.economics_agent import build_company_analysis_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, news_service = _make_extra_deps()

    with patch("agents.economics_agent.AgentBuilder", return_value=mock_builder):
        await build_company_analysis_agent(config, bus, registry, news_service, storage)

    mock_builder.model_role.assert_called_once_with("economics")


# ── build_trading_opportunity_agent ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_trading_orchestrator_returns_agent():
    from agents.trading_agent import build_trading_opportunity_agent

    mock_builder, mock_agent = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        agent = await build_trading_opportunity_agent(config, bus, registry, storage)

    assert agent is mock_agent


@pytest.mark.asyncio
async def test_build_trading_orchestrator_uses_toolcall_planner():
    from agents.trading_agent import build_trading_opportunity_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        await build_trading_opportunity_agent(config, bus, registry, storage)

    mock_builder.planner.assert_called_once_with("toolcall")


@pytest.mark.asyncio
async def test_build_trading_orchestrator_uses_trader_role():
    from agents.trading_agent import build_trading_opportunity_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        await build_trading_opportunity_agent(config, bus, registry, storage)

    mock_builder.model_role.assert_called_once_with("trader_opportunity")


@pytest.mark.asyncio
async def test_build_trading_orchestrator_registers_tools():
    from agents.trading_agent import build_trading_opportunity_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    _, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        await build_trading_opportunity_agent(config, bus, registry, storage)

    mock_builder.with_tools.assert_called_once()
    tools_arg = mock_builder.with_tools.call_args[0][0]
    tool_names = [t.name for t in tools_arg]
    assert "get_last_report" in tool_names
    assert "get_market_context" in tool_names
    assert "save_agent_report" in tool_names


# ── build_trading_entry_agent ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_build_trading_entry_agent_returns_agent():
    from agents.trading_agent import build_trading_entry_agent

    mock_builder, mock_agent = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        agent = await build_trading_entry_agent(config, bus, registry, alpaca, storage)

    assert agent is mock_agent


@pytest.mark.asyncio
async def test_build_trading_entry_agent_uses_toolcall_planner():
    from agents.trading_agent import build_trading_entry_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        await build_trading_entry_agent(config, bus, registry, alpaca, storage)

    mock_builder.planner.assert_called_once_with("toolcall")


@pytest.mark.asyncio
async def test_build_trading_entry_agent_uses_trader_entry_role():
    from agents.trading_agent import build_trading_entry_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        await build_trading_entry_agent(config, bus, registry, alpaca, storage)

    mock_builder.model_role.assert_called_once_with("trader_entry")
    mock_builder.stateless.assert_called_once()


@pytest.mark.asyncio
async def test_build_trading_entry_agent_registers_tools():
    from agents.trading_agent import build_trading_entry_agent

    mock_builder, _ = _make_builder_mock()
    config, bus, registry = _make_deps()
    alpaca, storage, _ = _make_extra_deps()

    with patch("agents.trading_agent.AgentBuilder", return_value=mock_builder):
        await build_trading_entry_agent(config, bus, registry, alpaca, storage)

    mock_builder.with_tools.assert_called_once()
    tools_arg = mock_builder.with_tools.call_args[0][0]
    tool_names = [t.name for t in tools_arg]
    assert "get_last_report" in tool_names
    assert "get_market_clock" in tool_names
    assert "save_agent_report" in tool_names


# ── Prompt builders legacy: technical_agent ───────────────────────────────────

def test_build_medium_term_prompt_contains_symbol_and_price():
    from agents.technical_agent import build_medium_term_prompt

    bars = [{"timestamp": "2024-01-01", "open": 170.0, "close": 175.0}]
    prompt = build_medium_term_prompt("AAPL", 175.0, bars)

    assert "AAPL" in prompt
    assert "175.00" in prompt
    assert "medium-term" in prompt.lower() or "daily" in prompt.lower() or "medium" in prompt.lower()


def test_build_medium_term_prompt_includes_bars_json():
    from agents.technical_agent import build_medium_term_prompt

    bars = [{"timestamp": "2024-01-01", "close": 175.0}]
    prompt = build_medium_term_prompt("AAPL", 175.0, bars)

    assert "2024-01-01" in prompt


def test_build_short_term_prompt_contains_symbol_and_price():
    from agents.technical_agent import build_short_term_prompt

    bars = [{"timestamp": "2024-01-01T10:00:00", "close": 175.0}]
    prompt = build_short_term_prompt("MSFT", 320.0, bars)

    assert "MSFT" in prompt
    assert "320.00" in prompt
    assert "entry" in prompt.lower() or "short" in prompt.lower()


def test_build_short_term_prompt_with_empty_bars():
    from agents.technical_agent import build_short_term_prompt

    prompt = build_short_term_prompt("NVDA", 500.0, [])
    assert "NVDA" in prompt
    assert "500.00" in prompt


def test_build_short_term_prompt_with_medium_context_injects_levels():
    """Cuando se pasa medium_term_context, el prompt incluye soportes y resistencias."""
    from agents.technical_agent import build_short_term_prompt

    bars = [{"timestamp": "t", "close": 175.0}]
    context = {
        "trend": "BULLISH",
        "key_supports": [165.0, 160.0],
        "key_resistances": [185.0],
        "signal": "BUY",
        "confidence": 75,
    }
    prompt = build_short_term_prompt("AAPL", 175.0, bars, medium_term_context=context)

    assert "BULLISH" in prompt
    assert "165.0" in prompt
    assert "185.0" in prompt
    assert "Medium-term context" in prompt


def test_build_short_term_prompt_with_empty_medium_context_no_injection():
    """medium_term_context con listas vacías y trend vacío no inyecta sección de contexto."""
    from agents.technical_agent import build_short_term_prompt

    bars = [{"timestamp": "t", "close": 175.0}]
    context = {"trend": "", "key_supports": [], "key_resistances": []}
    prompt = build_short_term_prompt("AAPL", 175.0, bars, medium_term_context=context)

    assert "Medium-term context" not in prompt


# ── Prompt builders legacy: economics_agent ───────────────────────────────────

def test_build_market_context_prompt_with_news():
    from agents.economics_agent import build_market_context_prompt

    news = [{"title": "Fed holds rates", "source": "Reuters"}]
    prompt = build_market_context_prompt(news)

    assert "Fed holds rates" in prompt
    assert "market" in prompt.lower()


def test_build_market_context_prompt_with_empty_news():
    from agents.economics_agent import build_market_context_prompt

    prompt = build_market_context_prompt([])

    assert "No market news available" in prompt


def test_build_company_analysis_prompt_with_news():
    from agents.economics_agent import build_company_analysis_prompt

    news = [{"title": "Apple beats earnings", "source": "Bloomberg"}]
    prompt = build_company_analysis_prompt("AAPL", news)

    assert "AAPL" in prompt
    assert "Apple beats earnings" in prompt


def test_build_company_analysis_prompt_empty_news():
    from agents.economics_agent import build_company_analysis_prompt

    prompt = build_company_analysis_prompt("AAPL", [])

    assert "AAPL" in prompt
    assert "No company news available" in prompt


def test_build_economics_prompt_with_both_news():
    from agents.economics_agent import build_economics_prompt

    company = [{"title": "Apple strong earnings"}]
    market = [{"title": "Markets rally on Fed news"}]
    prompt = build_economics_prompt("AAPL", company, market)

    assert "AAPL" in prompt
    assert "Apple strong earnings" in prompt
    assert "Markets rally" in prompt


def test_build_economics_prompt_with_empty_news():
    from agents.economics_agent import build_economics_prompt

    prompt = build_economics_prompt("AAPL", [], [])

    assert "No company news available" in prompt
    assert "No market news available" in prompt


# ── Prompt builders legacy: trading_agent ────────────────────────────────────

def test_build_opportunity_prompt_with_market_context():
    from agents.trading_agent import build_opportunity_prompt

    tech = {"trend": "BULLISH", "signal": "BUY", "confidence": 75}
    ctx = {"market_sentiment": "BULLISH", "risk_appetite": "RISK_ON"}
    prompt = build_opportunity_prompt("AAPL", 175.0, tech, ctx, max_position_value=500)

    assert "AAPL" in prompt
    assert "175.00" in prompt
    assert "BULLISH" in prompt
    assert "500" in prompt


def test_build_opportunity_prompt_without_market_context():
    from agents.trading_agent import build_opportunity_prompt

    tech = {"trend": "NEUTRAL", "signal": "HOLD", "confidence": 50}
    prompt = build_opportunity_prompt("MSFT", 320.0, tech, None, max_position_value=500)

    assert "MSFT" in prompt
    assert "Not available" in prompt


def test_build_entry_prompt_with_company_report():
    from agents.trading_agent import build_entry_prompt

    tech = {"trend": "BULLISH", "signal": "BUY", "confidence": 80}
    company = {"sentiment": "POSITIVE", "entry_risk": "LOW"}
    prompt = build_entry_prompt("NVDA", 500.0, tech, company, max_position_value=500)

    assert "NVDA" in prompt
    assert "500.00" in prompt
    assert "POSITIVE" in prompt


def test_build_entry_prompt_without_company_report():
    from agents.trading_agent import build_entry_prompt

    tech = {"trend": "BULLISH", "signal": "BUY", "confidence": 70}
    prompt = build_entry_prompt("TSLA", 250.0, tech, None, max_position_value=500)

    assert "TSLA" in prompt
    assert "Not available" in prompt
