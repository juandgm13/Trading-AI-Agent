"""Tool: obtiene barras históricas OHLCV desde Alpaca."""
import json
from agent_framework.tools.base_tool import BaseTool


class GetHistoricalBarsTool(BaseTool):
    name = "get_historical_bars"
    description = (
        "Get historical OHLCV bars for a stock symbol from Alpaca. "
        "Use timeframe='1Day' for medium-term analysis (up to 90 days), "
        "or '15Min'/'1Hour' for short-term intraday analysis."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
            "timeframe": {
                "type": "string",
                "enum": ["1Day", "15Min", "1Hour"],
                "description": "Bar timeframe. 1Day for medium-term, 15Min/1Hour for short-term.",
                "default": "1Day",
            },
            "limit": {
                "type": "integer",
                "description": "Number of bars to retrieve (max 1000, default 90)",
                "default": 90,
            },
        },
        "required": ["symbol"],
    }

    def __init__(self, alpaca_client):
        self._alpaca = alpaca_client

    async def execute(self, symbol: str, timeframe: str = "1Day", limit: int = 90) -> str:
        symbol = symbol.upper().strip()
        limit = min(limit, 1000)

        try:
            bars = await self._alpaca.get_bars(symbol, timeframe=timeframe, limit=limit)
        except Exception as e:
            return json.dumps({"error": f"Failed to get bars for {symbol}: {e}"})

        if not bars:
            return json.dumps({"error": f"No data available for {symbol}"})

        # Compact CSV format to reduce LLM context window usage
        # (~50 chars/bar vs ~120 chars/bar in verbose JSON)
        csv_lines = ["date,open,high,low,close,volume"]
        for b in bars:
            date = (b.get("timestamp") or "")[:10]  # YYYY-MM-DD
            csv_lines.append(
                f"{date},"
                f"{b.get('open', '')},"
                f"{b.get('high', '')},"
                f"{b.get('low', '')},"
                f"{b.get('close', '')},"
                f"{b.get('volume', '')}"
            )

        return json.dumps({
            "symbol": symbol,
            "timeframe": timeframe,
            "bars_count": len(bars),
            "format": "CSV: date,open,high,low,close,volume",
            "bars": "\n".join(csv_lines),
        })
