"""
Alpaca REST client — precios de mercado y ejecución de órdenes.

Usa dos base URLs:
- DATA_URL: para datos de mercado (barras, cotizaciones)
- TRADING_URL: para cuenta, posiciones y órdenes
"""
import json
import aiohttp
from datetime import datetime, timezone


DATA_URL = "https://data.alpaca.markets/v2"

_MAX_LOG_CHARS = 4000


class AlpacaClient:
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        trading_url: str = "https://paper-api.alpaca.markets",
        logger=None,
    ):
        self._api_key = api_key
        self._secret_key = secret_key
        self._trading_url = trading_url.rstrip("/")
        self._logger = logger
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }

    def _log_response(self, method: str, url: str, status: int, data) -> None:
        """Loguea la respuesta cruda de la API si el logger está en modo DEBUG."""
        if self._logger is None or not self._logger.is_debug:
            return
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw) > _MAX_LOG_CHARS:
            raw = raw[:_MAX_LOG_CHARS] + f"… [truncado {len(raw)} chars]"
        self._logger.debug(f"{method} {url} → {status} | {raw}", source="alpaca")

    # ── Datos de mercado ──────────────────────────────────────────────────────

    async def get_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 5) -> list[dict]:
        """Devuelve los últimos `limit` bars OHLCV para el símbolo."""
        from datetime import timedelta
        url = f"{DATA_URL}/stocks/{symbol}/bars"
        # Calcular start: ventana generosa para cubrir días no negociables
        if "Day" in timeframe:
            start = (datetime.now(timezone.utc) - timedelta(days=limit * 2)).strftime("%Y-%m-%dT00:00:00Z")
        else:
            start = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        params = {
            "timeframe": timeframe,
            "limit": limit,
            "sort": "desc",
            "start": start,
            "feed": "iex",
        }
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._log_response("GET", url, resp.status, data)
        bars = data.get("bars") or []
        result = []
        for b in bars:
            result.append({
                "timestamp": b.get("t"),
                "open": b.get("o"),
                "high": b.get("h"),
                "low": b.get("l"),
                "close": b.get("c"),
                "volume": b.get("v"),
            })
        return result

    async def get_latest_bar(self, symbol: str) -> dict:
        """Devuelve el bar más reciente para el símbolo."""
        bars = await self.get_bars(symbol, timeframe="1Min", limit=1)
        if not bars:
            raise ValueError(f"No bars returned for {symbol}")
        return bars[0]

    # ── Cuenta y posiciones ───────────────────────────────────────────────────

    async def get_account(self) -> dict:
        """Devuelve información de la cuenta: cash, equity, portfolio_value."""
        url = f"{self._trading_url}/v2/account"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._log_response("GET", url, resp.status, data)
        return {
            "cash": float(data.get("cash", 0)),
            "equity": float(data.get("equity", 0)),
            "portfolio_value": float(data.get("portfolio_value", 0)),
            "buying_power": float(data.get("buying_power", 0)),
            "currency": data.get("currency", "USD"),
        }

    async def get_positions(self) -> list[dict]:
        """Devuelve todas las posiciones abiertas (solo US equities, excluye crypto)."""
        url = f"{self._trading_url}/v2/positions"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._log_response("GET", url, resp.status, data)
        return [
            {
                "symbol": p.get("symbol"),
                "qty": float(p.get("qty", 0)),
                "avg_entry_price": float(p.get("avg_entry_price", 0)),
                "current_price": float(p.get("current_price", 0)),
                "market_value": float(p.get("market_value", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
                "unrealized_plpc": float(p.get("unrealized_plpc", 0)),
            }
            for p in data
            if p.get("asset_class") == "us_equity"
        ]

    async def get_position(self, symbol: str) -> dict | None:
        """Devuelve la posición abierta para un símbolo, o None si no existe."""
        url = f"{self._trading_url}/v2/positions/{symbol}"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                p = await resp.json()
                self._log_response("GET", url, resp.status, p)
        return {
            "symbol": p.get("symbol"),
            "qty": float(p.get("qty", 0)),
            "avg_entry_price": float(p.get("avg_entry_price", 0)),
            "current_price": float(p.get("current_price", 0)),
            "market_value": float(p.get("market_value", 0)),
            "unrealized_pl": float(p.get("unrealized_pl", 0)),
        }

    # ── Órdenes ───────────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str = "market",
    ) -> dict:
        """
        Envía una orden de mercado.

        Args:
            symbol: Ticker (e.g. "AAPL")
            qty: Número de acciones
            side: "buy" o "sell"
            order_type: "market" (default)

        Returns:
            dict con order_id, status, symbol, side, qty
        """
        url = f"{self._trading_url}/v2/orders"
        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": "day",
        }
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._log_response("POST", url, resp.status, data)
        return {
            "order_id": data.get("id"),
            "client_order_id": data.get("client_order_id"),
            "status": data.get("status"),
            "symbol": data.get("symbol"),
            "side": data.get("side"),
            "qty": float(data.get("qty", qty)),
            "submitted_at": data.get("submitted_at"),
        }

    async def cancel_all_orders(self) -> None:
        """Cancela todas las órdenes pendientes."""
        url = f"{self._trading_url}/v2/orders"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.delete(url) as resp:
                resp.raise_for_status()

    async def is_available(self) -> tuple[bool, str]:
        """
        Comprueba conectividad con la API de Alpaca.

        Returns:
            (True, "") si OK, (False, mensaje_de_error) si falla.
        """
        try:
            await self.get_account()
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    # ── Assets ────────────────────────────────────────────────────────────────

    async def get_assets(self, asset_class: str = "us_equity") -> list[dict]:
        """
        Devuelve todos los activos activos y negociables en Alpaca (excluye crypto).

        Returns:
            Lista de dicts con {symbol, name} para cada activo negociable.
        """
        url = f"{self._trading_url}/v2/assets"
        params = {"status": "active", "asset_class": asset_class, "tradable": "true"}
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._log_response("GET", url, resp.status, data)
        return [
            {
                "symbol": a["symbol"],
                "name": a.get("name", ""),
                "exchange": a.get("exchange", ""),
                "tradable": a.get("tradable", False),
                "marginable": a.get("marginable", False),
                "shortable": a.get("shortable", False),
            }
            for a in data
            if a.get("tradable")
            and a.get("status") == "active"
            and a.get("exchange") != "CRYPTO"
            and a.get("class") == "us_equity"
        ]

    def is_crypto(self, symbol: str) -> bool:
        """
        Heurística rápida para detectar símbolos de criptomonedas.

        Alpaca usa formatos como BTC/USD, ETH/USD, BTCUSD para crypto.
        """
        symbol = symbol.upper().strip()
        crypto_suffixes = ("/USD", "/USDT", "/BTC", "/ETH")
        crypto_bases = {"BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "DOT", "AVAX", "MATIC"}
        return (
            any(symbol.endswith(s) for s in crypto_suffixes)
            or symbol in crypto_bases
            or "USD" in symbol and "/" in symbol
        )

    async def get_snapshots(self, symbols: list[str]) -> dict[str, dict]:
        """
        Consulta precios para múltiples símbolos en una sola llamada.

        Returns:
            dict {symbol: {close, volume, timestamp}} con los datos más recientes.
        """
        if not symbols:
            return {}
        url = f"{DATA_URL}/stocks/snapshots"
        params = {"symbols": ",".join(symbols), "feed": "iex"}
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._log_response("GET", url, resp.status, data)
        result = {}
        for symbol, snap in data.items():
            bar = snap.get("minuteBar") or snap.get("dailyBar") or {}
            result[symbol] = {
                "timestamp": bar.get("t"),
                "close": bar.get("c"),
                "volume": bar.get("v"),
            }
        return result
