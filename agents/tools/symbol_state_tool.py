"""Tools: lee y escribe el estado de la máquina de estados por símbolo en la BD."""
import json
from datetime import datetime, timezone
from agent_framework.tools.base_tool import BaseTool


class GetSymbolStateTool(BaseTool):
    name = "get_symbol_state"
    description = (
        "Read the current state machine state for a stock symbol from the database. "
        "Returns state (WATCHING/OPPORTUNITY_FOUND/OPENING/POSITION_OPEN/CLOSING), "
        "last_analysis_at, target_price, stop_loss, position_qty, pending_order_id, "
        "bracket_order_id and other relevant fields."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
        },
        "required": ["symbol"],
    }

    def __init__(self, storage):
        self._storage = storage

    async def execute(self, symbol: str) -> str:
        symbol = symbol.upper().strip()
        state = self._storage.get_symbol_state(symbol)
        return json.dumps(state)


class UpdateSymbolStateTool(BaseTool):
    name = "update_symbol_state"
    description = (
        "Persist a state transition for a stock symbol to the database. "
        "Call this after every decision to ensure crash recovery. "
        "Only provide the fields you want to update — other fields are preserved. "
        "Valid states: WATCHING, OPPORTUNITY_FOUND, OPENING, POSITION_OPEN, CLOSING."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
            "state": {
                "type": "string",
                "enum": ["WATCHING", "OPPORTUNITY_FOUND", "OPENING", "POSITION_OPEN", "CLOSING"],
                "description": "New state to transition to",
            },
            "target_price": {
                "type": "number",
                "description": "Take-profit target price (set when entering OPENING)",
            },
            "stop_loss": {
                "type": "number",
                "description": "Stop-loss price (set when entering OPENING)",
            },
            "position_qty": {
                "type": "number",
                "description": "Number of shares in the open position",
            },
            "position_side": {
                "type": "string",
                "enum": ["long", "short"],
                "description": "Position direction (default: long)",
            },
            "pending_order_id": {
                "type": "string",
                "description": "Alpaca order ID for a pending order (OPENING or CLOSING state)",
            },
            "bracket_order_id": {
                "type": "string",
                "description": "Alpaca bracket order ID (main leg of a bracket order)",
            },
            "analysis_summary": {
                "type": "string",
                "description": "One-sentence summary of the analysis that led to this state change",
            },
            "entry_signal_at": {
                "type": "string",
                "description": "ISO 8601 timestamp when the entry signal was detected",
            },
            "last_analysis_at": {
                "type": "string",
                "description": "ISO 8601 timestamp of the last full analysis cycle",
            },
            "chart_path": {
                "type": "string",
                "description": "Path to the evidence candlestick chart PNG (if generated)",
            },
        },
        "required": ["symbol"],
    }

    # Fields that map directly to upsert_symbol_state kwargs
    _ALLOWED_FIELDS = {
        "state", "target_price", "stop_loss", "position_qty", "position_side",
        "pending_order_id", "bracket_order_id", "analysis_summary",
        "entry_signal_at", "last_analysis_at", "chart_path",
    }

    def __init__(self, storage):
        self._storage = storage

    async def execute(self, symbol: str, **kwargs) -> str:
        symbol = symbol.upper().strip()

        # Auto-set last_analysis_at when transitioning WATCHING→OPPORTUNITY_FOUND
        # so the orchestrator doesn't need to know the current timestamp
        if kwargs.get("state") in ("OPPORTUNITY_FOUND", "WATCHING") and "last_analysis_at" not in kwargs:
            kwargs["last_analysis_at"] = datetime.now(timezone.utc).isoformat()

        # Filter to allowed fields only
        update_kwargs = {k: v for k, v in kwargs.items() if k in self._ALLOWED_FIELDS and v is not None}

        try:
            self._storage.upsert_symbol_state(symbol, **update_kwargs)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e), "symbol": symbol})

        return json.dumps({
            "ok": True,
            "symbol": symbol,
            "updated_fields": list(update_kwargs.keys()),
            "new_state": update_kwargs.get("state", "(unchanged)"),
        })
