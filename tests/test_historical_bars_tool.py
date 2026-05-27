"""
Tests unitarios de GetHistoricalBarsTool.
No requieren credenciales — mockean AlpacaClient.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_tool(bars=None, raise_exc=None):
    mock_alpaca = MagicMock()
    if raise_exc:
        mock_alpaca.get_bars = AsyncMock(side_effect=raise_exc)
    else:
        mock_alpaca.get_bars = AsyncMock(return_value=bars or [])
    from agents.tools.historical_bars_tool import GetHistoricalBarsTool
    return GetHistoricalBarsTool(mock_alpaca), mock_alpaca


SAMPLE_BARS = [
    {"timestamp": "2024-01-01T00:00:00Z", "open": 170.0, "high": 176.0,
     "low": 169.0, "close": 175.0, "volume": 50000},
    {"timestamp": "2023-12-31T00:00:00Z", "open": 168.0, "high": 171.0,
     "low": 167.0, "close": 170.0, "volume": 45000},
]


@pytest.mark.asyncio
async def test_execute_returns_bars():
    tool, _ = _make_tool(bars=SAMPLE_BARS)
    result = await tool.execute("AAPL")
    data = json.loads(result)

    assert data["symbol"] == "AAPL"
    assert data["bars_count"] == 2
    assert data["timeframe"] == "1Day"
    assert "bars" in data


@pytest.mark.asyncio
async def test_execute_bars_format_is_csv():
    """bars debe ser una cadena CSV con cabecera y una fila por barra."""
    tool, _ = _make_tool(bars=SAMPLE_BARS)
    result = await tool.execute("AAPL")
    data = json.loads(result)

    bars_csv = data["bars"]
    assert isinstance(bars_csv, str)
    lines = bars_csv.strip().splitlines()
    assert lines[0] == "date,open,high,low,close,volume"
    assert len(lines) == 3  # header + 2 data rows


@pytest.mark.asyncio
async def test_execute_csv_contains_close_value():
    """El valor de cierre debe aparecer en las líneas CSV."""
    tool, _ = _make_tool(bars=SAMPLE_BARS)
    result = await tool.execute("AAPL")
    data = json.loads(result)

    assert "175.0" in data["bars"]
    assert "170.0" in data["bars"]


@pytest.mark.asyncio
async def test_execute_csv_date_is_truncated_to_yyyy_mm_dd():
    """El timestamp completo debe reducirse a YYYY-MM-DD para ahorrar tokens."""
    tool, _ = _make_tool(bars=SAMPLE_BARS)
    result = await tool.execute("AAPL")
    data = json.loads(result)

    assert "2024-01-01" in data["bars"]
    assert "T00:00:00Z" not in data["bars"]


@pytest.mark.asyncio
async def test_execute_uppercases_symbol():
    tool, mock_alpaca = _make_tool(bars=SAMPLE_BARS)
    await tool.execute("aapl")
    call_args = mock_alpaca.get_bars.call_args
    assert call_args[0][0] == "AAPL"


@pytest.mark.asyncio
async def test_execute_strips_symbol():
    tool, mock_alpaca = _make_tool(bars=SAMPLE_BARS)
    await tool.execute("  MSFT  ")
    assert mock_alpaca.get_bars.call_args[0][0] == "MSFT"


@pytest.mark.asyncio
async def test_execute_default_timeframe_is_1day():
    tool, mock_alpaca = _make_tool(bars=SAMPLE_BARS)
    await tool.execute("AAPL")
    assert mock_alpaca.get_bars.call_args[1]["timeframe"] == "1Day"


@pytest.mark.asyncio
async def test_execute_custom_timeframe():
    tool, mock_alpaca = _make_tool(bars=SAMPLE_BARS)
    await tool.execute("AAPL", timeframe="15Min", limit=50)
    assert mock_alpaca.get_bars.call_args[1]["timeframe"] == "15Min"
    assert mock_alpaca.get_bars.call_args[1]["limit"] == 50


@pytest.mark.asyncio
async def test_execute_caps_limit_at_1000():
    tool, mock_alpaca = _make_tool(bars=SAMPLE_BARS)
    await tool.execute("AAPL", limit=9999)
    assert mock_alpaca.get_bars.call_args[1]["limit"] == 1000


@pytest.mark.asyncio
async def test_execute_returns_error_when_no_bars():
    tool, _ = _make_tool(bars=[])
    result = await tool.execute("AAPL")
    data = json.loads(result)
    assert "error" in data
    assert "AAPL" in data["error"]


@pytest.mark.asyncio
async def test_execute_returns_error_on_exception():
    tool, _ = _make_tool(raise_exc=ConnectionError("Network error"))
    result = await tool.execute("AAPL")
    data = json.loads(result)
    assert "error" in data
    assert "AAPL" in data["error"]
