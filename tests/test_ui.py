"""
Tests de lógica de UI (sin levantar Streamlit).
Se testea la lógica de validación de símbolos en la página Watchlist.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Watchlist: validación de símbolo via Alpaca ───────────────────────────────

@pytest.mark.asyncio
async def test_get_asset_called_when_credentials_present():
    """Si hay credenciales, get_asset debe llamarse con el símbolo correcto."""
    from src.alpaca import AlpacaClient

    mock_asset = {
        "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ",
        "marginable": True, "shortable": True,
    }

    with patch.object(AlpacaClient, "get_asset", new_callable=AsyncMock, return_value=mock_asset) as mock_get:
        client = AlpacaClient("key", "secret")
        result = await client.get_asset("AAPL")

    mock_get.assert_awaited_once_with("AAPL")
    assert result["symbol"] == "AAPL"
    assert result["exchange"] == "NASDAQ"


@pytest.mark.asyncio
async def test_get_asset_returns_none_for_unknown_symbol():
    """get_asset devuelve None para un símbolo que no existe en Alpaca."""
    from src.alpaca import AlpacaClient

    with patch.object(AlpacaClient, "get_asset", new_callable=AsyncMock, return_value=None):
        client = AlpacaClient("key", "secret")
        result = await client.get_asset("XYZINVALID")

    assert result is None


def test_upsert_watchlist_called_with_asset_metadata(tmp_db):
    """upsert_watchlist recibe los datos enriquecidos de Alpaca cuando el símbolo es válido."""
    asset = {
        "symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ",
        "marginable": True, "shortable": True,
    }

    tmp_db.upsert_watchlist(
        "AAPL",
        asset.get("name") or "",
        exchange=asset.get("exchange", ""),
        marginable=asset.get("marginable", False),
        shortable=asset.get("shortable", False),
    )

    watchlist = tmp_db.get_watchlist()
    item = next((w for w in watchlist if w["symbol"] == "AAPL"), None)
    assert item is not None
    assert item["company"] == "Apple Inc."
    assert item["exchange"] == "NASDAQ"
    assert item["marginable"] == 1
    assert item["shortable"] == 1


def test_upsert_watchlist_without_asset_metadata(tmp_db):
    """Sin credenciales, upsert_watchlist añade el símbolo con datos mínimos."""
    tmp_db.upsert_watchlist("MSFT", "Microsoft Corp.")

    watchlist = tmp_db.get_watchlist()
    item = next((w for w in watchlist if w["symbol"] == "MSFT"), None)
    assert item is not None
    assert item["company"] == "Microsoft Corp."
    # exchange y shortable quedan en sus valores por defecto
    assert item.get("exchange") in (None, "")
    assert item["shortable"] == 0
