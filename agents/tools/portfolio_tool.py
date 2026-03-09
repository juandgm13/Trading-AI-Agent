"""Tool: consulta el estado actual del portfolio en Alpaca."""
import json
from agent_framework.tools.base_tool import BaseTool


class GetPortfolioTool(BaseTool):
    name = "get_portfolio"
    description = (
        "Get current portfolio status from Alpaca: available cash, equity, "
        "and all open positions with their unrealized P&L."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, alpaca_client):
        self._alpaca = alpaca_client

    async def execute(self) -> str:
        account = await self._alpaca.get_account()
        positions = await self._alpaca.get_positions()

        return json.dumps({
            "cash": round(account["cash"], 2),
            "equity": round(account["equity"], 2),
            "portfolio_value": round(account["portfolio_value"], 2),
            "buying_power": round(account["buying_power"], 2),
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": p["qty"],
                    "avg_entry_price": round(p["avg_entry_price"], 4),
                    "current_price": round(p["current_price"], 4),
                    "market_value": round(p["market_value"], 2),
                    "unrealized_pl": round(p["unrealized_pl"], 2),
                    "unrealized_plpc": round(p["unrealized_plpc"] * 100, 2),
                }
                for p in positions
            ],
        })
