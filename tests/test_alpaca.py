"""
Tests de AlpacaClient.

Sección 1 — Unit tests de logging y métodos síncronos (sin credenciales, sin red).
Sección 2 — Unit tests con HTTP mockeado (aiohttp).
Sección 3 — Integration tests contra la API real (requieren .env con Alpaca).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from conftest import requires_alpaca


# ── Helper: mock de sesión aiohttp ────────────────────────────────────────────

def _mock_http(method: str, status: int, json_data):
    """
    Devuelve un context manager que parchea aiohttp.ClientSession para que
    la llamada HTTP indicada (get/post/delete) devuelva status + json_data.
    """
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=json_data)

    mock_req_ctx = MagicMock()
    mock_req_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_req_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    getattr(mock_session, method).return_value = mock_req_ctx

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    return patch("aiohttp.ClientSession", return_value=mock_session_ctx)


# ── Unit tests: AgentLogger.is_debug ─────────────────────────────────────────

def test_logger_is_debug_true_at_debug_level():
    from src.logger import AgentLogger
    assert AgentLogger(level="DEBUG").is_debug is True


def test_logger_is_debug_false_at_info_level():
    from src.logger import AgentLogger
    assert AgentLogger(level="INFO").is_debug is False


def test_logger_is_debug_false_at_warning_level():
    from src.logger import AgentLogger
    assert AgentLogger(level="WARNING").is_debug is False


def test_logger_is_debug_false_at_error_level():
    from src.logger import AgentLogger
    assert AgentLogger(level="ERROR").is_debug is False


# ── Unit tests: AlpacaClient._log_response ────────────────────────────────────

def test_log_response_calls_debug_when_is_debug():
    from src.alpaca import AlpacaClient

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    client = AlpacaClient("key", "secret", logger=mock_logger)
    client._log_response("GET", "https://api.alpaca.markets/v2/account", 200, {"cash": "100000"})

    mock_logger.debug.assert_called_once()
    msg = mock_logger.debug.call_args[0][0]
    assert "GET" in msg
    assert "200" in msg
    assert "cash" in msg


def test_log_response_includes_source_alpaca():
    from src.alpaca import AlpacaClient

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    client = AlpacaClient("key", "secret", logger=mock_logger)
    client._log_response("POST", "https://api.alpaca.markets/v2/orders", 200, {})

    kwargs = mock_logger.debug.call_args[1]
    assert kwargs.get("source") == "alpaca"


def test_log_response_silent_when_is_debug_false():
    from src.alpaca import AlpacaClient

    mock_logger = MagicMock()
    mock_logger.is_debug = False

    client = AlpacaClient("key", "secret", logger=mock_logger)
    client._log_response("GET", "https://api.alpaca.markets/v2/account", 200, {"cash": "100000"})

    mock_logger.debug.assert_not_called()


def test_log_response_silent_when_no_logger():
    from src.alpaca import AlpacaClient

    client = AlpacaClient("key", "secret")
    # No debe lanzar excepción
    client._log_response("GET", "https://api.alpaca.markets/v2/account", 200, {"cash": "100000"})


def test_log_response_truncates_large_payload():
    from src.alpaca import AlpacaClient, _MAX_LOG_CHARS

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    large_data = {"key": "x" * 10000}
    client = AlpacaClient("key", "secret", logger=mock_logger)
    client._log_response("GET", "https://api.alpaca.markets/v2/assets", 200, large_data)

    msg = mock_logger.debug.call_args[0][0]
    assert "truncado" in msg
    # El mensaje truncado no debe exceder el límite + overhead razonable
    assert len(msg) < _MAX_LOG_CHARS + 200


def test_log_response_no_truncation_for_small_payload():
    from src.alpaca import AlpacaClient

    mock_logger = MagicMock()
    mock_logger.is_debug = True

    small_data = {"symbol": "AAPL", "close": 175.0}
    client = AlpacaClient("key", "secret", logger=mock_logger)
    client._log_response("GET", "https://api.alpaca.markets/v2/bars", 200, small_data)

    msg = mock_logger.debug.call_args[0][0]
    assert "truncado" not in msg
    assert "AAPL" in msg


def test_alpaca_client_accepts_logger_kwarg():
    """AlpacaClient almacena el logger pasado como parámetro."""
    from src.alpaca import AlpacaClient

    mock_logger = MagicMock()
    client = AlpacaClient("key", "secret", logger=mock_logger)
    assert client._logger is mock_logger


def test_alpaca_client_logger_none_by_default():
    """Sin logger explícito, _logger es None."""
    from src.alpaca import AlpacaClient

    client = AlpacaClient("key", "secret")
    assert client._logger is None


# ── Unit tests: métodos síncronos ─────────────────────────────────────────────

def test_is_crypto_detects_slash_format():
    from src.alpaca import AlpacaClient
    client = AlpacaClient("k", "s")
    assert client.is_crypto("BTC/USD") is True
    assert client.is_crypto("ETH/USDT") is True
    assert client.is_crypto("XRP/BTC") is True


def test_is_crypto_detects_known_bases():
    from src.alpaca import AlpacaClient
    client = AlpacaClient("k", "s")
    assert client.is_crypto("BTC") is True
    assert client.is_crypto("ETH") is True
    assert client.is_crypto("DOGE") is True
    assert client.is_crypto("SOL") is True


def test_is_crypto_false_for_equities():
    from src.alpaca import AlpacaClient
    client = AlpacaClient("k", "s")
    assert client.is_crypto("AAPL") is False
    assert client.is_crypto("MSFT") is False
    assert client.is_crypto("NVDA") is False
    assert client.is_crypto("TSLA") is False


def test_is_crypto_case_insensitive():
    from src.alpaca import AlpacaClient
    client = AlpacaClient("k", "s")
    assert client.is_crypto("btc/usd") is True
    assert client.is_crypto("aapl") is False


# ── Unit tests: HTTP mockeado ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_latest_bar_raises_when_no_bars():
    from src.alpaca import AlpacaClient

    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, {"bars": []}):
        with pytest.raises(ValueError, match="No bars returned"):
            await client.get_latest_bar("AAPL")


@pytest.mark.asyncio
async def test_get_latest_bar_returns_first_bar():
    from src.alpaca import AlpacaClient

    bar_data = {"bars": [{"t": "2024-01-01", "o": 174.0, "h": 176.0, "l": 173.0, "c": 175.0, "v": 1000}]}
    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, bar_data):
        bar = await client.get_latest_bar("AAPL")
    assert bar["close"] == 175.0
    assert bar["volume"] == 1000


@pytest.mark.asyncio
async def test_get_bars_day_timeframe_uses_wider_start_window():
    """Para timeframe 1Day, start = limit*2 días atrás (branch Day en get_bars)."""
    from datetime import datetime, timezone, timedelta
    from src.alpaca import AlpacaClient

    bar_data = {"bars": [{"t": "2024-01-01", "o": 170.0, "h": 175.0, "l": 169.0, "c": 173.0, "v": 1_000_000}]}
    client = AlpacaClient("k", "s")

    captured_params = {}

    def _fake_get(url, params=None):
        captured_params.update(params or {})
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=bar_data)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    mock_session = MagicMock()
    mock_session.get.side_effect = _fake_get
    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    limit = 90
    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        await client.get_bars("AAPL", timeframe="1Day", limit=limit)

    start_str = captured_params.get("start", "")
    expected_min = (datetime.now(timezone.utc) - timedelta(days=limit * 2 + 1)).strftime("%Y-%m-%d")
    assert start_str[:10] >= expected_min  # start >= limit*2 días atrás
    assert captured_params["timeframe"] == "1Day"


@pytest.mark.asyncio
async def test_get_position_returns_none_on_404():
    from src.alpaca import AlpacaClient

    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_resp.raise_for_status = MagicMock()

    mock_req_ctx = MagicMock()
    mock_req_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_req_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_req_ctx

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    client = AlpacaClient("k", "s")
    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        result = await client.get_position("AAPL")
    assert result is None


@pytest.mark.asyncio
async def test_get_position_returns_data_on_200():
    from src.alpaca import AlpacaClient

    pos_data = {
        "symbol": "AAPL", "qty": "5", "avg_entry_price": "170.0",
        "current_price": "175.0", "market_value": "875.0", "unrealized_pl": "25.0",
    }
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=pos_data)

    mock_req_ctx = MagicMock()
    mock_req_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_req_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_req_ctx

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    client = AlpacaClient("k", "s")
    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        result = await client.get_position("AAPL")
    assert result["symbol"] == "AAPL"
    assert result["qty"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_place_order_returns_expected_fields():
    from src.alpaca import AlpacaClient

    order_data = {
        "id": "ord-001", "client_order_id": "cid-001",
        "status": "accepted", "symbol": "AAPL",
        "side": "buy", "qty": "2", "submitted_at": "2024-01-01T10:00:00Z",
    }
    client = AlpacaClient("k", "s")
    with _mock_http("post", 200, order_data):
        result = await client.place_order("AAPL", 2, "buy")

    assert result["order_id"] == "ord-001"
    assert result["status"] == "accepted"
    assert result["symbol"] == "AAPL"
    assert result["side"] == "buy"
    assert result["qty"] == pytest.approx(2.0)
    assert result["submitted_at"] == "2024-01-01T10:00:00Z"


@pytest.mark.asyncio
async def test_cancel_all_orders_completes_without_error():
    from src.alpaca import AlpacaClient

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    mock_req_ctx = MagicMock()
    mock_req_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_req_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.delete.return_value = mock_req_ctx

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    client = AlpacaClient("k", "s")
    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        await client.cancel_all_orders()  # no debe lanzar excepción

    mock_resp.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_is_available_returns_false_on_exception():
    from src.alpaca import AlpacaClient

    client = AlpacaClient("k", "s")
    # get_account lanzará excepción porque aiohttp no puede conectar sin mock
    # Usamos un mock que lanza
    client.get_account = AsyncMock(side_effect=ConnectionError("Connection refused"))
    ok, err = await client.is_available()

    assert ok is False
    assert "ConnectionError" in err


@pytest.mark.asyncio
async def test_is_available_returns_true_on_success():
    from src.alpaca import AlpacaClient

    client = AlpacaClient("k", "s")
    client.get_account = AsyncMock(return_value={"cash": 100000.0, "equity": 100000.0})
    ok, err = await client.is_available()

    assert ok is True
    assert err == ""


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


# ── Unit tests: get_asset ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_asset_returns_fields():
    from src.alpaca import AlpacaClient

    asset_data = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "class": "us_equity",
    }
    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, asset_data):
        result = await client.get_asset("AAPL")

    assert result is not None
    assert result["symbol"] == "AAPL"
    assert result["name"] == "Apple Inc."
    assert result["exchange"] == "NASDAQ"
    assert result["marginable"] is True
    assert result["shortable"] is True


@pytest.mark.asyncio
async def test_get_asset_not_found_returns_none():
    from src.alpaca import AlpacaClient

    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_resp.raise_for_status = MagicMock()

    mock_req_ctx = MagicMock()
    mock_req_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_req_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_req_ctx

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    client = AlpacaClient("k", "s")
    with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
        result = await client.get_asset("XYZNOTREAL")
    assert result is None


@pytest.mark.asyncio
async def test_get_asset_crypto_returns_none():
    """get_asset devuelve None para activos con exchange=CRYPTO."""
    from src.alpaca import AlpacaClient

    asset_data = {
        "symbol": "BTCUSD",
        "name": "Bitcoin",
        "exchange": "CRYPTO",
        "tradable": True,
        "marginable": False,
        "shortable": False,
    }
    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, asset_data):
        result = await client.get_asset("BTCUSD")
    assert result is None


@pytest.mark.asyncio
async def test_get_asset_non_tradable_returns_none():
    """get_asset devuelve None para activos no negociables."""
    from src.alpaca import AlpacaClient

    asset_data = {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "tradable": False,
        "marginable": False,
        "shortable": False,
    }
    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, asset_data):
        result = await client.get_asset("AAPL")
    assert result is None


# ── Unit tests: get_clock ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_clock_market_open():
    from src.alpaca import AlpacaClient

    clock_data = {
        "is_open": True,
        "next_open": "2026-03-18T13:30:00Z",
        "next_close": "2026-03-17T20:00:00Z",
        "timestamp": "2026-03-17T15:00:00Z",
    }
    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, clock_data):
        result = await client.get_clock()

    assert result["is_open"] is True
    assert result["next_close"] == "2026-03-17T20:00:00Z"
    assert result["timestamp"] == "2026-03-17T15:00:00Z"


@pytest.mark.asyncio
async def test_get_clock_market_closed():
    from src.alpaca import AlpacaClient

    clock_data = {
        "is_open": False,
        "next_open": "2026-03-18T13:30:00Z",
        "next_close": "2026-03-18T20:00:00Z",
        "timestamp": "2026-03-17T22:00:00Z",
    }
    client = AlpacaClient("k", "s")
    with _mock_http("get", 200, clock_data):
        result = await client.get_clock()

    assert result["is_open"] is False
    assert result["next_open"] == "2026-03-18T13:30:00Z"
