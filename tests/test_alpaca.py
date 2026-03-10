"""
Tests de AlpacaClient — requieren ALPACA_API_KEY y ALPACA_SECRET_KEY en .env.
Usan paper trading, no ejecutan órdenes reales.
"""
import pytest
from conftest import requires_alpaca


@requires_alpaca
@pytest.mark.asyncio
async def test_is_available(alpaca):
    ok, err = await alpaca.is_available()
    assert ok is True, f"Alpaca no disponible: {err}"


@requires_alpaca
@pytest.mark.asyncio
async def test_get_account_fields(alpaca):
    account = await alpaca.get_account()

    assert "cash" in account
    assert "equity" in account
    assert "portfolio_value" in account
    assert "buying_power" in account
    assert account["cash"] >= 0
    assert account["equity"] >= 0


@requires_alpaca
@pytest.mark.asyncio
async def test_get_latest_bar(alpaca):
    bar = await alpaca.get_latest_bar("AAPL")

    assert "close" in bar
    assert "volume" in bar
    assert "timestamp" in bar
    assert bar["close"] > 0


@requires_alpaca
@pytest.mark.asyncio
async def test_get_bars_returns_requested_count(alpaca):
    bars = await alpaca.get_bars("MSFT", timeframe="1Min", limit=5)

    assert isinstance(bars, list)
    assert len(bars) >= 1  # puede ser menos si el mercado cerró
    for b in bars:
        assert "open" in b
        assert "high" in b
        assert "low" in b
        assert "close" in b


@requires_alpaca
@pytest.mark.asyncio
async def test_get_snapshots_multiple_symbols(alpaca):
    symbols = ["AAPL", "MSFT", "NVDA"]
    snaps = await alpaca.get_snapshots(symbols)

    assert isinstance(snaps, dict)
    # Al menos algunos deben tener datos (mercado puede estar cerrado)
    assert len(snaps) > 0
    for symbol, data in snaps.items():
        assert symbol in symbols
        assert "close" in data


@requires_alpaca
@pytest.mark.asyncio
async def test_get_snapshots_empty_list(alpaca):
    snaps = await alpaca.get_snapshots([])
    assert snaps == {}


@requires_alpaca
@pytest.mark.asyncio
async def test_get_positions_returns_list(alpaca):
    positions = await alpaca.get_positions()
    assert isinstance(positions, list)
    # En paper trading puede estar vacío, eso es válido
    for pos in positions:
        assert "symbol" in pos
        assert "qty" in pos
        assert "market_value" in pos


@requires_alpaca
@pytest.mark.asyncio
async def test_get_position_none_for_unowned(alpaca):
    # Un símbolo que muy probablemente no tengamos en paper
    result = await alpaca.get_position("XYZABC123")
    assert result is None


@requires_alpaca
@pytest.mark.asyncio
async def test_get_assets_returns_nonempty_list(alpaca):
    assets = await alpaca.get_assets()

    assert isinstance(assets, list)
    assert len(assets) > 100  # Alpaca tiene miles de activos
    first = assets[0]
    assert "symbol" in first
    assert "name" in first
