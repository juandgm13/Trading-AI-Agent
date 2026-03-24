"""
Tests de las tools del agente ReAct.
- GetPriceTool / GetPortfolioTool: requieren Alpaca
- PlaceOrderTool: validaciones sin credenciales + orden real con Alpaca
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from conftest import requires_alpaca, requires_all


# ── GetPriceTool ──────────────────────────────────────────────────────────────

@requires_alpaca
@pytest.mark.asyncio
async def test_get_price_tool_returns_valid_json(alpaca):
    from agents.tools.price_tool import GetPriceTool

    tool = GetPriceTool(alpaca)
    result = await tool.execute(symbol="AAPL", bars=3)

    data = json.loads(result)
    assert data.get("symbol") == "AAPL"
    assert "current_price" in data
    assert data["current_price"] > 0
    assert "change_pct_recent" in data
    assert "bars" in data


@requires_alpaca
@pytest.mark.asyncio
async def test_get_price_tool_lowercased_symbol(alpaca):
    from agents.tools.price_tool import GetPriceTool

    tool = GetPriceTool(alpaca)
    result = await tool.execute(symbol="aapl")
    data = json.loads(result)
    assert data["symbol"] == "AAPL"


def test_get_price_tool_error_on_bad_symbol():
    """Símbolo inválido — el cliente Alpaca mock lanza excepción."""
    from agents.tools.price_tool import GetPriceTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_bars = AsyncMock(return_value=[])
    tool = GetPriceTool(mock_alpaca)

    import asyncio
    result = asyncio.run(tool.execute(symbol="ZZZZINVALID"))
    data = json.loads(result)
    assert "error" in data


# ── GetPortfolioTool ──────────────────────────────────────────────────────────

@requires_alpaca
@pytest.mark.asyncio
async def test_get_portfolio_tool_returns_account_data(alpaca):
    from agents.tools.portfolio_tool import GetPortfolioTool

    tool = GetPortfolioTool(alpaca)
    result = await tool.execute()

    data = json.loads(result)
    assert "cash" in data
    assert "equity" in data
    assert "positions" in data
    assert isinstance(data["positions"], list)
    assert data["cash"] >= 0


# ── PlaceOrderTool — validaciones (sin llamadas reales) ──────────────────────

@pytest.mark.asyncio
async def test_place_order_rejects_zero_qty():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="buy", qty=0, reason="test")
    data = json.loads(result)
    assert "error" in data
    mock_alpaca.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_order_rejects_exceeding_max_qty():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="buy", qty=50, reason="test")
    data = json.loads(result)
    assert "error" in data
    assert "50" in data["error"] or "max" in data["error"].lower()
    mock_alpaca.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_order_rejects_sell_without_position():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_position = AsyncMock(return_value=None)  # no tenemos posición
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="sell", qty=5, reason="test")
    data = json.loads(result)
    assert "error" in data
    mock_alpaca.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_order_buy_success_saves_to_db():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.place_order = AsyncMock(return_value={
        "order_id": "test-order-id",
        "status": "accepted",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 2.0,
    })
    mock_alpaca.get_latest_bar = AsyncMock(return_value={"close": 175.0, "volume": 100})
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="buy", qty=2, reason="Strong signal")
    data = json.loads(result)

    assert data["order_id"] == "test-order-id"
    assert data["symbol"] == "AAPL"
    assert data["side"] == "buy"
    mock_storage.save_trade.assert_called_once()


# ── PlaceOrderTool: paths adicionales ────────────────────────────────────────

@pytest.mark.asyncio
async def test_place_order_rejects_sell_exceeding_held_qty():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.get_position = AsyncMock(return_value={"qty": 3.0})
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="sell", qty=5, reason="test")
    data = json.loads(result)
    assert "error" in data
    mock_alpaca.place_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_order_returns_error_when_order_raises():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.place_order = AsyncMock(side_effect=RuntimeError("API down"))
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="buy", qty=2, reason="test")
    data = json.loads(result)
    assert "error" in data
    assert "API down" in data["error"]


@pytest.mark.asyncio
async def test_place_order_succeeds_when_get_latest_bar_raises():
    """get_latest_bar falla silenciosamente — orden se completa igual."""
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.place_order = AsyncMock(return_value={
        "order_id": "ord-ok", "status": "accepted",
        "symbol": "AAPL", "side": "buy", "qty": 1.0,
    })
    mock_alpaca.get_latest_bar = AsyncMock(side_effect=ConnectionError("timeout"))
    mock_storage = MagicMock()
    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10)

    result = await tool.execute(symbol="AAPL", side="buy", qty=1, reason="test")
    data = json.loads(result)
    assert data["order_id"] == "ord-ok"
    assert data["approx_price"] is None  # no se obtuvo precio


@pytest.mark.asyncio
async def test_place_order_alerter_exception_does_not_fail_order():
    """Si el alerter lanza excepción, la orden ya fue ejecutada y se devuelve OK."""
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.place_order = AsyncMock(return_value={
        "order_id": "ord-xy", "status": "accepted",
        "symbol": "MSFT", "side": "buy", "qty": 1.0,
    })
    mock_alpaca.get_latest_bar = AsyncMock(return_value={"close": 320.0})
    mock_storage = MagicMock()
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert = MagicMock(side_effect=Exception("SMTP error"))

    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10, alerter=mock_alerter)
    result = await tool.execute(symbol="MSFT", side="buy", qty=1, reason="test")
    data = json.loads(result)
    assert data["order_id"] == "ord-xy"  # orden OK a pesar del fallo de alerta


@pytest.mark.asyncio
async def test_place_order_calls_alerter_on_success():
    from agents.tools.order_tool import PlaceOrderTool

    mock_alpaca = MagicMock()
    mock_alpaca.place_order = AsyncMock(return_value={
        "order_id": "ord-xyz", "status": "accepted",
        "symbol": "MSFT", "side": "buy", "qty": 1.0,
    })
    mock_alpaca.get_latest_bar = AsyncMock(return_value={"close": 320.0})
    mock_storage = MagicMock()
    mock_alerter = MagicMock()
    mock_alerter.send_trade_alert = MagicMock(return_value=True)

    tool = PlaceOrderTool(mock_alpaca, mock_storage, max_qty_per_order=10, alerter=mock_alerter)
    await tool.execute(symbol="MSFT", side="buy", qty=1, reason="Momentum confirmed")

    mock_alerter.send_trade_alert.assert_called_once()
    call_kwargs = mock_alerter.send_trade_alert.call_args
    assert call_kwargs.kwargs["symbol"] == "MSFT"
    assert call_kwargs.kwargs["side"] == "buy"
