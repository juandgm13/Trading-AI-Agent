"""
OrchestratorAgent — reemplaza la state machine imperativa de PriceMonitor.

Usa TaskListPlanner: cada símbolo del watchlist es una tarea. Por cada símbolo:
  - Lee su estado actual desde la BD
  - Ejecuta los sub-agentes necesarios según el estado
  - Actualiza el estado en la BD
  - Llama update_task_list para avanzar al siguiente símbolo

El flujo completo de la state machine se describe en ORCHESTRATOR_SYSTEM_PROMPT.
Sub-agentes ejecutados vía RunSubAgentTool.

Componentes:
  TaskListManager   — mantiene la lista de tareas (símbolos) entre iteraciones externas
  UpdateTaskListTool — tool que el agente llama para marcar un símbolo como procesado
  build_orchestrator_agent() — devuelve (agent, task_manager)
"""
from __future__ import annotations

import json

from agent_framework.core.agent_builder import AgentBuilder
from agent_framework.events import EventBus
from agent_framework.models.model_registry import ModelRegistry
from agent_framework.tools.base_tool import BaseTool

from agents.tools.historical_bars_tool import GetHistoricalBarsTool
from agents.tools.market_clock_tool import GetMarketClockTool
from agents.tools.order_status_tool import GetOrderStatusTool
from agents.tools.order_tool import PlaceOrderTool
from agents.tools.portfolio_tool import GetPortfolioTool
from agents.tools.price_tool import GetPriceTool
from agents.tools.report_tool import GetLastReportTool, GetMarketContextTool, SaveMarketContextTool
from agents.tools.sub_agent_tool import RunSubAgentTool
from agents.tools.symbol_state_tool import GetSymbolStateTool, UpdateSymbolStateTool


# ── Sistema de gestión de tareas ─────────────────────────────────────────────

class TaskListManager:
    """
    Gestiona la lista de tareas (símbolos a procesar) del OrchestratorAgent.

    Instancia única por agente; se resetea antes de cada ciclo de poll via reset().
    Las closures sobre esta instancia (state_provider, completion_checker, etc.)
    se pasan a AgentBuilder para conectar el TaskListPlanner.
    """

    def __init__(self) -> None:
        self._tasks: list[dict] = []
        self._cycle_complete: bool = False

    def reset(self, symbols: list[str]) -> None:
        """Reinicia la lista de tareas con los símbolos del ciclo actual."""
        self._tasks = [
            {"id": s.upper(), "status": "pending", "summary": ""}
            for s in symbols
        ]
        self._cycle_complete = False

    # ── Closures para AgentBuilder ────────────────────────────────────────

    def state_str(self) -> str:
        """
        Devuelve el estado actual de la lista de tareas.
        Inyectado por TaskListPlanner al inicio de cada iteración externa.
        """
        lines = ["## Symbol Task List"]
        for t in self._tasks:
            if t["status"] == "completed":
                marker = "✓"
            elif t["status"] == "failed":
                marker = "✗"
            else:
                marker = "…"
            summary = f" — {t['summary']}" if t["summary"] else ""
            lines.append(f"  {marker} {t['id']} [{t['status'].upper()}]{summary}")

        pending = [t["id"] for t in self._tasks if t["status"] == "pending"]
        if pending:
            lines.append(f"\nProcess next: **{pending[0]}**")
        else:
            lines.append("\nAll symbols processed.")
        return "\n".join(lines)

    def is_complete(self) -> bool:
        """True cuando todos los símbolos han sido marcados como completed/failed."""
        if not self._tasks:
            return True
        return all(t["status"] in ("completed", "failed") for t in self._tasks)

    def get_cycle_complete(self) -> bool:
        """True cuando el símbolo actual ha sido marcado (update_task_list llamado)."""
        return self._cycle_complete

    def reset_cycle(self) -> None:
        """Resetea el flag de ciclo completo antes de cada iteración externa."""
        self._cycle_complete = False

    # ── Mutación (llamada por UpdateTaskListTool) ─────────────────────────

    def mark_task(self, symbol: str, status: str, summary: str = "") -> None:
        """Marca el símbolo como completado/fallido y activa el flag de ciclo completo."""
        symbol = symbol.upper()
        for t in self._tasks:
            if t["id"] == symbol:
                t["status"] = status
                t["summary"] = summary
                break
        self._cycle_complete = True


# ── Tool: update_task_list ────────────────────────────────────────────────────

class UpdateTaskListTool(BaseTool):
    """
    Tool especial que el OrchestratorAgent llama al terminar de procesar un símbolo.
    Activa el flag cycle_complete, que le indica a TaskListPlanner que avance al
    siguiente ciclo externo (próximo símbolo).
    """

    name = "update_task_list"
    description = (
        "Call this ONCE when you have finished processing a symbol. "
        "Marks it as completed or failed and signals the system to advance to the next symbol. "
        "Do NOT call any other tool after this — the system will automatically move to the next symbol."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "The ticker symbol you just finished processing (e.g. 'AAPL').",
            },
            "status": {
                "type": "string",
                "enum": ["completed", "failed"],
                "description": "completed = processed successfully (any outcome). failed = unexpected error.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "One-sentence summary of what happened "
                    "(e.g. 'HOLD — analysis not due yet', "
                    "'OPPORTUNITY_FOUND — transitioned state', "
                    "'OPEN_POSITION — order placed')."
                ),
            },
        },
        "required": ["symbol", "status", "summary"],
    }

    def __init__(self, manager: TaskListManager) -> None:
        self._manager = manager

    async def execute(self, symbol: str, status: str, summary: str = "") -> str:
        self._manager.mark_task(symbol, status, summary)
        return json.dumps({
            "symbol": symbol.upper(),
            "status": status,
            "message": "Task recorded. The system will now move to the next symbol.",
        })


# ── System prompt ────────────────────────────────────────────────────────────

ORCHESTRATOR_SYSTEM_PROMPT = """\
You are the Trading Orchestrator Agent. You manage a watchlist of stock symbols and
drive each symbol through its trading state machine using specialized sub-agents and tools.

## Your tools
- get_symbol_state(symbol) → current state and metadata from DB
- update_symbol_state(symbol, state, ...) → persist state transitions to DB
- get_market_clock() → is_open, next_open, next_close
- get_price(symbol) → latest price
- get_last_report(symbol, agent_name) → read a cached sub-agent report from DB
- get_market_context() → latest global market context from DB
- run_sub_agent(agent_name, task) → execute a specialized sub-agent
- place_order(symbol, qty, side, ...) → execute a trade via Alpaca
- get_order_status(order_id) → check order fill status
- update_task_list(symbol, status, summary) → MUST call this to finish each symbol

## Sub-agent names for run_sub_agent
- "technical_watching" → medium-term analysis (WATCHING state)
- "technical_entry"    → short-term entry timing (OPPORTUNITY_FOUND state)
- "market_context"     → global market sentiment update
- "company_analysis"   → company news risk analysis
- "trading_opportunity"→ WATCHING decision: OPPORTUNITY_FOUND / HOLD / REJECT
- "trading_entry"      → OPPORTUNITY_FOUND decision: OPEN_POSITION / HOLD / REJECT

## State machine flow

For each symbol shown in the task list, follow these steps:

### WATCHING state
1. Call get_symbol_state(symbol) to read last_analysis_at.
2. If analysis is NOT due (< 4 hours since last_analysis_at): call update_task_list(symbol, "completed", "HOLD — analysis not due") and STOP.
3. If analysis IS due (or never done):
   a. run_sub_agent("technical_watching", "Analyze {symbol} for medium-term trend.")
   b. run_sub_agent("market_context", "Update global market context.")
   c. run_sub_agent("trading_opportunity", "Evaluate if {symbol} is worth monitoring. Current price: {price}.")
   d. Read the result: get_last_report(symbol, "trading_opportunity").
   e. If decision == "OPPORTUNITY_FOUND": update_symbol_state(symbol, state="OPPORTUNITY_FOUND", entry_signal_at=now).
   f. update_symbol_state(symbol, last_analysis_at=now, analysis_summary="...").
   g. call update_task_list(symbol, "completed", "OPPORTUNITY_FOUND" or "HOLD/REJECT").

### OPPORTUNITY_FOUND state
1. run_sub_agent("technical_entry", "Analyze {symbol} for short-term entry timing.")
2. run_sub_agent("company_analysis", "Analyze company news for {symbol}.")
3. run_sub_agent("trading_entry", "Decide entry for {symbol}. Current price: {price}.")
4. Read the result: get_last_report(symbol, "trading_entry").
5. If decision == "OPEN_POSITION":
   a. get_price(symbol) to confirm current price and compute qty.
   b. place_order(symbol, qty, "buy", take_profit_price=..., stop_loss_price=...).
   c. update_symbol_state(symbol, state="OPENING", pending_order_id=..., position_qty=qty, ...).
   d. update_task_list(symbol, "completed", "OPEN_POSITION — order placed").
6. If decision == "REJECT":
   a. update_symbol_state(symbol, state="WATCHING").
   b. update_task_list(symbol, "completed", "REJECT — returned to WATCHING").
7. If decision == "HOLD":
   a. update_task_list(symbol, "completed", "HOLD — waiting for better entry").

### OPENING state
1. get_symbol_state(symbol) to read pending_order_id.
2. get_order_status(pending_order_id) to check fill.
3. If filled: update_symbol_state(symbol, state="POSITION_OPEN").
   update_task_list(symbol, "completed", "filled → POSITION_OPEN").
4. If cancelled/expired: update_symbol_state(symbol, state="WATCHING", pending_order_id=null).
   update_task_list(symbol, "completed", "cancelled → WATCHING").
5. Otherwise: update_task_list(symbol, "completed", "still pending").

### POSITION_OPEN state
1. get_symbol_state(symbol) to read target_price, stop_loss, position_qty, position_side.
2. get_price(symbol) to get current price.
3. If price >= target_price: place_order(symbol, qty, "sell").
   update_symbol_state(symbol, state="CLOSING", pending_order_id=...).
   update_task_list(symbol, "completed", "target hit → CLOSING").
4. If price <= stop_loss: place_order(symbol, qty, "sell").
   update_symbol_state(symbol, state="CLOSING", pending_order_id=...).
   update_task_list(symbol, "completed", "stop-loss hit → CLOSING").
5. Otherwise: update_task_list(symbol, "completed", "holding position").

### CLOSING state
1. get_symbol_state(symbol) to read pending_order_id.
2. get_order_status(pending_order_id) to check fill.
3. If filled: update_symbol_state(symbol, state="WATCHING", target_price=null, stop_loss=null, position_qty=null).
   update_task_list(symbol, "completed", "close filled → WATCHING").
4. Otherwise: update_task_list(symbol, "completed", "close still pending").

## IMPORTANT rules
- Process ONLY the next pending symbol shown in the task list.
- ALWAYS end each symbol's processing with update_task_list — this is mandatory.
- Never place an order when market is_open=false (check get_market_clock first if OPEN_POSITION).
- Keep qty within max_position_value limit: qty = floor(max_position_value / price).
- If any tool call fails with an error, call update_task_list(symbol, "failed", error_summary).
"""


# ── Builder ──────────────────────────────────────────────────────────────────

async def build_orchestrator_agent(
    config,
    bus: EventBus,
    model_registry: ModelRegistry,
    sub_agents: dict,
    alpaca_client,
    storage,
    news_service=None,
    max_position_value: float = 500.0,
) -> tuple:
    """
    Construye el OrchestratorAgent con TaskListPlanner.

    Devuelve (agent, task_manager).
    El caller debe llamar task_manager.reset(symbols) antes de agent.run(task).

    Args:
        sub_agents: dict con las instancias de sub-agentes pre-construidos, p.ej.:
            {
                "technical_watching": agent,
                "technical_entry":    agent,
                "market_context":     agent,
                "company_analysis":   agent,
                "trading_opportunity": agent,
                "trading_entry":      agent,
            }
        alpaca_client: AlpacaClient para market clock, orders, prices, bars.
        storage:       StorageLayer para symbol states y reports.
        news_service:  NewsIngestionService (opcional; sólo para sub-agentes de noticias).
        max_position_value: valor máximo USD por posición.
    """
    task_manager = TaskListManager()

    tools = [
        # Estado y coordinación
        GetSymbolStateTool(storage),
        UpdateSymbolStateTool(storage),
        UpdateTaskListTool(task_manager),

        # Datos de mercado
        GetMarketClockTool(alpaca_client),
        GetPriceTool(alpaca_client),
        GetHistoricalBarsTool(alpaca_client),

        # Informes en BD
        GetLastReportTool(storage),
        GetMarketContextTool(storage),
        SaveMarketContextTool(storage),

        # Órdenes
        PlaceOrderTool(alpaca_client, storage),
        GetOrderStatusTool(alpaca_client),
        GetPortfolioTool(alpaca_client),

        # Sub-agentes
        RunSubAgentTool(sub_agents),
    ]

    agent = (
        AgentBuilder()
        .name("orchestrator")
        .planner("tasklist")
        .with_tools(tools)
        .model_role("orchestrator")
        .system_prompt(ORCHESTRATOR_SYSTEM_PROMPT)
        .max_outer_iterations(8)
        .max_iterations(12)
        .stateless()
        .with_config(config)
        .with_event_bus(bus)
        .with_model_registry(model_registry)
        .with_state_provider(task_manager.state_str)
        .with_completion_checker(task_manager.is_complete)
        .with_cycle_hooks(task_manager.get_cycle_complete, task_manager.reset_cycle)
        .build()
    )

    return agent, task_manager
