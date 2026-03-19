"""
PriceMonitor — loop de monitorización con máquina de estados por símbolo.

Estados por símbolo:
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
from src.response_parser import (
    ParseError,
    validate_technical_watching_report,
    validate_technical_report,
    validate_market_context,
    validate_company_analysis,
    validate_trading_decision,
)

from .technical_agent import (
    build_technical_watching_agent,
    build_technical_entry_agent,
    build_medium_term_prompt,
    build_short_term_prompt,
)
from .economics_agent import (
    build_market_context_agent,
    build_company_analysis_agent,
    build_market_context_prompt,
    build_company_analysis_prompt,
)
from .trading_agent import (
    build_trading_opportunity_agent,
    build_trading_entry_agent,
    build_opportunity_prompt,
    build_entry_prompt,
)

# Máximo de símbolos por llamada a snapshots (límite de la API de Alpaca)
_SNAPSHOT_BATCH = 100

_CRYPTO_KNOWN = {"BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "DOT", "AVAX", "MATIC", "LINK"}


class PriceMonitor:
    def __init__(
        self,
        alpaca: AlpacaClient,
        storage: StorageLayer,
        technical_watching_agent,
        technical_entry_agent,
        market_context_agent,
        company_analysis_agent,
        trading_opportunity_agent,
        trading_entry_agent,
        logger: AgentLogger,
        gnews_api_key: str,
        max_position_value: float = 500.0,
        analysis_interval_hours: float = 4.0,
        alerter=None,
        force_event: asyncio.Event | None = None,
    ):
        self._alpaca = alpaca
        self._storage = storage
        self._technical_watching_agent = technical_watching_agent
        self._technical_entry_agent = technical_entry_agent
        self._market_context_agent = market_context_agent
        self._company_analysis_agent = company_analysis_agent
        self._trading_opportunity_agent = trading_opportunity_agent
        self._trading_entry_agent = trading_entry_agent
        self._logger = logger
        self._gnews_api_key = gnews_api_key
        self._max_position_value = max_position_value
        self._analysis_interval_hours = analysis_interval_hours
        self._alerter = alerter
        self._force_event = force_event
        self._market_status_callback = None
        self._running = True
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
                await self._check_all(watchlist, stop_event)

            # Resetear flag de análisis forzado al final del ciclo
            self._force_analysis = False

            await _wait_or_stop(stop_event, poll_interval_seconds)

        self._logger.info("Monitorización detenida", source="monitor")

    # ── Ciclo de precios ──────────────────────────────────────────────────────

    async def _check_all(self, watchlist: list[dict], stop_event: asyncio.Event | None = None):
        """Consulta precios en batch y procesa los cambios detectados."""
        # Verificar horario de mercado (se omite si hay análisis forzado)
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
                        f"Mercado cerrado — esperando {wait_secs:.0f}s ({wait_secs / 60:.1f} min)",
                        source="monitor",
                    )

                    # Cancelar órdenes pendientes (tipo 'day' caducan igual, pero limpieza explícita)
                    try:
                        await self._alpaca.cancel_all_orders()
                        self._logger.info("Órdenes pendientes canceladas (mercado cerrado)", source="monitor")
                    except Exception as e:
                        self._logger.warning(f"No se pudieron cancelar órdenes pendientes: {e}", source="monitor")

                    await _interruptible_sleep(wait_secs, stop_event)
                    return
            except Exception as e:
                self._logger.warning(f"No se pudo verificar horario de mercado: {e}", source="monitor")
            else:
                # Mercado abierto — notificar a la UI
                if clock and clock.get("is_open") and self._market_status_callback:
                    self._market_status_callback(True, None)

        # ¿Cierre inminente? (< 5 min para next_close)
        close_imminent = False
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

        # Actualizar contexto global de mercado si corresponde
        await self._maybe_update_market_context()

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
            # F3: verificar stop_event al inicio de cada símbolo
            if stop_event and stop_event.is_set():
                self._logger.info("Parada solicitada — finalizando ciclo actual", source="monitor")
                break

            if not self._running:
                break

            snap = snapshots.get(symbol)
            if not snap or snap.get("close") is None:
                self._logger.debug(f"{symbol}: sin datos de precio en este ciclo", source="monitor")
                continue

            state_row = self._storage.get_symbol_state(symbol)

            # Si cierre inminente, solo gestionar posiciones abiertas u órdenes en vuelo
            if close_imminent and state_row.get("state", "WATCHING") not in ("POSITION_OPEN", "OPENING", "CLOSING"):
                self._logger.debug(
                    f"{symbol}: análisis omitido — cierre de mercado en < 5 min",
                    source="monitor",
                )
            else:
                triggered = await self._process_symbol(symbol, snap, state_row)
                if triggered:
                    alerts_triggered.append(triggered)

            # Guardar snapshot de precio siempre (independientemente del skip)
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

        if state == "CLOSING":
            triggered = await self._check_closing(symbol, state_row)
            return triggered

        elif state == "POSITION_OPEN":
            triggered = await self._manage_position(symbol, current_price, state_row)
            return triggered

        elif state == "OPENING":
            triggered = await self._check_opening(symbol, state_row)
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
        """Análisis completo: TechnicalAgent (medio plazo) + market context global → TradingAgent."""
        self._logger.info(f"{symbol}: iniciando análisis completo", source="monitor")

        # 1. TechnicalAgent — medio plazo (90 días diarios)
        tech_report, bars = await self._run_technical_medium(symbol, price)
        if tech_report is None:
            self._logger.warning(f"{symbol}: análisis técnico falló — saltando", source="monitor")
            return None

        # 2. Market context global — leer el último informe de la BD
        market_context = self._load_market_context()
        self._logger.debug(
            f"{symbol}: market context — sentiment: {market_context.get('market_sentiment', 'N/A') if market_context else 'sin datos'}",
            source="monitor",
        )

        # 3. TradingAgent — decide si hay oportunidad
        prompt = build_opportunity_prompt(symbol, price, tech_report, market_context, self._max_position_value)
        decision = await self._run_trading_decision(symbol, "opportunity_eval", prompt, self._trading_opportunity_agent)

        now = datetime.now(timezone.utc).isoformat()
        market_sentiment = market_context.get("market_sentiment", "N/A") if market_context else "N/A"
        summary = f"Tech: {tech_report.get('trend')} | Market: {market_sentiment}"

        self._storage.upsert_symbol_state(
            symbol,
            last_analysis_at=now,
            analysis_summary=summary,
        )

        if decision and decision.get("decision") == "OPPORTUNITY_FOUND":
            # F5: generar gráfico de evidencia al detectar oportunidad
            chart_path = self._generate_chart(symbol, bars, "1Day", "opportunity")
            self._logger.info(f"{symbol}: oportunidad detectada → OPPORTUNITY_FOUND", source="monitor")
            self._storage.upsert_symbol_state(
                symbol,
                state="OPPORTUNITY_FOUND",
                entry_signal_at=now,
                chart_path=chart_path,
            )
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

        tech_report, bars = await self._run_technical_short(symbol, price)
        if tech_report is None:
            self._logger.warning(f"{symbol}: análisis técnico corto falló", source="monitor")
            return None

        # Company analysis — noticias recientes de la empresa para evaluar riesgo de entrada
        company_report = await self._run_company_analysis(symbol)
        if company_report is None:
            self._logger.warning(f"{symbol}: company analysis falló — continuando sin él", source="monitor")

        prompt = build_entry_prompt(symbol, price, tech_report, company_report, self._max_position_value)
        decision = await self._run_trading_decision(symbol, "entry_eval", prompt, self._trading_entry_agent)

        if not decision:
            return None

        if decision.get("decision") == "OPEN_POSITION":
            # Calcular qty máxima por valor máximo en dólares
            max_qty = max(1, int(self._max_position_value / price))
            agent_qty = decision.get("qty")
            if isinstance(agent_qty, (int, float)) and agent_qty >= 1:
                qty = min(int(agent_qty), max_qty)
                if int(agent_qty) > max_qty:
                    self._logger.info(
                        f"{symbol}: qty del agente ({int(agent_qty)}) excede límite — usando {qty}",
                        source="monitor",
                    )
            else:
                qty = max_qty  # fallback si el agente devuelve null
            target = tech_report.get("target_price")
            stop = tech_report.get("stop_loss")

            self._logger.info(
                f"{symbol}: abriendo posición — qty={qty} (${price:.2f}×{qty}=${price*qty:.0f}) "
                f"target=${target} stop=${stop}",
                source="monitor",
            )

            # F5: generar gráfico de evidencia al abrir posición
            chart_path = self._generate_chart(symbol, bars, "15Min", "entry")

            try:
                result = await self._alpaca.place_order(symbol, qty, "buy")
                self._storage.upsert_symbol_state(
                    symbol,
                    state="OPENING",
                    target_price=target,
                    stop_loss=stop,
                    position_qty=float(qty),
                    pending_order_id=result.get("order_id"),
                    chart_path=chart_path,
                )
                self._logger.info(
                    f"{symbol}: orden de compra enviada (order_id={result.get('order_id')}) → OPENING",
                    source="monitor",
                )
                return f"{symbol} BUY×{qty} enviada (OPENING)"
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
            self._storage.upsert_symbol_state(
                symbol,
                state="CLOSING",
                pending_order_id=result.get("order_id"),
                analysis_summary=reason,  # guardamos el motivo de cierre para usarlo al confirmar fill
            )
            self._logger.info(
                f"{symbol}: orden de venta enviada (order_id={result.get('order_id')}) → CLOSING",
                source="monitor",
            )
            return f"{symbol} SELL×{qty} enviada (CLOSING)"
        except Exception as e:
            self._logger.error(f"{symbol}: error al cerrar posición: {e}", source="monitor", exc_info=True)
        return None

    # ── Estado: OPENING ───────────────────────────────────────────────────────

    async def _check_opening(self, symbol: str, state_row: dict) -> str | None:
        """Verifica el estado de la orden de compra pendiente."""
        order_id = state_row.get("pending_order_id")
        if not order_id:
            self._logger.warning(f"{symbol}: OPENING sin pending_order_id → WATCHING", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="WATCHING", pending_order_id=None)
            return None

        try:
            order = await self._alpaca.get_order(order_id)
        except Exception as e:
            self._logger.error(f"{symbol}: error consultando orden {order_id}: {e}", source="monitor", exc_info=True)
            return None

        if order is None:
            self._logger.warning(f"{symbol}: orden {order_id} no encontrada → WATCHING", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="WATCHING", pending_order_id=None)
            return None

        status = order.get("status", "")
        if status == "filled":
            fill_price = order.get("filled_avg_price")
            fill_qty = order.get("filled_qty") or state_row.get("position_qty", 0)
            now = datetime.now(timezone.utc).isoformat()

            self._storage.save_trade(
                symbol=symbol,
                side="buy",
                qty=fill_qty,
                price=fill_price,
                order_id=order_id,
                status="filled",
                trigger="entry_signal",
                reason="",
            )
            self._storage.upsert_symbol_state(
                symbol,
                state="POSITION_OPEN",
                position_open_at=now,
                pending_order_id=None,
            )
            price_str = f"${fill_price:.2f}" if fill_price else "precio de mercado"
            self._logger.info(
                f"{symbol}: orden de compra ejecutada — {fill_qty:.0f} acc. @ {price_str} → POSITION_OPEN",
                source="monitor",
            )
            if self._alerter:
                try:
                    self._alerter.send_trade_alert(
                        symbol=symbol,
                        side="buy",
                        qty=fill_qty,
                        price=fill_price,
                        order_id=order_id,
                        reason=f"Orden ejecutada @ {price_str}",
                    )
                except Exception:
                    pass
            return f"{symbol} BUY FILLED @ {price_str}"

        elif status in ("cancelled", "expired", "rejected"):
            self._logger.info(
                f"{symbol}: orden de compra {status} → WATCHING",
                source="monitor",
            )
            self._storage.upsert_symbol_state(
                symbol, state="WATCHING", pending_order_id=None, entry_signal_at=None
            )
        else:
            self._logger.debug(f"{symbol}: orden de compra pendiente (status: {status})", source="monitor")
        return None

    # ── Estado: CLOSING ───────────────────────────────────────────────────────

    async def _check_closing(self, symbol: str, state_row: dict) -> str | None:
        """Verifica el estado de la orden de venta pendiente y calcula P&L al fill."""
        order_id = state_row.get("pending_order_id")
        if not order_id:
            self._logger.warning(f"{symbol}: CLOSING sin pending_order_id → POSITION_OPEN", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="POSITION_OPEN", pending_order_id=None)
            return None

        try:
            order = await self._alpaca.get_order(order_id)
        except Exception as e:
            self._logger.error(f"{symbol}: error consultando orden de cierre {order_id}: {e}", source="monitor", exc_info=True)
            return None

        if order is None:
            self._logger.warning(f"{symbol}: orden de cierre {order_id} no encontrada → POSITION_OPEN", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="POSITION_OPEN", pending_order_id=None)
            return None

        status = order.get("status", "")
        if status == "filled":
            fill_price = order.get("filled_avg_price")
            fill_qty = order.get("filled_qty") or state_row.get("position_qty", 0)
            trigger = state_row.get("analysis_summary") or "cierre de posición"

            buy_trade = self._storage.get_last_buy_trade(symbol)
            buy_price = buy_trade.get("price") if buy_trade else None

            pnl = (fill_price - buy_price) * fill_qty if (fill_price and buy_price) else None
            fees = 0.0

            self._storage.save_trade(
                symbol=symbol,
                side="sell",
                qty=fill_qty,
                price=fill_price,
                order_id=order_id,
                status="filled",
                trigger=trigger,
                reason=trigger,
                pnl=pnl,
                fees=fees,
            )
            self._storage.upsert_symbol_state(
                symbol,
                state="WATCHING",
                target_price=None,
                stop_loss=None,
                position_qty=None,
                position_open_at=None,
                chart_path=None,
                pending_order_id=None,
                analysis_summary=None,
            )
            pnl_str = f" | P&L: ${pnl:+.2f}" if pnl is not None else ""
            price_str = f"${fill_price:.2f}" if fill_price else "precio de mercado"
            self._logger.info(
                f"{symbol}: posición cerrada — {fill_qty:.0f} acc. @ {price_str}{pnl_str} → WATCHING",
                source="monitor",
            )
            if self._alerter and fill_price and buy_price and pnl is not None:
                try:
                    self._alerter.send_close_alert(
                        symbol=symbol,
                        qty=fill_qty,
                        buy_price=buy_price,
                        sell_price=fill_price,
                        pnl=pnl,
                        fees=fees,
                        trigger=trigger,
                    )
                except Exception:
                    pass
            return f"{symbol} SELL FILLED @ {price_str}{pnl_str}"

        elif status in ("cancelled", "expired", "rejected"):
            # Orden cancelada — volver a POSITION_OPEN para reintentar en próximo ciclo
            self._logger.info(
                f"{symbol}: orden de venta {status} → POSITION_OPEN (reintentará)",
                source="monitor",
            )
            self._storage.upsert_symbol_state(symbol, state="POSITION_OPEN", pending_order_id=None)
        else:
            self._logger.debug(f"{symbol}: orden de venta pendiente (status: {status})", source="monitor")
        return None

    # ── Helpers de sub-agentes ────────────────────────────────────────────────

    async def _run_technical_medium(self, symbol: str, price: float) -> tuple[dict | None, list[dict]]:
        """Ejecuta TechnicalWatchingAgent en modo medio plazo (90 días diarios)."""
        bars = []
        try:
            bars = await self._alpaca.get_bars(symbol, timeframe="1Day", limit=90)
            prompt = build_medium_term_prompt(symbol, price, bars)
            response = await self._technical_watching_agent.run(prompt)
            report = validate_technical_watching_report(response)
            self._storage.save_agent_report(symbol, "technical_medium", json.dumps(report))
            return report, bars
        except ParseError as e:
            self._logger.error(f"{symbol}: TechnicalWatchingAgent respuesta inválida: {e}", source="monitor")
            return None, bars
        except Exception as e:
            self._logger.error(f"{symbol}: error en TechnicalWatchingAgent: {e}", source="monitor", exc_info=True)
            return None, bars

    async def _run_technical_short(self, symbol: str, price: float) -> tuple[dict | None, list[dict]]:
        """Ejecuta TechnicalEntryAgent en modo corto plazo (5 días de barras de 15min)."""
        bars = []
        try:
            bars = await self._alpaca.get_bars(symbol, timeframe="15Min", limit=96)  # ~5 días
            # Cargar contexto de medio plazo (soportes/resistencias) si existe
            medium_report = None
            row = self._storage.get_last_agent_report(symbol, "technical_medium")
            if row:
                try:
                    medium_report = json.loads(row["report"])
                except Exception:
                    pass
            prompt = build_short_term_prompt(symbol, price, bars, medium_term_context=medium_report)
            response = await self._technical_entry_agent.run(prompt)
            report = validate_technical_report(response)
            self._storage.save_agent_report(symbol, "technical_short", json.dumps(report))
            return report, bars
        except ParseError as e:
            self._logger.error(f"{symbol}: TechnicalEntryAgent respuesta inválida: {e}", source="monitor")
            return None, bars
        except Exception as e:
            self._logger.error(f"{symbol}: error en TechnicalEntryAgent: {e}", source="monitor", exc_info=True)
            return None, bars

    # ── Market context global ──────────────────────────────────────────────────

    async def _maybe_update_market_context(self) -> None:
        """Actualiza el informe de contexto global de mercado si ha pasado el intervalo."""
        ctx = self._storage.get_latest_market_context()
        if ctx and not self._force_analysis:
            try:
                last_dt = datetime.fromisoformat(ctx["created_at"])
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                if elapsed_hours < self._analysis_interval_hours:
                    return
            except Exception:
                pass
        await self._run_market_context()

    async def _run_market_context(self) -> None:
        """Obtiene noticias generales y genera el informe holístico de contexto global."""
        self._logger.info("Actualizando contexto global de mercado...", source="monitor")
        try:
            from .tools.news_tool import SearchNewsTool
            news_tool = SearchNewsTool(self._gnews_api_key, logger=self._logger)

            market_raw = await news_tool.execute("stock market economy")
            market_news = _parse_news_list(market_raw)

            prompt = build_market_context_prompt(market_news)
            response = await self._market_context_agent.run(prompt)
            report = validate_market_context(response)

            self._storage.save_market_context(json.dumps(report), len(market_news))
            self._logger.info(
                f"Contexto global actualizado — sentiment: {report.get('market_sentiment', '?')} | "
                f"risk: {report.get('risk_appetite', '?')}",
                source="monitor",
            )
        except ParseError as e:
            self._logger.error(f"MarketContextAgent respuesta inválida: {e}", source="monitor")
        except Exception as e:
            self._logger.error(f"Error actualizando contexto global: {e}", source="monitor", exc_info=True)

    def _load_market_context(self) -> dict | None:
        """Lee el último informe de contexto global de la BD y lo deserializa."""
        ctx_row = self._storage.get_latest_market_context()
        if not ctx_row:
            return None
        try:
            return json.loads(ctx_row["report"])
        except Exception:
            return None

    async def _run_company_analysis(self, symbol: str) -> dict | None:
        """Obtiene noticias de la empresa y genera un informe breve de riesgo de entrada."""
        try:
            from .tools.news_tool import SearchNewsTool
            news_tool = SearchNewsTool(self._gnews_api_key, logger=self._logger)

            company_raw = await news_tool.execute(symbol)
            company_news = _parse_news_list(company_raw)

            prompt = build_company_analysis_prompt(symbol, company_news)
            response = await self._company_analysis_agent.run(prompt)
            report = validate_company_analysis(response)

            self._storage.save_agent_report(symbol, "company_analysis", json.dumps(report))
            return report
        except ParseError as e:
            self._logger.error(f"{symbol}: CompanyAnalysisAgent respuesta inválida: {e}", source="monitor")
            return None
        except Exception as e:
            self._logger.error(f"{symbol}: error en company analysis: {e}", source="monitor", exc_info=True)
            return None

    async def _run_trading_decision(
        self, symbol: str, stage: str, prompt: str, agent
    ) -> dict | None:
        """Ejecuta un TradingAgent y devuelve la decisión parseada."""
        try:
            response = await agent.run(prompt)
            decision = validate_trading_decision(response)
            self._storage.save_agent_report(symbol, f"trading_{stage}", json.dumps(decision))
            return decision
        except ParseError as e:
            self._logger.error(f"{symbol}: TradingAgent ({stage}) respuesta inválida: {e}", source="monitor")
            return None
        except Exception as e:
            self._logger.error(f"{symbol}: error en TradingAgent ({stage}): {e}", source="monitor", exc_info=True)
            return None

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


async def _interruptible_sleep(seconds: float, stop_event: asyncio.Event | None):
    """Duerme hasta `seconds` segundos, interrumpible por stop_event."""
    await _wait_or_stop(stop_event, int(seconds))


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
    gnews_key = os.environ.get("GNEWS_API_KEY")
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    alert_recipient = os.environ.get("ALERT_RECIPIENT") or gmail_user
    max_position_value = float(os.environ.get("MAX_POSITION_VALUE", "500"))

    if not api_key or not secret_key:
        logger.error("ALPACA_API_KEY y ALPACA_SECRET_KEY requeridas en .env", source="monitor")
        return
    if not gnews_key:
        logger.error("GNEWS_API_KEY requerida en .env", source="monitor")
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
    _register("trader_opportunity")
    _register("trader_entry")

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

    # Construir sub-agentes (F1: 4 agentes especializados)
    technical_watching_agent = await build_technical_watching_agent(config, bus, model_registry)
    technical_entry_agent = await build_technical_entry_agent(config, bus, model_registry)
    market_context_agent = await build_market_context_agent(config, bus, model_registry)
    company_analysis_agent = await build_company_analysis_agent(config, bus, model_registry)
    trading_opportunity_agent = await build_trading_opportunity_agent(config, bus, model_registry)
    trading_entry_agent = await build_trading_entry_agent(config, bus, model_registry)

    # Arrancar monitor
    monitor = PriceMonitor(
        alpaca=alpaca,
        storage=storage,
        technical_watching_agent=technical_watching_agent,
        technical_entry_agent=technical_entry_agent,
        market_context_agent=market_context_agent,
        company_analysis_agent=company_analysis_agent,
        trading_opportunity_agent=trading_opportunity_agent,
        trading_entry_agent=trading_entry_agent,
        logger=logger,
        gnews_api_key=gnews_key,
        max_position_value=effective_max_position,
        analysis_interval_hours=analysis_interval,
        alerter=alerter,
        force_event=force_event,
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
