"""
PriceMonitor — loop de monitorización de precios.

Consulta Alpaca a intervalos regulares para cada símbolo en la watchlist.
Cuando detecta un cambio de precio superior al threshold, dispara el ReAct trading agent.
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_framework.config.config_loader import load_from_yaml
from agent_framework.events import EventBus, EventType, Event
from agent_framework.models.model_registry import ModelRegistry
from agent_framework.providers import OllamaProvider

from src.alpaca import AlpacaClient
from src.storage import StorageLayer
from .trader import build_trading_agent


class PriceMonitor:
    def __init__(self, alpaca: AlpacaClient, storage: StorageLayer, trading_agent, on_log=None):
        self._alpaca = alpaca
        self._storage = storage
        self._agent = trading_agent
        self._on_log = on_log
        self._running = False

    def _log(self, msg: str):
        if self._on_log:
            self._on_log(msg)
        else:
            print(msg)

    async def run(self, poll_interval_seconds: int = 60, stop_event: asyncio.Event | None = None):
        """Loop principal de monitorización."""
        self._running = True
        self._log(f"[monitor] Iniciando monitorización (intervalo: {poll_interval_seconds}s)")

        while self._running:
            if stop_event and stop_event.is_set():
                break

            watchlist = self._storage.get_watchlist()
            if not watchlist:
                self._log("[monitor] Watchlist vacía — añade símbolos desde el dashboard")
            else:
                for item in watchlist:
                    if not self._running:
                        break
                    try:
                        await self._check_symbol(item)
                    except Exception as e:
                        self._log(f"[monitor] Error revisando {item['symbol']}: {e}")

            try:
                await asyncio.wait_for(
                    _wait_or_stop(stop_event, poll_interval_seconds),
                    timeout=poll_interval_seconds + 1,
                )
            except asyncio.TimeoutError:
                pass

        self._log("[monitor] Monitorización detenida")

    async def _check_symbol(self, item: dict):
        symbol = item["symbol"]
        threshold = item.get("threshold_pct", 2.0)

        try:
            bar = await self._alpaca.get_latest_bar(symbol)
        except Exception as e:
            self._log(f"[monitor] {symbol}: error obteniendo precio — {e}")
            return

        current_price = bar["close"]
        bar_time = bar.get("timestamp", "")

        last_price = self._storage.get_last_price(symbol)
        self._storage.save_price_snapshot(symbol, current_price, bar.get("volume"), bar_time)

        if last_price is None:
            self._log(f"[monitor] {symbol}: precio base registrado — ${current_price:.2f}")
            return

        change_pct = abs((current_price - last_price) / last_price * 100)
        direction = "▲" if current_price > last_price else "▼"
        self._log(f"[monitor] {symbol}: ${current_price:.2f} {direction} {change_pct:.2f}% (prev: ${last_price:.2f})")

        if change_pct >= threshold:
            self._log(
                f"[monitor] {symbol}: cambio {change_pct:.1f}% ≥ threshold {threshold}% "
                f"→ disparando análisis"
            )
            await self._trigger_analysis(symbol, current_price, change_pct, last_price)

    async def _trigger_analysis(
        self, symbol: str, current_price: float, change_pct: float, prev_price: float
    ):
        direction = "risen" if current_price > prev_price else "dropped"
        prompt = (
            f"PRICE ALERT: {symbol} has {direction} {change_pct:.1f}% "
            f"from ${prev_price:.2f} to ${current_price:.2f}.\n"
            f"Analyze this price movement using the available tools. "
            f"Check the price trend, search for relevant news about {symbol}, "
            f"review the portfolio, and decide: BUY, SELL, or HOLD."
        )

        # Limpiar memoria del agente para análisis independiente
        await self._agent._context.memory.clear()

        try:
            result = await self._agent.run(prompt)
            self._log(f"[{symbol}] Decisión del agente: {result}")
        except Exception as e:
            self._log(f"[{symbol}] Error en el agente: {e}")

    def stop(self):
        self._running = False


async def _wait_or_stop(stop_event: asyncio.Event | None, seconds: int):
    """Espera `seconds` segundos o hasta que stop_event se active."""
    if stop_event:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(seconds)


async def run_monitor(on_log=None, stop_event: asyncio.Event | None = None):
    """
    Inicializa todos los componentes y arranca el loop de monitorización.
    Diseñado para ejecutarse con asyncio.run() desde un thread de Tkinter.
    """
    def log(msg):
        if on_log:
            on_log(msg)
        else:
            print(msg)

    # Credenciales
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    trading_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    gnews_key = os.environ.get("GNEWS_API_KEY")
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    if not api_key or not secret_key:
        log("[error] ALPACA_API_KEY y ALPACA_SECRET_KEY requeridas en .env")
        return
    if not gnews_key:
        log("[error] GNEWS_API_KEY requerida en .env")
        return

    # Inicializar Alpaca
    alpaca = AlpacaClient(api_key, secret_key, trading_url)
    log(f"[monitor] Conectando a Alpaca ({trading_url})...")
    if not await alpaca.is_available():
        log("[error] No se puede conectar con Alpaca — verifica las credenciales")
        return
    account = await alpaca.get_account()
    log(f"[monitor] Cuenta OK — equity: ${account['equity']:.2f} | cash: ${account['cash']:.2f}")

    # Storage
    storage = StorageLayer(db_path="trading_agent.db")

    # Rellenar watchlist desde .env si está vacía
    watchlist = storage.get_watchlist()
    if not watchlist:
        symbols_env = os.environ.get("WATCHLIST_SYMBOLS", "AAPL,MSFT,NVDA")
        for sym in [s.strip().upper() for s in symbols_env.split(",") if s.strip()]:
            storage.upsert_watchlist(sym)
        log(f"[monitor] Watchlist inicializada desde .env: {symbols_env}")

    # Framework config
    config_path = Path(__file__).parent / "trading_agent.yaml"
    config = load_from_yaml(config_path)

    trading_cfg = config.models.get("trader")
    if not trading_cfg:
        log("[error] Modelo 'trader' no encontrado en trading_agent.yaml")
        return

    model_registry = ModelRegistry()
    model_registry.register("trader", OllamaProvider(trading_cfg.model, base_url=ollama_url))
    log(f"[monitor] Modelo trader: {trading_cfg.model}")

    # EventBus
    bus = EventBus()

    async def on_event(event):
        if event.type == EventType.MODEL_CALLED:
            log(f"[agent] Llamando al modelo ({trading_cfg.model})...")
        elif event.type == EventType.TOOL_CALLED:
            log(f"[agent] Tool: {event.data.get('tool_name', '?')}")

    bus.subscribe("*", on_event)

    # Leer configuración de trading del YAML
    trading_opts = config.trading
    max_qty = trading_opts.get("max_qty_per_order", 10)
    poll_interval = trading_opts.get("poll_interval_seconds", 60)

    # Construir ReAct trading agent
    agent = await build_trading_agent(
        config=config,
        bus=bus,
        model_registry=model_registry,
        alpaca_client=alpaca,
        storage=storage,
        gnews_api_key=gnews_key,
        max_qty_per_order=max_qty,
    )

    # Arrancar monitor
    monitor = PriceMonitor(alpaca, storage, agent, on_log=on_log)
    await monitor.run(poll_interval_seconds=poll_interval, stop_event=stop_event)


def run_monitor_sync(on_log=None, stop_event: asyncio.Event | None = None):
    """Wrapper síncrono para Tkinter thread."""
    asyncio.run(run_monitor(on_log=on_log, stop_event=stop_event))
