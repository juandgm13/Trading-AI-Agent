"""
PriceMonitor — loop de monitorización con OrchestratorAgent.

El OrchestratorAgent (TaskListPlanner) gestiona la state machine completa
de cada símbolo usando tool calls: análisis técnico, económico, decisiones
de trading y ejecución de órdenes. El monitor solo se ocupa del poll loop,
precios en batch, standby de mercado cerrado y lifecycle del agente.

Estados por símbolo (gestionados por el OrchestratorAgent):
  WATCHING         → análisis completo cada analysis_interval_hours
  OPPORTUNITY_FOUND → análisis de corto plazo cada ciclo
  OPENING          → orden de compra enviada, esperando confirmación de fill
  POSITION_OPEN    → seguimiento de target_price y stop_loss
  CLOSING          → orden de venta enviada, esperando confirmación de fill

Si WATCHLIST_SYMBOLS no está definido en .env, se usan TODOS los activos
negociables de Alpaca (US equities activos). Los precios se consultan en
batch (snapshots) para minimizar llamadas a la API.
"""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

from agent_framework.config.config_loader import load_from_yaml
from agent_framework.events import EventBus, EventType
from agent_framework.models.model_registry import ModelRegistry
from agent_framework.providers import OllamaProvider

from src.alpaca import AlpacaClient
from src.alerting import TradeAlerter
from src.logger import get_logger, AgentLogger
from src.storage import StorageLayer
from src.chart_generator import generate_candlestick_chart

from .technical_agent import (
    build_technical_watching_agent,
    build_technical_entry_agent,
)
from .economics_agent import (
    build_market_context_agent,
    build_company_analysis_agent,
)
from .trading_agent import (
    build_trading_opportunity_agent,
    build_trading_entry_agent,
)
from .orchestrator_agent import build_orchestrator_agent

# Máximo de símbolos por llamada a snapshots (límite de la API de Alpaca)
_SNAPSHOT_BATCH = 100

_CRYPTO_KNOWN = {"BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "DOT", "AVAX", "MATIC", "LINK"}


class PriceMonitor:
    def __init__(
        self,
        alpaca: AlpacaClient,
        storage: StorageLayer,
        orchestrator_agent,        # OrchestratorAgent (TaskListPlanner)
        task_manager,              # TaskListManager — reset antes de cada run()
        logger: AgentLogger,
        max_position_value: float = 500.0,
        analysis_interval_hours: float = 4.0,
        alerter=None,
        force_event: asyncio.Event | None = None,
        allow_short_selling: bool = False,
    ):
        self._alpaca = alpaca
        self._storage = storage
        self._orchestrator_agent = orchestrator_agent
        self._task_manager = task_manager
        self._logger = logger
        self._max_position_value = max_position_value
        self._analysis_interval_hours = analysis_interval_hours
        self._alerter = alerter
        self._force_event = force_event
        self._market_status_callback = None
        self._running = True
        self._force_analysis = False
        self._allow_short_selling = allow_short_selling

    # ── Loop principal ────────────────────────────────────────────────────────

    async def run(
        self,
        poll_interval_seconds: int = 60,
        stop_event: asyncio.Event | None = None,
    ):
        """Loop principal de monitorización."""
        self._running = True
        self._logger.info(
            f"Iniciando monitorización (intervalo: {poll_interval_seconds}s, "
            f"análisis cada {self._analysis_interval_hours}h)",
            source="monitor",
        )

        while self._running:
            if stop_event and stop_event.is_set():
                break

            # Comprobar si se solicitó análisis forzado
            if self._force_event and self._force_event.is_set():
                self._force_analysis = True
                self._force_event.clear()
                self._logger.info("Análisis forzado solicitado — procesando todos los símbolos", source="monitor")

            watchlist = self._storage.get_watchlist()
            if not watchlist:
                self._logger.warning("Watchlist vacía — añade símbolos desde el dashboard", source="monitor")
            else:
                await self._check_all(watchlist, stop_event)

            # Resetear flag de análisis forzado al final del ciclo
            self._force_analysis = False

            await _wait_or_stop(stop_event, poll_interval_seconds, self._force_event)

        self._logger.info("Monitorización detenida", source="monitor")

    # ── Ciclo de precios ──────────────────────────────────────────────────────

    async def _check_all(self, watchlist: list[dict], stop_event: asyncio.Event | None = None):
        """Consulta precios en batch y procesa los cambios detectados."""
        # Verificar horario de mercado (se omite si hay análisis forzado)
        market_is_open = True
        wait_secs: float | None = None

        if self._force_analysis:
            self._logger.info(
                "Análisis forzado — se omite la verificación de horario de mercado",
                source="monitor",
            )
            clock = None
        else:
            clock = None
            try:
                clock = await self._alpaca.get_clock()
                if not clock["is_open"]:
                    market_is_open = False
                    next_open_str = clock.get("next_open", "")
                    if next_open_str:
                        next_open_utc = datetime.fromisoformat(next_open_str.replace("Z", "+00:00"))
                        wait_secs = max(60, (next_open_utc - datetime.now(timezone.utc)).total_seconds())
                        next_open_local = next_open_utc.astimezone().strftime("%H:%M %Z")
                    else:
                        wait_secs = 300
                        next_open_local = "desconocida"

                    if self._market_status_callback:
                        self._market_status_callback(False, next_open_str)

                    self._logger.info(
                        f"Mercado cerrado — apertura a las {next_open_local}",
                        source="monitor",
                    )
                    self._logger.debug(
                        f"Mercado cerrado — esperando {wait_secs:.0f}s ({wait_secs / 60:.1f} min) "
                        f"(análisis WATCHING y contexto de mercado se ejecutarán antes)",
                        source="monitor",
                    )

                    # Cancelar órdenes pendientes (tipo 'day' caducan igual, pero limpieza explícita)
                    try:
                        await self._alpaca.cancel_all_orders()
                        self._logger.info("Órdenes pendientes canceladas (mercado cerrado)", source="monitor")
                    except Exception as e:
                        self._logger.warning(f"No se pudieron cancelar órdenes pendientes: {e}", source="monitor")
            except Exception as e:
                self._logger.warning(f"No se pudo verificar horario de mercado: {e}", source="monitor")
            else:
                # Mercado abierto — notificar a la UI
                if clock and clock.get("is_open") and self._market_status_callback:
                    self._market_status_callback(True, None)

        # ¿Cierre inminente? (< 5 min para next_close) — solo relevante con mercado abierto
        close_imminent = False
        if market_is_open:
            next_close_str = clock.get("next_close", "") if clock else ""
            if next_close_str:
                try:
                    next_close_utc = datetime.fromisoformat(next_close_str.replace("Z", "+00:00"))
                    mins_to_close = (next_close_utc - datetime.now(timezone.utc)).total_seconds() / 60
                    if mins_to_close < 5:
                        close_imminent = True
                        self._logger.info(
                            f"Cierre de mercado en {mins_to_close:.1f} min — análisis nuevos omitidos",
                            source="monitor",
                        )
                except Exception:
                    pass

        symbols = [item["symbol"] for item in watchlist]

        # Consultar precios en lotes de _SNAPSHOT_BATCH
        snapshots: dict[str, dict] = {}
        for i in range(0, len(symbols), _SNAPSHOT_BATCH):
            batch = symbols[i: i + _SNAPSHOT_BATCH]
            try:
                batch_data = await self._alpaca.get_snapshots(batch)
                snapshots.update(batch_data)
            except Exception as e:
                self._logger.error(f"Error en batch {batch[:3]}...: {e}", source="monitor", exc_info=True)

        self._logger.info(f"Ciclo inicio — {len(symbols)} símbolos a monitorizar", source="monitor")

        # Determinar qué símbolos procesar (excluir los que no tienen precio o son omitidos por cierre)
        symbols_to_process = []
        for symbol in symbols:
            snap = snapshots.get(symbol)
            if not snap or snap.get("close") is None:
                self._logger.debug(f"{symbol}: sin datos de precio en este ciclo", source="monitor")
                continue
            if close_imminent:
                state_row = self._storage.get_symbol_state(symbol)
                state = state_row.get("state", "WATCHING")
                if state not in ("POSITION_OPEN", "OPENING", "CLOSING"):
                    self._logger.debug(
                        f"{symbol}: análisis omitido — cierre de mercado en < 5 min",
                        source="monitor",
                    )
                    continue
            symbols_to_process.append(symbol)

        # Delegar a OrchestratorAgent si hay símbolos a procesar y no se solicitó parada
        if symbols_to_process and (not stop_event or not stop_event.is_set()) and self._running:
            self._task_manager.reset(symbols_to_process)
            task = self._build_orchestrator_task(
                symbols_to_process, snapshots, market_is_open, close_imminent
            )
            try:
                await self._orchestrator_agent.run(task)
            except Exception as e:
                self._logger.error(
                    f"Error en OrchestratorAgent: {e}", source="monitor", exc_info=True
                )
        elif stop_event and stop_event.is_set():
            self._logger.info("Parada solicitada — ciclo de orquestación omitido", source="monitor")

        # Guardar snapshots de precio para todos los símbolos con precio disponible
        for symbol in symbols:
            snap = snapshots.get(symbol)
            if snap and snap.get("close") is not None:
                self._storage.save_price_snapshot(
                    symbol,
                    snap["close"],
                    snap.get("volume"),
                    snap.get("timestamp") or "",
                )

        self._logger.info("Ciclo fin", source="monitor")

        # Con mercado cerrado: dormir hasta apertura tras haber ejecutado el orquestador
        if not market_is_open and wait_secs is not None:
            await _interruptible_sleep(wait_secs, stop_event, self._force_event)

    # ── Orchestrator task builder ─────────────────────────────────────────────

    def _build_orchestrator_task(
        self,
        symbols: list[str],
        snapshots: dict,
        market_is_open: bool,
        close_imminent: bool = False,
    ) -> str:
        """
        Construye el string de tarea para el OrchestratorAgent.
        Incluye precios actuales y flags de contexto.
        """
        lines = [
            f"Process the following {len(symbols)} symbol(s): {', '.join(symbols)}.",
            f"Market is currently: {'OPEN' if market_is_open else 'CLOSED'}.",
            f"Close imminent (< 5 min to close): {'YES — only manage POSITION_OPEN/OPENING/CLOSING' if close_imminent else 'NO'}.",
            f"Force analysis: {'YES — run analysis even if interval not elapsed' if self._force_analysis else 'NO'}.",
            f"Max position value: ${self._max_position_value:.0f} USD.",
            f"Allow short selling: {'YES' if self._allow_short_selling else 'NO'}.",
            "",
            "Current prices:",
        ]
        for sym in symbols:
            snap = snapshots.get(sym, {})
            price = snap.get("close")
            price_str = f"${price:.2f}" if price is not None else "unavailable"
            lines.append(f"  {sym}: {price_str}")
        return "\n".join(lines)

    def _generate_chart(self, symbol: str, bars: list[dict], timeframe: str, event_label: str) -> str | None:
        """Genera un gráfico de velas y retorna la ruta, o None si falla."""
        if not bars:
            return None
        try:
            from src.chart_generator import generate_candlestick_chart
            path = generate_candlestick_chart(symbol, bars, timeframe, event_label)
            self._logger.info(f"{symbol}: gráfico de evidencia → file:///{path}", source="monitor")
            return path
        except Exception as e:
            self._logger.warning(f"{symbol}: no se pudo generar gráfico: {e}", source="monitor")
            return None

    def stop(self):
        self._running = False


# ── Utilidades ────────────────────────────────────────────────────────────────

async def _wait_or_stop(
    stop_event: asyncio.Event | None,
    seconds: int,
    force_event: asyncio.Event | None = None,
):
    waiters = []
    if stop_event:
        waiters.append(asyncio.ensure_future(stop_event.wait()))
    if force_event:
        waiters.append(asyncio.ensure_future(force_event.wait()))

    if waiters:
        _, pending = await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    else:
        await asyncio.sleep(seconds)


async def _interruptible_sleep(
    seconds: float,
    stop_event: asyncio.Event | None,
    force_event: asyncio.Event | None = None,
):
    """Duerme hasta `seconds` segundos, interrumpible por stop_event o force_event."""
    await _wait_or_stop(stop_event, int(seconds), force_event)


async def _init_watchlist_from_alpaca(
    alpaca: AlpacaClient,
    storage: StorageLayer,
    logger: AgentLogger,
):
    """Pobla la watchlist con TODOS los activos negociables de Alpaca."""
    logger.info("WATCHLIST_SYMBOLS no definido — obteniendo todos los activos de Alpaca...", source="monitor")
    try:
        assets = await alpaca.get_assets()
        for asset in assets:
            storage.upsert_watchlist(
                asset["symbol"],
                asset.get("name", ""),
                exchange=asset.get("exchange", ""),
                marginable=asset.get("marginable", False),
                shortable=asset.get("shortable", False),
            )
        logger.info(f"Watchlist inicializada con {len(assets)} activos de Alpaca", source="monitor")
    except Exception as e:
        logger.error(f"Error obteniendo activos de Alpaca: {e} — watchlist vacía", source="monitor", exc_info=True)


async def run_monitor(
    on_log=None,
    stop_event: asyncio.Event | None = None,
    force_event: asyncio.Event | None = None,
    market_status_callback=None,
):
    """
    Inicializa todos los componentes y arranca el loop de monitorización.
    Diseñado para ejecutarse con asyncio.run() desde un thread de Tkinter.
    """
    logger = get_logger(on_log=on_log)

    # Credenciales
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    trading_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    alert_recipient = os.environ.get("ALERT_RECIPIENT") or gmail_user
    max_position_value = float(os.environ.get("MAX_POSITION_VALUE", "500"))

    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY y ALPACA_SECRET_KEY requeridas en .env", source="monitor")
        return

    # Inicializar Alpaca
    alpaca = AlpacaClient(api_key, secret_key, trading_url, logger=logger)
    logger.info(f"Conectando a Alpaca ({trading_url})...", source="monitor")
    logger.info(
        f"API key: {api_key[:4]}...{api_key[-4:]}  |  Secret: {secret_key[:4]}...{secret_key[-4:]}",
        source="monitor",
    )
    ok, err = await alpaca.is_available()
    if not ok:
        logger.error(f"No se puede conectar con Alpaca: {err}", source="monitor")
        return
    account = await alpaca.get_account()
    logger.info(
        f"Cuenta OK — equity: ${account['equity']:.2f} | cash: ${account['cash']:.2f}",
        source="monitor",
    )

    # Alerter de email (opcional)
    alerter = None
    if gmail_user and gmail_pass:
        alerter = TradeAlerter(gmail_user, gmail_pass, alert_recipient)
        logger.info(f"Alertas email habilitadas → {alert_recipient}", source="monitor")
    else:
        logger.warning("GMAIL_USER / GMAIL_APP_PASSWORD no configurados — alertas desactivadas", source="monitor")

    # Storage
    storage = StorageLayer(db_path="trading_agent.db")

    # Inicializar watchlist
    watchlist = storage.get_watchlist()
    if not watchlist:
        symbols_env = os.environ.get("WATCHLIST_SYMBOLS", "").strip()
        if symbols_env:
            filtered = []
            for sym in [s.strip().upper() for s in symbols_env.split(",") if s.strip()]:
                if alpaca.is_crypto(sym):
                    logger.warning(
                        f"{sym} excluido (crypto — usar Binance en el futuro)", source="monitor"
                    )
                    continue
                try:
                    asset = await alpaca.get_asset(sym)
                except Exception:
                    asset = None
                if asset:
                    storage.upsert_watchlist(
                        asset["symbol"],
                        asset.get("name", ""),
                        exchange=asset.get("exchange", ""),
                        marginable=asset.get("marginable", False),
                        shortable=asset.get("shortable", False),
                    )
                else:
                    storage.upsert_watchlist(sym)  # fallback mínimo
                filtered.append(sym)
            logger.info(f"Watchlist inicializada desde .env: {', '.join(filtered)}", source="monitor")
        else:
            await _init_watchlist_from_alpaca(alpaca, storage, logger)

    # Framework config
    config_path = Path(__file__).parent / "trading_agent.yaml"
    config = load_from_yaml(config_path)
    trading_opts = config.trading

    analysis_interval = trading_opts.get("analysis_interval_hours", 4)
    max_pos_val = trading_opts.get("max_position_value", max_position_value)
    poll_interval = trading_opts.get("poll_interval_seconds", 60)

    # Usar el valor de .env si es distinto del default del yaml
    effective_max_position = max_position_value if max_position_value != 500.0 else max_pos_val
    logger.info(f"Valor máximo por posición: ${effective_max_position:.0f} USD", source="monitor")

    # Short selling — ENV override tiene prioridad sobre yaml
    env_short = os.environ.get("ALLOW_SHORT_SELLING", "").lower()
    allow_short = env_short in ("1", "true", "yes") or trading_opts.get("allow_short_selling", False)
    if allow_short:
        logger.info("Short selling habilitado (ALLOW_SHORT_SELLING)", source="monitor")

    # Modelos
    model_registry = ModelRegistry()

    def _register(role: str):
        cfg = config.models.get(role)
        if cfg:
            model_registry.register(role, OllamaProvider(cfg.model, base_url=ollama_url))
            logger.info(f"Modelo {role}: {cfg.model}", source="monitor")
        else:
            logger.warning(f"Modelo '{role}' no encontrado en trading_agent.yaml", source="monitor")

    _register("technical")
    _register("technical_entry")
    _register("economics")
    _register("news_classifier")
    _register("trader_opportunity")
    _register("trader_entry")
    _register("orchestrator")

    # EventBus con logging de prompts
    bus = EventBus()

    async def on_event(event):
        if event.type == EventType.MODEL_CALLED:
            logger.debug(f"Llamando al modelo ({event.data.get('role', '?')})...", source="agent")
        elif event.type == EventType.MODEL_FINISHED:
            data = event.data
            logger.log_prompt(
                agent_name=data.get("role", "unknown"),
                prompt=data.get("prompt", ""),
                response=data.get("response", ""),
                system_prompt=data.get("system_prompt", ""),
            )
        elif event.type == EventType.TOOL_CALLED:
            logger.debug(f"Tool: {event.data.get('tool', '?')}", source="agent")

    bus.subscribe("*", on_event)

    # Construir NewsIngestionService primero (necesario para los sub-agentes de economía)
    from src.news_sources import AlpacaNewsSource, RSSNewsSource, YahooSymbolSource, NewsAggregator
    from src.news_service import NewsIngestionService
    from .economics_agent import build_news_classifier_agent

    alpaca_news = AlpacaNewsSource(api_key, secret_key, logger=logger)
    rss_source = RSSNewsSource(logger=logger)
    yahoo_source = YahooSymbolSource(logger=logger)
    aggregator = NewsAggregator(alpaca_news, rss_source, yahoo_source, logger=logger)
    news_classifier_agent = await build_news_classifier_agent(config, bus, model_registry)
    news_classifier_full_agent = await build_news_classifier_agent(
        config, bus, model_registry, full_content=True
    )
    news_service = NewsIngestionService(
        aggregator,
        news_classifier_agent,
        storage,
        logger=logger,
        full_content_classifier_agent=news_classifier_full_agent,
    )

    # Construir sub-agentes especializados (usados por el OrchestratorAgent via RunSubAgentTool)
    technical_watching_agent = await build_technical_watching_agent(
        config, bus, model_registry, alpaca_client=alpaca, storage=storage
    )
    technical_entry_agent = await build_technical_entry_agent(
        config, bus, model_registry, alpaca_client=alpaca, storage=storage
    )
    market_context_agent = await build_market_context_agent(
        config, bus, model_registry, news_service=news_service, storage=storage
    )
    company_analysis_agent = await build_company_analysis_agent(
        config, bus, model_registry, news_service=news_service, storage=storage
    )
    trading_opportunity_agent = await build_trading_opportunity_agent(
        config, bus, model_registry, storage=storage
    )
    trading_entry_agent = await build_trading_entry_agent(
        config, bus, model_registry, alpaca_client=alpaca, storage=storage
    )

    # Construir OrchestratorAgent (TaskListPlanner) con todos los sub-agentes
    sub_agents = {
        "technical_watching":  technical_watching_agent,
        "technical_entry":     technical_entry_agent,
        "market_context":      market_context_agent,
        "company_analysis":    company_analysis_agent,
        "trading_opportunity": trading_opportunity_agent,
        "trading_entry":       trading_entry_agent,
    }
    orchestrator_agent, task_manager = await build_orchestrator_agent(
        config=config,
        bus=bus,
        model_registry=model_registry,
        sub_agents=sub_agents,
        alpaca_client=alpaca,
        storage=storage,
        news_service=news_service,
        max_position_value=effective_max_position,
    )

    # Arrancar monitor
    monitor = PriceMonitor(
        alpaca=alpaca,
        storage=storage,
        orchestrator_agent=orchestrator_agent,
        task_manager=task_manager,
        logger=logger,
        max_position_value=effective_max_position,
        analysis_interval_hours=analysis_interval,
        alerter=alerter,
        force_event=force_event,
        allow_short_selling=allow_short,
    )
    monitor._market_status_callback = market_status_callback
    await monitor.run(poll_interval_seconds=poll_interval, stop_event=stop_event)


def run_monitor_sync(
    on_log=None,
    stop_event: asyncio.Event | None = None,
    force_event: asyncio.Event | None = None,
    market_status_callback=None,
):
    """Wrapper síncrono para Tkinter thread."""
    asyncio.run(run_monitor(
        on_log=on_log,
        stop_event=stop_event,
        force_event=force_event,
        market_status_callback=market_status_callback,
    ))
