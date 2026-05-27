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
)
from .economics_agent import (
    build_market_context_agent,
    build_company_analysis_agent,
)
from .trading_agent import (
    build_trading_opportunity_agent,
    build_trading_entry_agent,
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
        news_service,  # NewsIngestionService
        max_position_value: float = 500.0,
        analysis_interval_hours: float = 4.0,
        alerter=None,
        force_event: asyncio.Event | None = None,
        allow_short_selling: bool = False,
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
        self._news_service = news_service
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

        # Actualizar contexto global de mercado si corresponde (siempre, incluso con mercado cerrado)
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

        # Lookup de shortable por símbolo para inyectar en state_row
        wl_lookup = {item["symbol"]: item for item in watchlist}

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
            # Enriquecer state_row con shortable del watchlist (no está en symbol_states)
            state_row["shortable"] = wl_lookup.get(symbol, {}).get("shortable", 0)

            # Si cierre inminente, solo gestionar posiciones abiertas u órdenes en vuelo
            if close_imminent and state_row.get("state", "WATCHING") not in ("POSITION_OPEN", "OPENING", "CLOSING"):
                self._logger.debug(
                    f"{symbol}: análisis omitido — cierre de mercado en < 5 min",
                    source="monitor",
                )
            else:
                triggered = await self._process_symbol(symbol, snap, state_row, market_is_open=market_is_open)
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

        # Con mercado cerrado: dormir hasta apertura tras haber ejecutado análisis WATCHING y contexto
        if not market_is_open and wait_secs is not None:
            await _interruptible_sleep(wait_secs, stop_event, self._force_event)

    # ── Máquina de estados ────────────────────────────────────────────────────

    async def _process_symbol(
        self, symbol: str, snap: dict, state_row: dict, market_is_open: bool = True
    ) -> str | None:
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

        # Con mercado cerrado solo se ejecuta el análisis a largo plazo (WATCHING)
        if not market_is_open and state != "WATCHING":
            self._logger.debug(
                f"{symbol}: estado {state} omitido — mercado cerrado",
                source="monitor",
            )
            return None

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

        # 2. TradingOpportunityAgent — lee el informe técnico y contexto de mercado vía tools
        task = (
            f"Evaluate if {symbol} is worth monitoring for a trade entry. "
            f"Current price: ${price:.2f}. Max position: ${self._max_position_value:.0f} USD."
        )
        decision = await self._run_trading_decision(symbol, "opportunity_eval", task, self._trading_opportunity_agent)

        now = datetime.now(timezone.utc).isoformat()
        summary = f"Tech: {tech_report.get('trend')} (confidence: {tech_report.get('confidence', '?')})"

        self._storage.upsert_symbol_state(
            symbol,
            last_analysis_at=now,
            analysis_summary=summary,
        )

        if decision and decision.get("decision") == "OPPORTUNITY_FOUND":
            # F5: generar gráfico de evidencia al detectar oportunidad
            chart_path = self._generate_chart(symbol, bars, "1Day", "opportunity")
            if chart_path:
                report_id = self._storage.get_last_agent_report_id(symbol, "trading_opportunity")
                if report_id:
                    self._storage.update_agent_report_chart_path(report_id, chart_path)
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
        exchange = state_row.get("exchange") if state_row else None
        company_report = await self._run_company_analysis(symbol, exchange)
        if company_report is None:
            self._logger.warning(f"{symbol}: company analysis falló — continuando sin él", source="monitor")

        can_short = self._allow_short_selling and bool(state_row.get("shortable"))
        max_shares = max(1, int(self._max_position_value / price))
        short_hint = " Short selling is allowed (OPEN_SHORT is valid)." if can_short else ""
        task = (
            f"Decide entry for {symbol}. Current price: ${price:.2f}. "
            f"Max position: ${self._max_position_value:.0f} USD (max {max_shares} shares at current price).{short_hint}"
        )
        decision = await self._run_trading_decision(symbol, "entry_eval", task, self._trading_entry_agent)

        if not decision:
            return None

        if decision.get("decision") in ("OPEN_POSITION", "OPEN_SHORT"):
            is_short = decision.get("decision") == "OPEN_SHORT"
            if is_short and not can_short:
                self._logger.info(f"{symbol}: OPEN_SHORT ignorado — short selling deshabilitado o símbolo no shortable", source="monitor")
                return None
            side = "sell" if is_short else "buy"
            position_side = "short" if is_short else "long"

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

            direction = "SHORT" if is_short else "LONG"
            self._logger.info(
                f"{symbol}: abriendo posición {direction} — qty={qty} (${price:.2f}×{qty}=${price*qty:.0f}) "
                f"target=${target} stop=${stop}",
                source="monitor",
            )

            # Generar gráfico de evidencia al abrir posición
            chart_path = self._generate_chart(symbol, bars, "15Min", "entry")
            if chart_path:
                report_id = self._storage.get_last_agent_report_id(symbol, "trading_entry")
                if report_id:
                    self._storage.update_agent_report_chart_path(report_id, chart_path)

            try:
                result = await self._alpaca.place_order(
                    symbol, qty, side,
                    take_profit_price=target if (target and stop) else None,
                    stop_loss_price=stop if (target and stop) else None,
                )
                bracket_id = result.get("order_id") if (target and stop) else None
                self._storage.upsert_symbol_state(
                    symbol,
                    state="OPENING",
                    target_price=target,
                    stop_loss=stop,
                    position_qty=float(qty),
                    position_side=position_side,
                    pending_order_id=result.get("order_id"),
                    bracket_order_id=bracket_id,
                    chart_path=chart_path,
                )
                bracket_info = " [bracket]" if bracket_id else ""
                self._logger.info(
                    f"{symbol}: orden {side.upper()}×{qty} enviada{bracket_info} (order_id={result.get('order_id')}) → OPENING",
                    source="monitor",
                )
                return f"{symbol} {side.upper()}×{qty} enviada (OPENING)"
            except Exception as e:
                self._logger.error(f"{symbol}: error al abrir posición: {e}", source="monitor", exc_info=True)

        elif decision.get("decision") == "REJECT":
            self._logger.info(f"{symbol}: señal rechazada → volviendo a WATCHING", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="WATCHING", entry_signal_at=None)

        return None

    # ── Estado: POSITION_OPEN ─────────────────────────────────────────────────

    async def _manage_position(self, symbol: str, price: float, state_row: dict) -> str | None:
        """
        Gestiona una posición abierta.

        Orden de verificación:
        1. Si existe bracket_order_id, comprueba si alguna leg fue ejecutada por Alpaca.
        2. Fallback: compara precio actual contra target/stop del monitor.
           Si se activa, cancela las legs activas del bracket antes de colocar la orden de cierre.
        """
        target = state_row.get("target_price")
        stop = state_row.get("stop_loss")
        qty = state_row.get("position_qty") or 0
        position_side = state_row.get("position_side", "long")
        bracket_order_id = state_row.get("bracket_order_id")

        if not qty:
            self._logger.warning(f"{symbol}: POSITION_OPEN sin qty registrada → WATCHING", source="monitor")
            self._storage.upsert_symbol_state(symbol, state="WATCHING")
            return None

        close_side = "buy" if position_side == "short" else "sell"

        # ── 1. Verificar si Alpaca ejecutó una leg del bracket automáticamente ─
        if bracket_order_id:
            try:
                bracket_order = await self._alpaca.get_order(bracket_order_id)
                if bracket_order:
                    for leg in (bracket_order.get("legs") or []):
                        if leg.get("status") == "filled":
                            leg_id = leg.get("id")
                            leg_order = await self._alpaca.get_order(leg_id) if leg_id else None
                            fill_price = (leg_order.get("filled_avg_price") if leg_order else None) or price
                            fill_qty = (leg_order.get("filled_qty") if leg_order else None) or qty
                            fees = (leg_order.get("commission") if leg_order else 0.0) or 0.0

                            leg_type = leg.get("type", "")
                            if leg_type == "limit":
                                trigger = f"Take-profit ${target:.2f} ejecutado automáticamente por Alpaca"
                            else:
                                trigger = f"Stop-loss ${stop:.2f} ejecutado automáticamente por Alpaca"

                            if position_side == "short":
                                entry_trade = self._storage.get_last_sell_trade(symbol)
                                entry_price = entry_trade.get("price") if entry_trade else None
                                pnl = (entry_price - fill_price) * fill_qty if (fill_price and entry_price) else None
                                alert_buy, alert_sell = fill_price, entry_price
                            else:
                                entry_trade = self._storage.get_last_buy_trade(symbol)
                                entry_price = entry_trade.get("price") if entry_trade else None
                                pnl = (fill_price - entry_price) * fill_qty if (fill_price and entry_price) else None
                                alert_buy, alert_sell = entry_price, fill_price

                            self._storage.save_trade(
                                symbol=symbol,
                                side=close_side,
                                qty=fill_qty,
                                price=fill_price,
                                order_id=leg_id,
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
                                position_side="long",
                                bracket_order_id=None,
                            )
                            pnl_str = f" | P&L: ${pnl:+.2f}" if pnl is not None else ""
                            price_str = f"${fill_price:.2f}" if fill_price else "precio de mercado"
                            self._logger.info(
                                f"{symbol}: posición {position_side} cerrada por bracket Alpaca — "
                                f"{fill_qty:.0f} acc. @ {price_str}{pnl_str} → WATCHING",
                                source="monitor",
                            )
                            if self._alerter and fill_price and entry_price and pnl is not None:
                                try:
                                    self._alerter.send_close_alert(
                                        symbol=symbol,
                                        qty=fill_qty,
                                        buy_price=alert_buy,
                                        sell_price=alert_sell,
                                        pnl=pnl,
                                        fees=fees,
                                        trigger=trigger,
                                    )
                                except Exception:
                                    pass
                            return f"{symbol} {close_side.upper()} FILLED @ {price_str}{pnl_str} (bracket)"
            except Exception as e:
                self._logger.error(
                    f"{symbol}: error verificando bracket order {bracket_order_id}: {e}",
                    source="monitor",
                    exc_info=True,
                )

        # ── 2. Fallback: lógica de precio ──────────────────────────────────────
        reason = None
        if position_side == "short":
            if target and price <= target:
                reason = f"Short target ${target:.2f} alcanzado (precio actual: ${price:.2f})"
            elif stop and price >= stop:
                reason = f"Short stop-loss ${stop:.2f} activado (precio actual: ${price:.2f})"
        else:
            if target and price >= target:
                reason = f"Target ${target:.2f} alcanzado (precio actual: ${price:.2f})"
            elif stop and price <= stop:
                reason = f"Stop-loss ${stop:.2f} activado (precio actual: ${price:.2f})"

        if not reason:
            self._logger.debug(
                f"{symbol}: posición {position_side} abierta — precio ${price:.2f} | target ${target} | stop ${stop}",
                source="monitor",
            )
            return None

        # Cancelar legs activas del bracket antes de colocar orden de cierre manual
        if bracket_order_id:
            try:
                bracket_order = await self._alpaca.get_order(bracket_order_id)
                if bracket_order:
                    for leg in (bracket_order.get("legs") or []):
                        if leg.get("status") not in ("filled", "cancelled", "expired", "rejected"):
                            leg_id = leg.get("id")
                            if leg_id:
                                await self._alpaca.cancel_order(leg_id)
                                self._logger.debug(
                                    f"{symbol}: leg bracket {leg_id} cancelada (fallback precio)",
                                    source="monitor",
                                )
            except Exception as e:
                self._logger.error(
                    f"{symbol}: error cancelando legs del bracket: {e}",
                    source="monitor",
                    exc_info=True,
                )

        self._logger.info(f"{symbol}: cerrando posición {position_side} — {reason}", source="monitor")
        try:
            result = await self._alpaca.place_order(symbol, qty, close_side)
            self._storage.upsert_symbol_state(
                symbol,
                state="CLOSING",
                pending_order_id=result.get("order_id"),
                analysis_summary=reason,
                bracket_order_id=None,
            )
            self._logger.info(
                f"{symbol}: orden {close_side.upper()}×{qty} enviada (order_id={result.get('order_id')}) → CLOSING",
                source="monitor",
            )
            return f"{symbol} {close_side.upper()}×{qty} enviada (CLOSING)"
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
            position_side = state_row.get("position_side", "long")

            fees = order.get("commission", 0.0)

            if position_side == "short":
                # Cierre de short: la entrada fue un SELL, la salida es un BUY
                entry_trade = self._storage.get_last_sell_trade(symbol)
                entry_price = entry_trade.get("price") if entry_trade else None
                pnl = (entry_price - fill_price) * fill_qty if (fill_price and entry_price) else None
                close_side = "buy"
                buy_price = entry_price  # para la alerta
            else:
                entry_trade = self._storage.get_last_buy_trade(symbol)
                entry_price = entry_trade.get("price") if entry_trade else None
                pnl = (fill_price - entry_price) * fill_qty if (fill_price and entry_price) else None
                close_side = "sell"
                buy_price = entry_price

            self._storage.save_trade(
                symbol=symbol,
                side=close_side,
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
                position_side="long",
                bracket_order_id=None,
            )
            pnl_str = f" | P&L: ${pnl:+.2f}" if pnl is not None else ""
            price_str = f"${fill_price:.2f}" if fill_price else "precio de mercado"
            self._logger.info(
                f"{symbol}: posición {position_side} cerrada — {fill_qty:.0f} acc. @ {price_str}{pnl_str} → WATCHING",
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
            return f"{symbol} {close_side.upper()} FILLED @ {price_str}{pnl_str}"

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
        """Ejecuta TechnicalWatchingAgent — el agente obtiene sus propios datos vía tools."""
        bars = []
        try:
            # El agente obtiene los datos y guarda el informe vía tools internas
            response = await self._technical_watching_agent.run(
                f"Analyze {symbol} for medium-term trend. Current price: ${price:.2f}."
            )
            report = validate_technical_watching_report(response)
            # Obtener barras por separado para la generación del gráfico de evidencia
            try:
                bars = await self._alpaca.get_bars(symbol, timeframe="1Day", limit=90)
            except Exception:
                pass
            return report, bars
        except ParseError as e:
            self._logger.error(f"{symbol}: TechnicalWatchingAgent respuesta inválida: {e}", source="monitor")
            return None, bars
        except Exception as e:
            self._logger.error(f"{symbol}: error en TechnicalWatchingAgent: {e}", source="monitor", exc_info=True)
            return None, bars

    async def _run_technical_short(self, symbol: str, price: float) -> tuple[dict | None, list[dict]]:
        """Ejecuta TechnicalEntryAgent — el agente obtiene sus propios datos vía tools."""
        bars = []
        try:
            # El agente obtiene los datos, lee el contexto de medio plazo y guarda el informe vía tools
            response = await self._technical_entry_agent.run(
                f"Analyze {symbol} for short-term entry timing. Current price: ${price:.2f}."
            )
            report = validate_technical_report(response)
            # Obtener barras por separado para la generación del gráfico de evidencia
            try:
                bars = await self._alpaca.get_bars(symbol, timeframe="15Min", limit=96)
            except Exception:
                pass
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
        """Actualiza el contexto global de mercado — el agente obtiene y clasifica noticias vía tools."""
        self._logger.info("Actualizando contexto global de mercado...", source="monitor")
        try:
            # El agente fetcha noticias vía FetchMarketNewsTool y guarda el informe vía SaveMarketContextTool
            response = await self._market_context_agent.run("Update global market context.")
            report = validate_market_context(response)
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

    async def _run_company_analysis(self, symbol: str, exchange: str | None = None) -> dict | None:
        """Analiza noticias de empresa — el agente fetcha y clasifica noticias vía tools."""
        try:
            exchange_hint = f" Exchange: {exchange}." if exchange else ""
            response = await self._company_analysis_agent.run(
                f"Analyze company news for {symbol}.{exchange_hint}"
            )
            report = validate_company_analysis(response)
            return report
        except ParseError as e:
            self._logger.error(f"{symbol}: CompanyAnalysisAgent respuesta inválida: {e}", source="monitor")
            return None
        except Exception as e:
            self._logger.error(f"{symbol}: error en company analysis: {e}", source="monitor", exc_info=True)
            return None

    async def _run_trading_decision(
        self, symbol: str, stage: str, task: str, agent
    ) -> dict | None:
        """Ejecuta un TradingAgent con una tarea simple y devuelve la decisión parseada.

        El agente lee sus propios informes vía tools (GetLastReportTool, GetMarketContextTool)
        y guarda el resultado vía SaveAgentReportTool. El monitor solo valida la respuesta.
        """
        try:
            response = await agent.run(task)
            decision = validate_trading_decision(response)
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

    # Construir NewsIngestionService primero (necesario para los agentes de economía)
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

    # Construir sub-agentes (pasan alpaca/storage/news_service para sus tools)
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
        news_service=news_service,
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
