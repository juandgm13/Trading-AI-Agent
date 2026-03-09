"""Tool: consulta precios e histórico de barras desde Alpaca."""
import json
from agent_framework.tools.base_tool import BaseTool


class GetPriceTool(BaseTool):
    name = "get_price"
    description = (
        "Get current price and recent bar data for a stock symbol from Alpaca. "
        "Returns close price, volume and recent trend."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL, MSFT, NVDA)",
            },
            "bars": {
                "type": "integer",
                "description": "Number of recent 1-minute bars to retrieve (default: 5)",
                "default": 5,
            },
        },
        "required": ["symbol"],
    }

    def __init__(self, alpaca_client):
        self._alpaca = alpaca_client

    async def execute(self, symbol: str, bars: int = 5) -> str:
        symbol = symbol.upper().strip()
        bar_list = await self._alpaca.get_bars(symbol, timeframe="1Min", limit=bars)

        if not bar_list:
            return json.dumps({"error": f"No data available for {symbol}"})

        current_price = bar_list[0]["close"]
        oldest_price = bar_list[-1]["close"] if len(bar_list) > 1 else current_price
        change_pct = ((current_price - oldest_price) / oldest_price * 100) if oldest_price else 0.0

        return json.dumps({
            "symbol": symbol,
            "current_price": round(current_price, 4),
            "change_pct_recent": round(change_pct, 2),
            "bars_count": len(bar_list),
            "bars": [
                {
                    "timestamp": b["timestamp"],
                    "open": b["open"],
                    "high": b["high"],
                    "low": b["low"],
                    "close": b["close"],
                    "volume": b["volume"],
                }
                for b in bar_list
            ],
        })
