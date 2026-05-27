"""
Tests para OrchestratorAgent (Phase 4):
- TaskListManager: state management, cycle hooks, completion
- UpdateTaskListTool: execute, mark_task, cycle_complete
- build_orchestrator_agent: verifica configuración del agente y herramientas
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_framework.core.agent_builder import AgentBuilder
from agents.orchestrator_agent import (
    TaskListManager,
    UpdateTaskListTool,
    build_orchestrator_agent,
)


# ── TaskListManager ────────────────────────────────────────────────────────────

class TestTaskListManager:
    def test_reset_creates_pending_tasks(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL", "MSFT", "GOOGL"])
        state = mgr.state_str()
        assert "AAPL" in state
        assert "MSFT" in state
        assert "GOOGL" in state
        assert "PENDING" in state.upper()

    def test_reset_clears_previous_tasks(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("AAPL", "completed", "done")
        mgr.reset(["MSFT"])
        state = mgr.state_str()
        assert "AAPL" not in state
        assert "MSFT" in state

    def test_reset_normalizes_symbols_to_uppercase(self):
        mgr = TaskListManager()
        mgr.reset(["aapl", "msft"])
        state = mgr.state_str()
        assert "AAPL" in state
        assert "MSFT" in state

    def test_is_complete_returns_false_with_pending_tasks(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL", "MSFT"])
        assert mgr.is_complete() is False

    def test_is_complete_returns_true_when_all_completed(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL", "MSFT"])
        mgr.mark_task("AAPL", "completed", "done")
        mgr.mark_task("MSFT", "completed", "done")
        assert mgr.is_complete() is True

    def test_is_complete_returns_true_when_all_failed(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("AAPL", "failed", "error")
        assert mgr.is_complete() is True

    def test_is_complete_returns_true_on_empty_list(self):
        mgr = TaskListManager()
        mgr.reset([])
        assert mgr.is_complete() is True

    def test_get_cycle_complete_initially_false(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        assert mgr.get_cycle_complete() is False

    def test_mark_task_sets_cycle_complete(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("AAPL", "completed", "done")
        assert mgr.get_cycle_complete() is True

    def test_reset_cycle_clears_cycle_complete(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("AAPL", "completed", "done")
        assert mgr.get_cycle_complete() is True
        mgr.reset_cycle()
        assert mgr.get_cycle_complete() is False

    def test_mark_task_case_insensitive(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("aapl", "completed", "lower case input")
        assert mgr.is_complete() is True

    def test_state_str_shows_next_pending(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL", "MSFT", "GOOGL"])
        mgr.mark_task("AAPL", "completed", "done")
        mgr.reset_cycle()
        state = mgr.state_str()
        assert "MSFT" in state
        assert "Process next" in state

    def test_state_str_shows_all_done_when_complete(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("AAPL", "completed", "done")
        state = mgr.state_str()
        assert "All symbols processed" in state

    def test_mark_task_updates_summary(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL"])
        mgr.mark_task("AAPL", "completed", "OPPORTUNITY_FOUND — watching")
        state = mgr.state_str()
        assert "OPPORTUNITY_FOUND" in state


# ── UpdateTaskListTool ─────────────────────────────────────────────────────────

class TestUpdateTaskListTool:
    @pytest.fixture
    def manager_and_tool(self):
        mgr = TaskListManager()
        mgr.reset(["AAPL", "MSFT"])
        tool = UpdateTaskListTool(mgr)
        return mgr, tool

    @pytest.mark.asyncio
    async def test_execute_marks_task_completed(self, manager_and_tool):
        mgr, tool = manager_and_tool
        result = await tool.execute(symbol="AAPL", status="completed", summary="HOLD")
        data = json.loads(result)
        assert data["status"] == "completed"
        assert data["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_execute_sets_cycle_complete(self, manager_and_tool):
        mgr, tool = manager_and_tool
        assert mgr.get_cycle_complete() is False
        await tool.execute(symbol="AAPL", status="completed", summary="done")
        assert mgr.get_cycle_complete() is True

    @pytest.mark.asyncio
    async def test_execute_marks_task_failed(self, manager_and_tool):
        mgr, tool = manager_and_tool
        await tool.execute(symbol="MSFT", status="failed", summary="error occurred")
        assert mgr.is_complete() is False  # AAPL still pending
        mgr.mark_task("AAPL", "completed", "done")
        assert mgr.is_complete() is True

    @pytest.mark.asyncio
    async def test_execute_uppercases_symbol(self, manager_and_tool):
        mgr, tool = manager_and_tool
        result = await tool.execute(symbol="aapl", status="completed", summary="done")
        data = json.loads(result)
        assert data["symbol"] == "AAPL"

    def test_tool_name_is_update_task_list(self):
        mgr = TaskListManager()
        tool = UpdateTaskListTool(mgr)
        assert tool.name == "update_task_list"

    def test_tool_has_required_parameters(self):
        mgr = TaskListManager()
        tool = UpdateTaskListTool(mgr)
        params = tool.parameters
        assert "symbol" in params["properties"]
        assert "status" in params["properties"]
        assert "summary" in params["properties"]
        assert "symbol" in params["required"]
        assert "status" in params["required"]


# ── build_orchestrator_agent ───────────────────────────────────────────────────

def _make_mock_sub_agents():
    """Helper: crea un dict de sub-agentes mockeados."""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value='{"decision": "HOLD", "symbol": "AAPL"}')
    return {
        "technical_watching": mock_agent,
        "technical_entry": mock_agent,
        "market_context": mock_agent,
        "company_analysis": mock_agent,
        "trading_opportunity": mock_agent,
        "trading_entry": mock_agent,
    }


def _make_mock_infrastructure():
    """Helper: crea mocks de alpaca_client, storage, news_service."""
    mock_alpaca = MagicMock()
    mock_alpaca.get_bars = AsyncMock(return_value=[])
    mock_alpaca.get_clock = AsyncMock(return_value={"is_open": True})
    mock_alpaca.place_order = AsyncMock(return_value={"order_id": "test"})
    mock_alpaca.get_order = AsyncMock(return_value=None)
    mock_alpaca.get_portfolio = AsyncMock(return_value=[])
    mock_alpaca.get_latest_price = AsyncMock(return_value=100.0)

    mock_storage = MagicMock()
    mock_storage.get_symbol_state = MagicMock(return_value={"state": "WATCHING"})
    mock_storage.upsert_symbol_state = MagicMock()
    mock_storage.get_last_agent_report = MagicMock(return_value=None)
    mock_storage.get_latest_market_context = MagicMock(return_value=None)
    mock_storage.save_market_context = MagicMock()

    mock_news_service = MagicMock()
    mock_news_service.run_market_ingestion = AsyncMock(return_value=(0, 0))
    mock_news_service.run_symbol_ingestion = AsyncMock(return_value=[])

    return mock_alpaca, mock_storage, mock_news_service


@pytest.mark.asyncio
async def test_build_orchestrator_agent_returns_agent_and_manager():
    """build_orchestrator_agent devuelve una tupla (agent, TaskListManager)."""
    from agent_framework.core.agent_builder import AgentBuilder
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.config.schema import FrameworkConfig
    from agent_framework.providers.mock_provider import MockProvider

    bus = EventBus()
    registry = ModelRegistry()
    registry.register("orchestrator", MockProvider("ok"))
    config = FrameworkConfig()

    alpaca, storage, news = _make_mock_infrastructure()
    sub_agents = _make_mock_sub_agents()

    with patch("agent_framework.core.agent_builder.AgentBuilder.build") as mock_build:
        mock_agent = MagicMock()
        mock_build.return_value = mock_agent

        result = await build_orchestrator_agent(
            config=config,
            bus=bus,
            model_registry=registry,
            sub_agents=sub_agents,
            alpaca_client=alpaca,
            storage=storage,
            news_service=news,
            max_position_value=500.0,
        )

    assert isinstance(result, tuple)
    assert len(result) == 2
    agent, task_manager = result
    assert isinstance(task_manager, TaskListManager)


@pytest.mark.asyncio
async def test_build_orchestrator_agent_uses_tasklist_planner():
    """build_orchestrator_agent configura el planner como 'tasklist'."""
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.config.schema import FrameworkConfig
    from agent_framework.providers.mock_provider import MockProvider

    bus = EventBus()
    registry = ModelRegistry()
    registry.register("orchestrator", MockProvider("ok"))
    config = FrameworkConfig()

    alpaca, storage, news = _make_mock_infrastructure()
    sub_agents = _make_mock_sub_agents()

    call_log = {}

    original_build = AgentBuilder.build

    def capture_build(self):
        call_log["planner_type"] = self._planner_type
        call_log["model_role"] = self._model_role
        call_log["tool_names"] = [t.name for t in self._tools]
        call_log["max_outer"] = self._max_outer_iterations
        call_log["has_state_provider"] = self._state_provider is not None
        call_log["has_completion_checker"] = self._completion_checker is not None
        call_log["has_cycle_hooks"] = self._cycle_complete_getter is not None
        mock = MagicMock()
        mock.run = AsyncMock(return_value="done")
        return mock

    with patch.object(AgentBuilder, "build", capture_build):
        await build_orchestrator_agent(
            config=config,
            bus=bus,
            model_registry=registry,
            sub_agents=sub_agents,
            alpaca_client=alpaca,
            storage=storage,
        )

    assert call_log["planner_type"] == "tasklist"
    assert call_log["model_role"] == "orchestrator"


@pytest.mark.asyncio
async def test_build_orchestrator_agent_registers_all_tools():
    """build_orchestrator_agent registra todas las tools necesarias."""
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.config.schema import FrameworkConfig

    bus = EventBus()
    registry = ModelRegistry()
    config = FrameworkConfig()

    alpaca, storage, news = _make_mock_infrastructure()
    sub_agents = _make_mock_sub_agents()

    call_log = {}

    def capture_build(self):
        call_log["tool_names"] = [t.name for t in self._tools]
        mock = MagicMock()
        mock.run = AsyncMock(return_value="done")
        return mock

    with patch.object(AgentBuilder, "build", capture_build):
        await build_orchestrator_agent(
            config=config,
            bus=bus,
            model_registry=registry,
            sub_agents=sub_agents,
            alpaca_client=alpaca,
            storage=storage,
        )

    tool_names = call_log["tool_names"]
    # Core orchestrator tools
    assert "update_task_list" in tool_names
    assert "get_symbol_state" in tool_names
    assert "update_symbol_state" in tool_names
    assert "get_market_clock" in tool_names
    assert "get_price" in tool_names
    assert "get_historical_bars" in tool_names
    assert "get_last_report" in tool_names
    assert "get_market_context" in tool_names
    assert "place_order" in tool_names
    assert "get_order_status" in tool_names
    assert "get_portfolio" in tool_names
    assert "run_sub_agent" in tool_names


@pytest.mark.asyncio
async def test_build_orchestrator_agent_wires_state_provider_to_manager():
    """El state_provider del agente está conectado al TaskListManager."""
    from agent_framework.events import EventBus
    from agent_framework.models.model_registry import ModelRegistry
    from agent_framework.config.schema import FrameworkConfig

    bus = EventBus()
    registry = ModelRegistry()
    config = FrameworkConfig()

    alpaca, storage, news = _make_mock_infrastructure()
    sub_agents = _make_mock_sub_agents()

    captured = {}

    def capture_build(self):
        captured["state_provider"] = self._state_provider
        captured["completion_checker"] = self._completion_checker
        captured["cycle_getter"] = self._cycle_complete_getter
        captured["cycle_resetter"] = self._cycle_complete_resetter
        mock = MagicMock()
        mock.run = AsyncMock(return_value="done")
        return mock

    with patch.object(AgentBuilder, "build", capture_build):
        _, task_manager = await build_orchestrator_agent(
            config=config,
            bus=bus,
            model_registry=registry,
            sub_agents=sub_agents,
            alpaca_client=alpaca,
            storage=storage,
        )

    # Verify closures are wired correctly
    task_manager.reset(["AAPL", "MSFT"])
    state_str = captured["state_provider"]()
    assert "AAPL" in state_str
    assert "MSFT" in state_str

    assert captured["completion_checker"]() is False  # still pending
    task_manager.mark_task("AAPL", "completed", "done")
    task_manager.mark_task("MSFT", "completed", "done")
    assert captured["completion_checker"]() is True

    task_manager.reset(["X"])
    task_manager.mark_task("X", "completed", "done")
    assert captured["cycle_getter"]() is True
    captured["cycle_resetter"]()
    assert captured["cycle_getter"]() is False


# ── Integración: TaskListManager + UpdateTaskListTool ──────────────────────────

class TestTaskListIntegration:
    @pytest.mark.asyncio
    async def test_full_cycle_two_symbols(self):
        """Simula el procesamiento de dos símbolos: AAPL y MSFT."""
        mgr = TaskListManager()
        tool = UpdateTaskListTool(mgr)

        mgr.reset(["AAPL", "MSFT"])
        assert mgr.is_complete() is False

        # Simular: TaskListPlanner procesa AAPL
        state1 = mgr.state_str()
        assert "Process next: **AAPL**" in state1
        mgr.reset_cycle()
        assert mgr.get_cycle_complete() is False

        await tool.execute(symbol="AAPL", status="completed", summary="HOLD — not due")
        assert mgr.get_cycle_complete() is True
        assert mgr.is_complete() is False

        # Simular: TaskListPlanner resetea cycle y procesa MSFT
        mgr.reset_cycle()
        state2 = mgr.state_str()
        assert "AAPL [COMPLETED]" in state2.upper()
        assert "Process next: **MSFT**" in state2

        await tool.execute(symbol="MSFT", status="completed", summary="OPPORTUNITY_FOUND")
        assert mgr.get_cycle_complete() is True
        assert mgr.is_complete() is True

    @pytest.mark.asyncio
    async def test_failed_task_still_advances(self):
        """Una tarea fallida no bloquea el avance al siguiente símbolo."""
        mgr = TaskListManager()
        tool = UpdateTaskListTool(mgr)

        mgr.reset(["AAPL", "MSFT"])
        await tool.execute(symbol="AAPL", status="failed", summary="exception")
        mgr.reset_cycle()
        await tool.execute(symbol="MSFT", status="completed", summary="done")
        assert mgr.is_complete() is True
