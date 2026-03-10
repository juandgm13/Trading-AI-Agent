"""
PriceMonitor — loop de monitorización con máquina de estados por símbolo.

Estados por símbolo:
  WATCHING         → análisis completo cada analysis_interval_hours
  OPPORTUNITY_FOUND → análisis de corto plazo cada ciclo
  POSITION_OPEN    → seguimiento de target_price y stop_loss

Si WATCHLIST_SYMBOLS no está definido en .env, se usan TODOS los activos
negociables de Alpaca (US equities activos). Los precios se consultan en
batch (snapshots) para minimizar llamadas a la API.
"""
import asyncio
import json
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

from .technical_agent import build_technical_agent, build_medium_term_prompt, build_short_term_prompt
from .economics_agent import build_economics_agent, build_economics_prompt
from .trading_agent import build_trading_orchestrator, build_opportunity_prompt, build_entry_prompt

# Máximo de símbolos por llamada a snapshots (límite de la API de Alpaca)
_SNAPSHOT_BATCH = 100

_CRYPTO_KNOWN = {"BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "DOT", "AVAX", "MATIC", "LINK"}


class PriceMonitor:
    def __init__(
        self,
        alpaca: AlpacaClient,
        storage: StorageLayer,
        technical_agent,
        economics_agent,
        trading_agent,
        logger: AgentLogger,
        gnews_api_key: str,
        max_qty: int = 10,
        analysis_interval_hours: float = 4.0,
        alerter=None,
        force_event: asyncio.Event | None = None,
    ):
        self._alpaca = alpaca
        self._storage = storage
        self._technical_agent = technical_agent
        self._economics_agent = economics_agent
        self._trading_agent = trading_agent
        self._logger = logger
        self._gnews_api_key = gnews_api_key
        self._max_qty = max_qty
        self._analysis_interval_hours = analysis_interval_hours
        self._alerter = alerter
        self._force_event = force_event
        self._running = False
        self._force_analysis = False

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
                await self._check_all(watchlist)

            # Resetear flag de análisis forzado al final del ciclo
            self._force_analysis = False

            await _wait_or_stop(stop_event, poll_interval_seconds)

        self._logger.info("Monitorización detenida", source="monitor")

    # ── Ciclo de precios ──────────────────────────────────────────────────────

    async def _check_all(self, watchlist: list[dict]):
        """Consulta precios en batch y procesa los cambios detectados."""
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

        alerts_triggered = []
        self._logger.info(f"Ciclo inicio — {len(symbols)} símbolos a monitorizar", source="monitor")

        for symbol in symbols:
            if not self._running:
                break
            snap = snapshots.get(symbol)
            if not snap or snap.get("close") is None:
                self._logger.debug(f"{symbol}: sin datos de precio en este ciclo", source="monitor")
                continue

            state_row = self._storage.get_symbol_state(symbol)
            triggered = await self._process_symbol(symbol, snap, state_row)
            if triggered:
                alerts_triggered.append(triggered)

            # Guardar snapshot
            self._storage.save_price_snapshot(
                symbol,
                snap["close"],
                snap.get("volume"),
                snap.get("timestamp") or "",
            )

        if alerts_triggered:
            self._logger.info(
                f"Ciclo fin — {len(alerts_triggered)} alertas: {', '.join(alerts_triggered)}",
                source="monitor",
            )
        else:
            self._logger.info("Ciclo fin — sin alertas", source="monitor")

    # ── Máquina de estados ────────────────────────────────────────────────────

    async def _process_symbol(self, symbol: str, snap: dict, state_row: dict) -> str | None:
        """
        Procesa un símbolo según su estado actual.

        Returns:
            Descripción del trigger si se disparó alguna acción, None si no.
        """
        current_price = snap["close"]
        state = state_row.get("state", "WATCHING")

        self._logger.debug(
            f"{symbol}: ${current_price:.2f} | estado: {state}",
            source="monitor",
        )

        if state == "POSITION_OPEN":
            triggered = await self._manage_position(symbol, current_price, state_row)
            return triggered

        elif state == "OPPORTUNITY_FOUND":
            triggered = await self._look_for_entry(symbol, current_price, state_row)
            return triggered

        elif state == "WATCHING":
            if self._analysis_due(state_row) or self._force_analysis:
                triggered = await self._full_analysis(symbol, current_price)
                return triggered

        return None

    def _analysis_due(self, state_row: dict) -> bool:
        """Comprueba si ha pasado suficiente tiempo desde el último análisis completo."""
        last_analysis = state_row.get("last_analysis_at")
        if not last_analysis:
            return True
        try:
            last_dt = datetime.fromisoformat(last_analysis)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            return elapsed_hours >= self._analysis_interval_hours
        except Exception:
            return True

    # ── Estado: WATCHING ──────────────────────────────────────────────────────

    async def _full_analysis(self, symbol: str, price: float) -> str | None:
        """Análisis completo: TechnicalAgent (medio plazo) + EconomicsAgent → TradingAgent."""
        self._logger.info(f"{symbol}: iniciando análisis completo", source="monitor")

        # 1. TechnicalAgent — medio plazo (90 días diarios)
        tech_report = await self._run_technical_medium(symbol, price)
        if tech_report is None:
            self._logger.warning(f"{symbol}: análisis técnico falló — saltando", source="monitor")
            return None

        # 2. EconomicsAgent — noticias del símbolo + mercado
        econ_report = await self._run_economics(symbol)
        if econ_report is None:
            self._logger.warning(f"{symbol}: análisis económico falló — usando neutral", source="monitor")
            econ_report = {"sentiment": "NEUTRAL", "market_context": "NEUTRAL", "key_events": [], "confidence": 0, "summary": "No data"}

        # 3. TradingAgent — decide si hay oportunidad
        prompt = build_opportunity_prompt(symbol, price, tech_report, econ_report, self._max_qty)
        decision = await self._run_trading_decision(symbol, "opportunity_eval", prompt)

        now = datetime.now(timezone.utc).isoformat()
        summary = f"Tech: {tech_report.get('trend')} | Econ: {econ_report.get('sentiment')}"

        self._storage.upsert_symbol_state(
            symbol,
            last_analysis_at=now,
            analysis_summary=summary,
        )

        if decision and decision.get("decision") == "OPEN_POSITION":
            self._logger.info(f"{symbol}: oportunidad detectada → OPPORTUNITY_FOUND", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="OPPORTUNITY_FOUND", entry_signal_at=now)
            return f"{symbol} → OPPORTUNITY_FOUND"

        self._logger.info(
            f"{symbol}: análisis completo → {decision.get('decision', 'HOLD') if decision else 'error'}",
            source="monitor",
        )
        return None

    # ── Estado: OPPORTUNITY_FOUND ─────────────────────────────────────────────

    async def _look_for_entry(self, symbol: str, price: float, state_row: dict) -> str | None:
        """Análisis de corto plazo para decidir si es el momento de entrar."""
        self._logger.info(f"{symbol}: buscando punto de entrada (corto plazo)", source="monitor")

        tech_report = await self._run_technical_short(symbol, price)
        if tech_report is None:
            self._logger.warning(f"{symbol}: análisis técnico corto falló", source="monitor")
            return None

        prompt = build_entry_prompt(symbol, price, tech_report, self._max_qty)
        decision = await self._run_trading_decision(symbol, "entry_eval", prompt)

        if not decision:
            return None

        if decision.get("decision") == "OPEN_POSITION":
            qty = decision.get("qty") or 1
            target = tech_report.get("target_price")
            stop = tech_report.get("stop_loss")

            self._logger.info(
                f"{symbol}: abriendo posición — qty={qty} target=${target} stop=${stop}",
                source="monitor",
            )

            try:
                result = await self._alpaca.place_order(symbol, qty, "buy")
                now = datetime.now(timezone.utc).isoformat()
                self._storage.save_trade(
                    symbol=symbol,
                    side="buy",
                    qty=qty,
                    price=price,
                    order_id=result.get("order_id"),
                    status=result.get("status", "submitted"),
                    trigger="entry_signal",
                    reason=decision.get("reasoning", ""),
                )
                self._storage.upsert_symbol_state(
                    symbol,
                    state="POSITION_OPEN",
                    target_price=target,
                    stop_loss=stop,
                    position_qty=float(qty),
                    position_open_at=now,
                )
                if self._alerter:
                    try:
                        self._alerter.send_trade_alert(
                            symbol=symbol,
                            side="buy",
                            qty=qty,
                            price=price,
                            order_id=result.get("order_id"),
                            reason=decision.get("reasoning", ""),
                        )
                    except Exception:
                        pass
                return f"{symbol} BUY×{qty} @ ${price:.2f}"
            except Exception as e:
                self._logger.error(f"{symbol}: error al abrir posición: {e}", source="monitor", exc_info=True)

        elif decision.get("decision") == "REJECT":
            self._logger.info(f"{symbol}: señal rechazada → volviendo a WATCHING", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="WATCHING", entry_signal_at=None)

        return None

    # ── Estado: POSITION_OPEN ─────────────────────────────────────────────────

    async def _manage_position(self, symbol: str, price: float, state_row: dict) -> str | None:
        """Gestiona una posición abierta: comprueba target y stop-loss."""
        target = state_row.get("target_price")
        stop = state_row.get("stop_loss")
        qty = state_row.get("position_qty") or 0

        if not qty:
            # Posición sin cantidad registrada → resetear
            self._logger.warning(f"{symbol}: POSITION_OPEN sin qty registrada → WATCHING", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="WATCHING")
            return None

        reason = None
        if target and price >= target:
            reason = f"Target ${target:.2f} alcanzado (precio actual: ${price:.2f})"
        elif stop and price <= stop:
            reason = f"Stop-loss ${stop:.2f} activado (precio actual: ${price:.2f})"

        if not reason:
            self._logger.debug(
                f"{symbol}: posición abierta — precio ${price:.2f} | target ${target} | stop ${stop}",
                source="monitor",
            )
            return None

        self._logger.info(f"{symbol}: cerrando posición — {reason}", source="monitor")
        try:
            result = await self._alpaca.place_order(symbol, qty, "sell")
            self._storage.save_trade(
                symbol=symbol,
                side="sell",
                qty=qty,
                price=price,
                order_id=result.get("order_id"),
                status=result.get("status", "submitted"),
                trigger=reason,
                reason=reason,
            )
            self._storage.upsert_symbol_state(
                symbol,
                state="WATCHING",
                target_price=None,
                stop_loss=None,
                position_qty=None,
                position_open_at=None,
            )
            if self._alerter:
                try:
                    self._alerter.send_trade_alert(
                        symbol=symbol,
                        side="sell",
                        qty=qty,
                        price=price,
                        order_id=result.get("order_id"),
                        reason=reason,
                    )
                except Exception:
                    pass
            return f"{symbol} SELL×{qty} @ ${price:.2f}"
        except Exception as e:
            self._logger.error(f"{symbol}: error al cerrar posición: {e}", source="monitor", exc_info=True)
        return None

    # ── Helpers de sub-agentes ────────────────────────────────────────────────

    async def _run_technical_medium(self, symbol: str, price: float) -> dict | None:
        """Ejecuta TechnicalAgent en modo medio plazo (90 días diarios)."""
        try:
            bars = await self._alpaca.get_bars(symbol, timeframe="1Day", limit=90)
            prompt = build_medium_term_prompt(symbol, price, bars)
            response = await self._technical_agent.run(prompt)
            report = _parse_json_response(response)
            self._storage.save_agent_report(symbol, "technical_medium", json.dumps(report))
            self._logger.log_prompt("technical_medium", prompt, response)
            return report
        except Exception as e:
            self._logger.error(f"{symbol}: error en TechnicalAgent (medium): {e}", source="monitor", exc_info=True)
            return None

    async def _run_technical_short(self, symbol: str, price: float) -> dict | None:
        """Ejecuta TechnicalAgent en modo corto plazo (5 días de barras de 15min)."""
        try:
            bars = await self._alpaca.get_bars(symbol, timeframe="15Min", limit=96)  # ~5 días
            prompt = build_short_term_prompt(symbol, price, bars)
            response = await self._technical_agent.run(prompt)
            report = _parse_json_response(response)
            self._storage.save_agent_report(symbol, "technical_short", json.dumps(report))
            self._logger.log_prompt("technical_short", prompt, response)
            return report
        except Exception as e:
            self._logger.error(f"{symbol}: error en TechnicalAgent (short): {e}", source="monitor", exc_info=True)
            return None

    async def _run_economics(self, symbol: str) -> dict | None:
        """Ejecuta EconomicsAgent con noticias del símbolo + mercado general."""
        try:
            from .tools.news_tool import SearchNewsTool
            news_tool = SearchNewsTool(self._gnews_api_key)

            company_raw = await news_tool.execute(symbol)
            market_raw = await news_tool.execute("stock market")

            company_news = _parse_news_list(company_raw)
            market_news = _parse_news_list(market_raw)

            prompt = build_economics_prompt(symbol, company_news, market_news)
            response = await self._economics_agent.run(prompt)
            report = _parse_json_response(response)
            self._storage.save_agent_report(symbol, "economics", json.dumps(report))
            self._logger.log_prompt("economics", prompt, response)
            return report
        except Exception as e:
            self._logger.error(f"{symbol}: error en EconomicsAgent: {e}", source="monitor", exc_info=True)
            return None

    async def _run_trading_decision(
        self, symbol: str, stage: str, prompt: str
    ) -> dict | None:
        """Ejecuta el TradingAgent orquestador y devuelve la decisión parseada."""
        try:
            response = await self._trading_agent.run(prompt)
            decision = _parse_json_response(response)
            self._storage.save_agent_report(symbol, f"trading_{stage}", json.dumps(decision))
            self._logger.log_prompt(f"trading_{stage}", prompt, response)
            return decision
        except Exception as e:
            self._logger.error(f"{symbol}: error en TradingAgent ({stage}): {e}", source="monitor", exc_info=True)
            return None

    def stop(self):
        self._running = False


# ── Utilidades ────────────────────────────────────────────────────────────────

def _parse_json_response(text: str) -> dict:
    """Extrae el primer bloque JSON de una respuesta de texto."""
    import re
    if not text:
        return {}
    # Intentar parsear directamente
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Buscar bloque JSON con regex
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _parse_news_list(raw: str) -> list[dict]:
    """Extrae la lista de artículos del JSON devuelto por SearchNewsTool."""
    try:
        data = json.loads(raw)
        return data.get("articles", [])
    except Exception:
        return []


async def _wait_or_stop(stop_event: asyncio.Event | None, seconds: int):
    if stop_event:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(seconds)


async def _init_watchlist_from_alpaca(
    alpaca: AlpacaClient,
    storage: StorageLayer,
    logger: AgentLogger,
    alpaca_client: AlpacaClient,
):
    """Pobla la watchlist con TODOS los activos negociables de Alpaca."""
    logger.info("WATCHLIST_SYMBOLS no definido — obteniendo todos los activos de Alpaca...", source="monitor")
    try:
        assets = await alpaca.get_assets()
        for asset in assets:
            storage.upsert_watchlist(asset["symbol"], asset.get("name", ""))
        logger.info(f"Watchlist inicializada con {len(assets)} activos de Alpaca", source="monitor")
    except Exception as e:
        logger.error(f"Error obteniendo activos de Alpaca: {e} — watchlist vacía", source="monitor", exc_info=True)


async def run_monitor(
    on_log=None,
    stop_event: asyncio.Event | None = None,
    force_event: asyncio.Event | None = None,
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
    gnews_key = os.environ.get("GNEWS_API_KEY")
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    alert_recipient = os.environ.get("ALERT_RECIPIENT") or gmail_user

    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY y ALPACA_SECRET_KEY requeridas en .env", source="monitor")
        return
    if not gnews_key:
        logger.error("GNEWS_API_KEY requerida en .env", source="monitor")
        return

    # Inicializar Alpaca
    alpaca = AlpacaClient(api_key, secret_key, trading_url)
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
                else:
                    storage.upsert_watchlist(sym)
                    filtered.append(sym)
            logger.info(f"Watchlist inicializada desde .env: {', '.join(filtered)}", source="monitor")
        else:
            await _init_watchlist_from_alpaca(alpaca, storage, logger, alpaca)

    # Framework config
    config_path = Path(__file__).parent / "trading_agent.yaml"
    config = load_from_yaml(config_path)
    trading_opts = config.trading

    analysis_interval = trading_opts.get("analysis_interval_hours", 4)
    max_qty = trading_opts.get("max_qty_per_order", 10)
    poll_interval = trading_opts.get("poll_interval_seconds", 60)

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
    _register("economics")
    _register("trader")

    # EventBus con logging de prompts
    bus = EventBus()

    async def on_event(event):
        if event.type == EventType.MODEL_CALLED:
            logger.debug(f"Llamando al modelo...", source="agent")
        elif event.type == EventType.TOOL_CALLED:
            logger.debug(f"Tool: {event.data.get('tool', '?')}", source="agent")
        elif event.type == EventType.MODEL_FINISHED:
            pass  # Los prompts se registran en los métodos _run_*

    bus.subscribe("*", on_event)

    # Construir sub-agentes
    technical_agent = await build_technical_agent(config, bus, model_registry)
    economics_agent = await build_economics_agent(config, bus, model_registry)
    trading_agent = await build_trading_orchestrator(config, bus, model_registry)

    # Arrancar monitor
    monitor = PriceMonitor(
        alpaca=alpaca,
        storage=storage,
        technical_agent=technical_agent,
        economics_agent=economics_agent,
        trading_agent=trading_agent,
        logger=logger,
        gnews_api_key=gnews_key,
        max_qty=max_qty,
        analysis_interval_hours=analysis_interval,
        alerter=alerter,
        force_event=force_event,
    )
    await monitor.run(poll_interval_seconds=poll_interval, stop_event=stop_event)


def run_monitor_sync(
    on_log=None,
    stop_event: asyncio.Event | None = None,
    force_event: asyncio.Event | None = None,
):
    """Wrapper síncrono para Tkinter thread."""
    asyncio.run(run_monitor(on_log=on_log, stop_event=stop_event, force_event=force_event))
