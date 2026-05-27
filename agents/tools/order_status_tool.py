"""Tool: consulta el estado de una orden en Alpaca (incluyendo bracket orders)."""
import json
from agent_framework.tools.base_tool import BaseTool


class GetOrderStatusTool(BaseTool):
    name = "get_order_status"
    description = (
        "Check the current status of an Alpaca order by its order ID. "
        "For bracket orders, also returns the status of the take-profit and stop-loss legs. "
        "Use this in OPENING state to check if the buy was filled, "
        "and in CLOSING state to check if the sell was filled."
    )
    parameters = {
        "type": "object",
        "properties": {
            "order_id": {
                "type": "string",
                "description": "Alpaca order ID (UUID format)",
            },
        },
        "required": ["order_id"],
    }

    def __init__(self, alpaca_client):
        self._alpaca = alpaca_client

    async def execute(self, order_id: str) -> str:
        order_id = order_id.strip()

        try:
            order = await self._alpaca.get_order(order_id)
        except Exception as e:
            return json.dumps({"error": f"Failed to get order {order_id}: {e}"})

        if order is None:
            return json.dumps({
                "found": False,
                "order_id": order_id,
                "message": "Order not found",
            })

        return json.dumps({
            "found": True,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "filled_avg_price": order.get("filled_avg_price"),
            "filled_qty": order.get("filled_qty"),
            "commission": order.get("commission", 0.0),
            "legs": order.get("legs", []),
        })
