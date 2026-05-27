"""Tool: consulta el estado del reloj de mercado en Alpaca."""
import json
from agent_framework.tools.base_tool import BaseTool


class GetMarketClockTool(BaseTool):
    name = "get_market_clock"
    description = (
        "Get current US stock market status from Alpaca. "
        "Returns whether the market is currently open, and the timestamps "
        "of the next open and close. Use this before placing orders to confirm "
        "the market is open."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, alpaca_client):
        self._alpaca = alpaca_client

    async def execute(self) -> str:
        try:
            clock = await self._alpaca.get_clock()
        except Exception as e:
            return json.dumps({"error": f"Failed to get market clock: {e}"})

        return json.dumps({
            "is_open": clock.get("is_open", False),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
            "timestamp": clock.get("timestamp"),
        })
