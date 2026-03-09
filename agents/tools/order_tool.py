"""Tool: ejecuta órdenes de compra/venta en Alpaca con límites de seguridad."""
import json
from agent_framework.tools.base_tool import BaseTool


class PlaceOrderTool(BaseTool):
    name = "place_order"
    description = (
        "Place a market buy or sell order via Alpaca. "
        "Only use this after you have gathered sufficient price and news information "
        "and have HIGH confidence in the decision. "
        "Always provide a reason explaining why you are placing this order."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol (e.g. AAPL)",
            },
            "side": {
                "type": "string",
                "enum": ["buy", "sell"],
                "description": "Direction of the trade",
            },
            "qty": {
                "type": "number",
                "description": "Number of shares to trade (must be > 0)",
            },
            "reason": {
                "type": "string",
                "description": "One sentence explaining the rationale for this trade decision",
            },
        },
        "required": ["symbol", "side", "qty", "reason"],
    }

    def __init__(self, alpaca_client, storage, max_qty_per_order: int = 10, alerter=None):
        self._alpaca = alpaca_client
        self._storage = storage
        self._max_qty = max_qty_per_order
        self._alerter = alerter  # TradeAlerter | None

    async def execute(self, symbol: str, side: str, qty: float, reason: str) -> str:
        symbol = symbol.upper().strip()

        # Seguridad: límite de cantidad por orden
        if qty <= 0:
            return json.dumps({"error": "qty must be greater than 0"})
        if qty > self._max_qty:
            return json.dumps({
                "error": f"qty {qty} exceeds max allowed per order ({self._max_qty})",
                "hint": f"Reduce qty to {self._max_qty} or less",
            })

        # Seguridad: para vender, verificar que tenemos la posición
        if side == "sell":
            position = await self._alpaca.get_position(symbol)
            if position is None:
                return json.dumps({
                    "error": f"Cannot sell {symbol}: no open position found",
                    "hint": "You can only sell shares you currently hold",
                })
            available_qty = position["qty"]
            if qty > available_qty:
                return json.dumps({
                    "error": f"Cannot sell {qty} shares of {symbol}: only {available_qty} held",
                })

        # Ejecutar orden
        try:
            result = await self._alpaca.place_order(symbol, qty, side)
        except Exception as e:
            return json.dumps({"error": f"Order failed: {e}"})

        # Obtener precio aproximado para registro
        approx_price = None
        try:
            bar = await self._alpaca.get_latest_bar(symbol)
            approx_price = bar.get("close")
        except Exception:
            pass

        # Persistir en SQLite
        self._storage.save_trade(
            symbol=symbol,
            side=side,
            qty=qty,
            price=approx_price,
            order_id=result.get("order_id"),
            status=result.get("status", "submitted"),
            trigger="agent_decision",
            reason=reason,
        )

        # Enviar alerta por email (no bloquea si falla)
        if self._alerter:
            try:
                self._alerter.send_trade_alert(
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=approx_price,
                    order_id=result.get("order_id"),
                    reason=reason,
                )
            except Exception:
                pass

        return json.dumps({
            "order_id": result.get("order_id"),
            "status": result.get("status"),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "approx_price": approx_price,
            "reason": reason,
        })
