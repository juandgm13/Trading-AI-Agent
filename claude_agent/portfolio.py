"""
Cartera del usuario a partir de sus movimientos (`claude_agent/inputs/cartera.csv`).

El usuario edita el CSV como una tabla (Excel, LibreOffice, Numbers, GitHub…). Este script
reconstruye las posiciones abiertas con el método de **precio medio ponderado**, obtiene el
último precio público de Yahoo Finance, convierte a EUR y calcula pesos, ganancias/pérdidas
y la exposición a criptomonedas frente al tope de `config.yaml` (`crypto.max_portfolio_pct`).

Columnas del CSV (cabecera obligatoria, separador `,` o `;`, decimales con `.` o `,`):
    fecha,activo,nombre,tipo,operacion,cantidad,precio,moneda,comision,broker,notas
- activo:    ticker de Yahoo Finance (SAN.MC, AAPL, SPY) o par de Coinbase para cripto (BTC-EUR, PEPE-USD…)
- tipo:      accion | etf | fondo | cripto | liquidez | otro
             liquidez: dinero disponible (activo `EUR`, o `USDC`/`USD` en dólares), precio 1; `compra` = ingreso,
             `venta` = retirada o dinero usado para invertir. No se busca precio y no cuenta como cripto.
- operacion: compra | venta | dividendo   (dividendo: `precio` = importe neto cobrado; `cantidad` se ignora)
- precio:    precio por unidad en `moneda`; comision: gastos totales de la operación en `moneda`

Simplificación: la ganancia en EUR de un activo en divisa se calcula con el tipo de cambio actual
(no se guarda el cambio histórico de cada compra).

`config.yaml` → `portfolio.aportacion_mensual` ({importe, moneda, dia}) se devuelve con la próxima fecha
para que el informe proponga cómo repartir cada aportación periódica (no se suma sola a la liquidez).

Evolución: como `data/` no se guarda entre ejecuciones, la evolución diaria de la cartera se **reconstruye cada día**
a partir de los movimientos y de los cierres históricos de Yahoo (Coinbase para pares cripto que Yahoo no tenga),
desde `portfolio.grafico.desde` (por defecto, el primer movimiento de liquidez = inicio del plan). Se guarda en el
JSON (`evolucion`, `evolucion_resumen`) y se dibuja en `docs/informes/charts/FECHA/cartera.png`.

Uso:
    python claude_agent/portfolio.py            # → data/portfolio_FECHA.json + gráfico de evolución
    python claude_agent/portfolio.py --no-chart # sin evolución ni gráfico
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
import calendar
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_HERE.parent))

from claude_agent.fetch_news import DEFAULT_CONFIG, load_config  # noqa: E402
from claude_agent.fetch_prices import DEFAULT_CHARTS_DIR, fetch_history  # noqa: E402

DEFAULT_DATA_DIR = _HERE / "data"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{product}/ticker"
COLUMNS = ["fecha", "activo", "nombre", "tipo", "operacion", "cantidad", "precio", "moneda",
           "comision", "broker", "notas"]
REQUIRED = ("fecha", "activo", "operacion", "precio")
OPERATIONS = ("compra", "venta", "dividendo")
CASH_TYPE = "liquidez"              # dinero disponible: precio fijo 1 en su moneda, sin buscar cotización
_EPS = 1e-9


@dataclass
class Movement:
    fecha: date
    activo: str
    nombre: str
    tipo: str
    operacion: str
    cantidad: float
    precio: float
    moneda: str
    comision: float
    broker: str


@dataclass
class Position:
    activo: str
    nombre: str = ""
    tipo: str = "otro"
    moneda: str = "EUR"
    broker: str = ""
    cantidad: float = 0.0
    coste: float = 0.0               # coste total de la posición abierta (incluye comisiones), en `moneda`
    realizado: float = 0.0           # ganancia/pérdida realizada (ventas + dividendos), en `moneda`
    dividendos: float = 0.0
    first_buy: date | None = None
    movimientos: int = 0
    brokers: set = field(default_factory=set)

    @property
    def precio_medio(self) -> float | None:
        return self.coste / self.cantidad if self.cantidad > _EPS else None


# ── Lectura del CSV ──────────────────────────────────────────────────────────

def _num(value: str | None, default: float | None = None) -> float | None:
    """Acepta '1.234,56', '1234.56', '1,5' y vacío."""
    text = (value or "").strip().replace(" ", "").replace("€", "").replace("$", "")
    if not text:
        return default
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") \
            else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _date(value: str) -> date:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: {value!r} (usa AAAA-MM-DD)")


def read_movements(text: str) -> tuple[list[Movement], list[dict]]:
    """Parsea el CSV. Devuelve movimientos válidos (ordenados por fecha) y las filas con error."""
    issues: list[dict] = []
    first = next((ln for ln in text.splitlines() if ln.strip()), None)
    if first is None:
        return [], issues
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        return [], [{"row": 1, "message": f"faltan columnas obligatorias: {', '.join(missing)}"}]
    reader.fieldnames = header

    movements: list[Movement] = []
    for row in reader:
        n = reader.line_num  # nº de línea real del archivo (el que ve el usuario en su hoja)
        row = {k: (v or "").strip() for k, v in row.items() if k}
        try:
            op = row.get("operacion", "").lower()
            if op not in OPERATIONS:
                raise ValueError(f"operación {op!r} no válida (compra | venta | dividendo)")
            activo = row.get("activo", "").upper()
            if not activo:
                raise ValueError("falta el activo")
            cantidad = _num(row.get("cantidad"), 0.0 if op == "dividendo" else None)
            if cantidad is None or (op != "dividendo" and cantidad <= 0):
                raise ValueError("la cantidad debe ser mayor que 0")
            precio = _num(row.get("precio"))
            if precio is None or precio < 0:
                raise ValueError("precio no válido")
            tipo = (row.get("tipo") or "").lower() or ("cripto" if _is_crypto_pair(activo) else "otro")
            movements.append(Movement(
                fecha=_date(row.get("fecha", "")),
                activo=activo,
                nombre=row.get("nombre", ""),
                tipo=tipo,
                operacion=op,
                cantidad=cantidad,
                precio=precio,
                moneda=(row.get("moneda") or "EUR").upper(),
                comision=_num(row.get("comision"), 0.0) or 0.0,
                broker=row.get("broker", ""),
            ))
        except ValueError as exc:
            issues.append({"row": n, "message": str(exc)})
    movements.sort(key=lambda m: m.fecha)
    return movements, issues


# ── Posiciones ───────────────────────────────────────────────────────────────

def build_positions(movements: list[Movement]) -> tuple[dict[str, Position], list[dict]]:
    """Aplica los movimientos en orden con precio medio ponderado."""
    positions: dict[str, Position] = {}
    issues: list[dict] = []
    for m in movements:
        pos = positions.setdefault(m.activo, Position(activo=m.activo, moneda=m.moneda))
        pos.nombre = m.nombre or pos.nombre
        pos.tipo = m.tipo if m.tipo != "otro" else pos.tipo
        pos.movimientos += 1
        if m.broker:
            pos.brokers.add(m.broker)
        if m.moneda != pos.moneda:
            issues.append({"activo": m.activo, "message": f"moneda {m.moneda} distinta de {pos.moneda}; se ignora"})
            continue
        if m.operacion == "compra":
            pos.cantidad += m.cantidad
            pos.coste += m.cantidad * m.precio + m.comision
            pos.first_buy = pos.first_buy or m.fecha
        elif m.operacion == "venta":
            qty = m.cantidad
            if qty > pos.cantidad + _EPS:
                issues.append({"activo": m.activo,
                               "message": f"venta de {qty:g} con solo {pos.cantidad:g} en cartera ({m.fecha}); se ajusta"})
                qty = pos.cantidad
            if qty <= _EPS:
                continue
            avg = pos.coste / pos.cantidad
            pos.realizado += qty * m.precio - m.comision - qty * avg
            pos.coste -= qty * avg
            pos.cantidad -= qty
            if pos.cantidad <= _EPS:
                pos.cantidad, pos.coste, pos.first_buy = 0.0, 0.0, None
        else:  # dividendo
            pos.realizado += m.precio - m.comision
            pos.dividendos += m.precio - m.comision
    return positions, issues


def public_brokers(brokers: set[str]) -> str:
    """Privacidad: el informe se publica en la web y solo se nombra Coinbase; el resto → "otro bróker"."""
    names = {b if "coinbase" in b.lower() else "otro bróker" for b in brokers if b.strip()}
    return ", ".join(sorted(names, key=lambda n: (n == "otro bróker", n)))


def fx_symbol(currency: str, base: str = "EUR") -> str:
    """Ticker de Yahoo con unidades de `currency` por 1 `base` (EURUSD=X → dólares por euro)."""
    return f"{base}{currency}=X"


def summarize(positions: dict[str, Position], prices: dict[str, float], fx: dict[str, float],
              base: str = "EUR", crypto_cap_pct: float | None = None) -> dict:
    """Valora posiciones en `base`. `fx[moneda]` = unidades de moneda por 1 `base`."""
    issues: list[dict] = []

    def to_base(amount: float, currency: str) -> float | None:
        if currency == base:
            return amount
        rate = fx.get(currency)
        return amount / rate if rate else None

    rows, closed = [], []
    realized_base = 0.0
    for pos in positions.values():
        realized = to_base(pos.realizado, pos.moneda)
        if realized is not None:
            realized_base += realized
        if pos.cantidad <= _EPS:
            closed.append({"activo": pos.activo, "nombre": pos.nombre, "tipo": pos.tipo,
                           "realizado": round(pos.realizado, 2), "moneda": pos.moneda})
            continue
        price = 1.0 if pos.tipo == CASH_TYPE else prices.get(pos.activo)
        value = pos.cantidad * price if price is not None else None
        if price is None:
            issues.append({"activo": pos.activo, "message": "sin precio actual; se valora a coste"})
        value_base = to_base(value if value is not None else pos.coste, pos.moneda)
        cost_base = to_base(pos.coste, pos.moneda)
        if value_base is None:
            issues.append({"activo": pos.activo, "message": f"sin tipo de cambio {pos.moneda}/{base}"})
        pnl = value - pos.coste if value is not None else None
        rows.append({
            "activo": pos.activo,
            "nombre": pos.nombre,
            "tipo": pos.tipo,
            "broker": public_brokers(pos.brokers),
            "moneda": pos.moneda,
            "cantidad": round(pos.cantidad, 8),
            "precio_medio": round(pos.precio_medio, 4),
            "precio_actual": price,
            "valor": round(value, 2) if value is not None else None,
            "coste": round(pos.coste, 2),
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl / pos.coste * 100, 2) if pnl is not None and pos.coste else None,
            "valor_eur": round(value_base, 2) if value_base is not None else None,
            "coste_eur": round(cost_base, 2) if cost_base is not None else None,
            "pnl_eur": round(value_base - cost_base, 2)
            if value_base is not None and cost_base is not None and value is not None else None,
            "realizado": round(pos.realizado, 2),
            "dividendos": round(pos.dividendos, 2),
            "desde": pos.first_buy.isoformat() if pos.first_buy else None,
        })

    total = sum(r["valor_eur"] or 0 for r in rows)
    cash = sum(r["valor_eur"] or 0 for r in rows if r["tipo"] == CASH_TYPE)
    invested = total - cash
    cost = sum(r["coste_eur"] or 0 for r in rows if r["tipo"] != CASH_TYPE)
    for r in rows:
        r["peso_pct"] = round((r["valor_eur"] or 0) / total * 100, 2) if total else None
    rows.sort(key=lambda r: r["valor_eur"] or 0, reverse=True)

    by_type: dict[str, float] = {}
    for r in rows:
        by_type[r["tipo"]] = by_type.get(r["tipo"], 0) + (r["valor_eur"] or 0)
    crypto_value = by_type.get("cripto", 0.0)
    crypto_pct = round(crypto_value / total * 100, 2) if total else None
    crypto = {
        "valor_eur": round(crypto_value, 2),
        "peso_pct": crypto_pct,
        "tope_pct": crypto_cap_pct,
        "excede_tope": crypto_pct is not None and crypto_cap_pct is not None and crypto_pct > crypto_cap_pct,
        # Cuánto se podría añadir en cripto (con el resto de la cartera igual) sin superar el tope
        "margen_hasta_tope_eur": round(max(0.0, (crypto_cap_pct / 100 * total - crypto_value)
                                           / (1 - crypto_cap_pct / 100)), 2)
        if crypto_cap_pct is not None and total and crypto_cap_pct < 100 else None,
    }
    return {
        "base_currency": base,
        "totals": {
            "valor_eur": round(total, 2),                # patrimonio: inversiones + liquidez
            "invertido_eur": round(invested, 2),         # valor actual de las inversiones (sin liquidez)
            "liquidez_eur": round(cash, 2),
            "coste_eur": round(cost, 2),                 # coste de las inversiones (sin liquidez)
            "pnl_no_realizado_eur": round(invested - cost, 2),
            "pnl_no_realizado_pct": round((invested / cost - 1) * 100, 2) if cost else None,
            "pnl_realizado_eur": round(realized_base, 2),
        },
        "por_tipo_pct": {t: round(v / total * 100, 2) for t, v in by_type.items()} if total else {},
        "crypto": crypto,
        "liquidez": {"valor_eur": round(cash, 2), "peso_pct": round(cash / total * 100, 2) if total else None},
        "positions": rows,
        "closed_positions": closed,
        "fx": fx,
        "issues": issues,
    }



# ── Evolución de la cartera ──────────────────────────────────────────────────

CHART_FILE = "cartera.png"
# Orden de las capas del gráfico de reparto (la liquidez, arriba) y colores fijos
TYPE_LAYERS = [("etf", "ETF", "#2563eb"), ("fondo", "Fondos", "#0891b2"), ("accion", "Acciones", "#7c3aed"),
               ("cripto", "Cripto", "#f59e0b"), ("otro", "Otros", "#64748b"), (CASH_TYPE, "Liquidez", "#cbd5e1")]
_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def evolution_start(movements: list[Movement], desde=None) -> date | None:
    """Inicio del gráfico: `portfolio.grafico.desde` o, por defecto, el primer movimiento de liquidez."""
    if desde:
        return desde if isinstance(desde, date) else _date(str(desde))
    cash = [m.fecha for m in movements if m.tipo == CASH_TYPE]
    if cash:
        return min(cash)
    return movements[0].fecha if movements else None


def history_range(start: date, today: date) -> str:
    """Rango de Yahoo suficiente para cubrir desde `start` (con margen para el relleno hacia delante)."""
    days = (today - start).days + 10
    for limit, rng in ((30, "1mo"), (90, "3mo"), (180, "6mo"), (365, "1y"), (730, "2y"), (1825, "5y")):
        if days <= limit:
            return rng
    return "max"


def daily_closes(df: pd.DataFrame) -> pd.Series:
    """Cierres por día natural (fecha local de la vela; si hay varias el mismo día, la última)."""
    close = df["Close"].dropna()
    days = [ts.date() for ts in pd.to_datetime(close.index)]
    return pd.Series(close.values, index=days).groupby(level=0).last().astype(float)


def _filled(series: pd.Series | None, days: list[date]) -> dict[date, float]:
    """Último cierre conocido para cada día (fines de semana y festivos con el cierre anterior)."""
    if series is None or series.empty:
        return {}
    idx = sorted(set(series.index) | set(days))
    full = series.reindex(idx).ffill()
    return {d: float(full[d]) for d in days if pd.notna(full[d])}


def build_evolution(movements: list[Movement], histories: dict[str, pd.Series], fx_histories: dict[str, pd.Series],
                    start: date, end: date, fx_now: dict[str, float], base: str = "EUR") -> tuple[list[dict], list[dict]]:
    """Valor diario de la cartera entre `start` y `end` (ambos incluidos).

    - Posiciones: se reaplican con `build_positions` los movimientos hasta cada día.
    - Valor: último cierre conocido × cantidad, al tipo de cambio de ese día (si falta, el actual).
    - Coste ("lo que has puesto"): al tipo de cambio actual, igual que `summarize`, para que la línea solo cambie
      con compras y ventas. Sin cierre histórico, el activo se valora a coste (y se avisa una vez).
    """
    days = [d.date() for d in pd.date_range(start, end, freq="D")]
    prices = {sym: _filled(series, days) for sym, series in histories.items()}
    rates = {cur: _filled(series, days) for cur, series in fx_histories.items()}
    issues: list[dict] = []
    warned: set[str] = set()
    points = []
    for day in days:
        positions, _ = build_positions([m for m in movements if m.fecha <= day])
        by_type: dict[str, float] = {}
        cost = 0.0
        for pos in positions.values():
            if pos.cantidad <= _EPS:
                continue
            rate_now = 1.0 if pos.moneda == base else fx_now.get(pos.moneda)
            rate_day = 1.0 if pos.moneda == base else rates.get(pos.moneda, {}).get(day, rate_now)
            if not rate_now or not rate_day:
                continue
            if pos.tipo == CASH_TYPE:
                value = pos.cantidad / rate_day
            else:
                price = prices.get(pos.activo, {}).get(day)
                if price is None:
                    if pos.activo not in warned:
                        warned.add(pos.activo)
                        issues.append({"activo": pos.activo,
                                       "message": "evolución: sin cierre histórico; se valora a coste"})
                    value = pos.coste / rate_now
                else:
                    value = pos.cantidad * price / rate_day
                cost += pos.coste / rate_now
            by_type[pos.tipo] = by_type.get(pos.tipo, 0.0) + value
        cash = by_type.get(CASH_TYPE, 0.0)
        total = sum(by_type.values())
        invested = total - cash
        points.append({
            "fecha": day.isoformat(),
            "valor_inversiones_eur": round(invested, 2),
            "coste_eur": round(cost, 2),
            "pnl_eur": round(invested - cost, 2),
            "pnl_pct": round((invested / cost - 1) * 100, 2) if cost else None,
            "liquidez_eur": round(cash, 2),
            "patrimonio_eur": round(total, 2),
            "por_tipo_eur": {t: round(v, 2) for t, v in by_type.items()},
        })
    return points, issues


def today_point(summary: dict, day: date) -> dict:
    """Último punto de la serie con los totales de `summarize` (cuadra al céntimo con el resto del JSON)."""
    t = summary["totals"]
    by_type: dict[str, float] = {}
    for r in summary["positions"]:
        by_type[r["tipo"]] = by_type.get(r["tipo"], 0.0) + (r["valor_eur"] or 0)
    return {
        "fecha": day.isoformat(),
        "valor_inversiones_eur": t["invertido_eur"],
        "coste_eur": t["coste_eur"],
        "pnl_eur": t["pnl_no_realizado_eur"],
        "pnl_pct": t["pnl_no_realizado_pct"],
        "liquidez_eur": t["liquidez_eur"],
        "patrimonio_eur": t["valor_eur"],
        "por_tipo_eur": {k: round(v, 2) for k, v in by_type.items()},
    }


def evolution_summary(points: list[dict]) -> dict | None:
    """Cifras listas para el informe: cambios de la ganancia/pérdida (no dependen de las aportaciones)."""
    if not points:
        return None
    last, first = points[-1], points[0]

    def change(n: int, key: str) -> float | None:
        return round(last[key] - points[-1 - n][key], 2) if len(points) > n else None

    best = max(points, key=lambda p: p["pnl_eur"])
    worst = min(points, key=lambda p: p["pnl_eur"])
    total = last["patrimonio_eur"]
    return {
        "desde": first["fecha"],
        "dias": len(points),
        "pnl_eur": last["pnl_eur"],
        "pnl_pct": last["pnl_pct"],
        "pnl_cambio_1d_eur": change(1, "pnl_eur"),
        "pnl_cambio_7d_eur": change(7, "pnl_eur"),
        "patrimonio_cambio_1d_eur": change(1, "patrimonio_eur"),
        "patrimonio_cambio_desde_inicio_eur": round(total - first["patrimonio_eur"], 2),
        "puesto_en_periodo_eur": round(last["coste_eur"] - first["coste_eur"], 2),
        "invertido_pct": round(last["valor_inversiones_eur"] / total * 100, 2) if total else None,
        "pnl_max": {"fecha": best["fecha"], "eur": best["pnl_eur"]},
        "pnl_min": {"fecha": worst["fecha"], "eur": worst["pnl_eur"]},
    }


async def fetch_histories(symbols: list[str], currencies: list[str], start: date, now: datetime,
                          base: str = "EUR") -> tuple[dict[str, pd.Series], dict[str, pd.Series], list[dict]]:
    """Cierres diarios de Yahoo para activos y divisas; pares cripto sin Yahoo → velas de Coinbase."""
    from claude_agent.fetch_crypto import fetch_coinbase_history

    rng = history_range(start, now.date())
    fx_syms = [fx_symbol(c, base) for c in currencies]
    all_syms = symbols + fx_syms
    issues: list[dict] = []
    hist: dict[str, pd.Series] = {}
    if not all_syms:
        return {}, {}, issues
    async with aiohttp.ClientSession(trust_env=True) as session:
        results = await asyncio.gather(*(fetch_history(session, s, range_=rng) for s in all_syms),
                                       return_exceptions=True)
        retry = [s for s, r in zip(all_syms, results) if isinstance(r, Exception) and _is_crypto_pair(s)]
        pages = max(1, -(-((now.date() - start).days + 10) // 300))
        fallback = dict(zip(retry, await asyncio.gather(
            *(fetch_coinbase_history(session, s, now, pages=pages) for s in retry), return_exceptions=True
        )))
    for sym, res in zip(all_syms, results):
        if isinstance(res, Exception) and sym in fallback and not isinstance(fallback[sym], Exception):
            res = fallback[sym]
        if isinstance(res, Exception):
            issues.append({"activo": sym, "message": f"histórico: {type(res).__name__}: {res}"})
            continue
        hist[sym] = daily_closes(res)
    fx_hist = {c: hist.pop(s) for c, s in zip(currencies, fx_syms) if s in hist}
    return hist, fx_hist, issues


def _eur(value: float, decimals: int = 0) -> str:
    """Formato español: 1.234,5."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def make_portfolio_chart(points: list[dict], movements: list[Movement], out_dir: Path,
                         objetivo_liquidez_pct: float | None = None) -> str:
    """Gráfico de dos paneles: inversiones (valor vs. lo puesto, ganancia en verde/pérdida en rojo, compras y
    ventas marcadas) y reparto del patrimonio por tipo (cuánto está ya invertido)."""
    import logging

    import matplotlib

    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    out_dir.mkdir(parents=True, exist_ok=True)
    x = pd.to_datetime([p["fecha"] for p in points])
    value = pd.Series([p["valor_inversiones_eur"] for p in points], index=x)
    cost = pd.Series([p["coste_eur"] for p in points], index=x)
    last = points[-1]

    fig, (ax1, ax3, ax2) = plt.subplots(3, 1, figsize=(11, 9), sharex=True,
                                        gridspec_kw={"height_ratios": [2.4, 1.3, 1], "hspace": 0.32})
    # Panel 1 — inversiones
    ax1.fill_between(x, value, cost, where=value >= cost, interpolate=True, color="#16a34a", alpha=0.22,
                     label="Ganancia")
    ax1.fill_between(x, value, cost, where=value < cost, interpolate=True, color="#dc2626", alpha=0.22,
                     label="Pérdida")
    ax1.plot(x, cost, color="#6b7280", linestyle="--", linewidth=1.6, label="Lo que has puesto")
    ax1.plot(x, value, color="#1d4ed8", linewidth=2.4, label="Lo que valen tus inversiones")
    start, end = points[0]["fecha"], last["fecha"]
    seen: dict[str, int] = {}
    for m in movements:
        day = m.fecha.isoformat()
        if m.tipo == CASH_TYPE or m.operacion == "dividendo" or not (start <= day <= end):
            continue
        ts = pd.Timestamp(day)
        y = float(value.get(ts, value.iloc[-1]))
        k = seen.get(day, 0)
        seen[day] = k + 1
        buy = m.operacion == "compra"
        ax1.scatter([ts], [y], marker="^" if buy else "v", s=70, color="#16a34a" if buy else "#dc2626",
                    edgecolor="white", linewidth=0.8, zorder=5)
        ax1.annotate(m.activo, (ts, y), xytext=(0, 9 + 10 * k), textcoords="offset points", ha="center",
                     fontsize=7.5, color="#374151")
    pnl, pct = last["pnl_eur"], last["pnl_pct"]
    sign = "+" if pnl >= 0 else "−"
    pct_txt = f" ({sign}{_eur(abs(pct), 1)} %)" if pct is not None else ""
    ax1.text(0.01, 0.97, f"Hoy: valen {_eur(last['valor_inversiones_eur'])} € · has puesto {_eur(last['coste_eur'])} €"
             f" · {sign}{_eur(abs(pnl), 2)} €{pct_txt}", transform=ax1.transAxes, va="top", fontsize=10.5,
             fontweight="bold", color="#15803d" if pnl >= 0 else "#b91c1c",
             bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#e5e7eb"})
    ax1.set_title("Tus inversiones: lo que has puesto frente a lo que valen (sin contar la liquidez)",
                  fontsize=12, loc="left")
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{_eur(v)} €"))
    lo, hi = min(value.min(), cost.min()), max(value.max(), cost.max())
    pad = max((hi - lo) * 0.12, hi * 0.03, 1)
    ax1.set_ylim(max(0, lo - pad), hi + pad * 2.2)
    ax1.grid(alpha=0.25)
    ax1.legend(loc="lower right", fontsize=8.5, framealpha=0.9)

    # Panel 2 — ganancia/pérdida acumulada (no depende de cuánto aportas)
    pnl_series = value - cost
    ax3.bar(x, pnl_series, width=0.8, color=["#16a34a" if v >= 0 else "#dc2626" for v in pnl_series])
    ax3.axhline(0, color="#374151", linewidth=0.8)
    ax3.set_title(f"Ganancia o pérdida acumulada cada día: hoy {sign}{_eur(abs(pnl), 2)} €",
                  fontsize=11, loc="left")
    ax3.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{_eur(v)} €"))
    ax3.grid(alpha=0.25, axis="y")

    # Panel 3 — reparto del patrimonio
    totals = [p["patrimonio_eur"] or 1 for p in points]
    layers, labels, colors = [], [], []
    for key, label, color in TYPE_LAYERS:
        series = [p["por_tipo_eur"].get(key, 0.0) / t * 100 for p, t in zip(points, totals)]
        if any(v > 0 for v in series):
            layers.append(series)
            labels.append(label)
            colors.append(color)
    if layers:
        ax2.stackplot(x, layers, labels=labels, colors=colors, alpha=0.9, step="post")
    inv_pct = 100 - last["por_tipo_eur"].get(CASH_TYPE, 0.0) / (last["patrimonio_eur"] or 1) * 100
    ax2.set_title(f"¿Cuánto de tu dinero está ya invertido? Hoy, el {_eur(inv_pct, 1)} % "
                  f"(el resto es liquidez)", fontsize=11, loc="left")
    if objetivo_liquidez_pct is not None:
        ax2.axhline(100 - objetivo_liquidez_pct, color="#111827", linestyle=":", linewidth=1.2)
        ax2.text(x[0], 100 - objetivo_liquidez_pct + 2, f"objetivo del plan: {_eur(100 - objetivo_liquidez_pct)} % "
                 "invertido", fontsize=8, color="#111827")
    ax2.set_ylim(0, 100)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f} %"))
    ax2.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8.5, frameon=False)
    ax2.grid(alpha=0.25, axis="y")
    ax2.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: (lambda d: f"{d.day} {_MONTHS[d.month - 1]}")(mdates.num2date(v))))
    fig.autofmt_xdate(rotation=0, ha="center")
    path = out_dir / CHART_FILE
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return str(path)

# ── Orquestación ─────────────────────────────────────────────────────────────

def _is_crypto_pair(symbol: str) -> bool:
    """BTC-EUR, PEPE-USD, ETH-USDC… (par de Coinbase)."""
    return re.fullmatch(r"[A-Z0-9]+-(EUR|USD|USDC|USDT|GBP)", symbol) is not None


async def _coinbase_price(session: aiohttp.ClientSession, product: str) -> float:
    async with session.get(COINBASE_TICKER_URL.format(product=product),
                           timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        return float((await resp.json(content_type=None))["price"])


async def fetch_prices(symbols: list[str]) -> tuple[dict[str, float], list[dict]]:
    """Último precio (cierre o vela en curso) de Yahoo Finance; para pares cripto que Yahoo
    no tenga (p. ej. PEPE-USD), precio actual de Coinbase."""
    prices: dict[str, float] = {}
    issues: list[dict] = []
    if not symbols:
        return prices, issues
    async with aiohttp.ClientSession(trust_env=True) as session:
        results = await asyncio.gather(
            *(fetch_history(session, s, range_="5d") for s in symbols), return_exceptions=True
        )
        retry = [s for s, r in zip(symbols, results) if isinstance(r, Exception) and _is_crypto_pair(s)]
        fallback = dict(zip(retry, await asyncio.gather(
            *(_coinbase_price(session, s) for s in retry), return_exceptions=True
        )))
    for sym, res in zip(symbols, results):
        if not isinstance(res, Exception):
            prices[sym] = float(f"{float(res['Close'].iloc[-1]):.8g}")
        elif sym in fallback and not isinstance(fallback[sym], Exception):
            prices[sym] = fallback[sym]
        else:
            extra = f" · Coinbase: {fallback[sym]}" if sym in fallback else ""
            issues.append({"activo": sym, "message": f"precio: {type(res).__name__}: {res}{extra}"})
    return prices, issues


def next_contribution(spec: dict | None, today: date) -> dict | None:
    """Próxima aportación periódica mensual (`portfolio.aportacion_mensual`): hoy cuenta si es el día."""
    if not spec or not spec.get("importe"):
        return None
    day = int(spec.get("dia", 1))

    def on(year: int, month: int) -> date:
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    nxt = on(today.year, today.month)
    if nxt < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        nxt = on(year, month)
    return {
        "importe": spec["importe"],
        "moneda": (spec.get("moneda") or "EUR").upper(),
        "dia": day,
        "proxima_fecha": nxt.isoformat(),
        "dias_hasta": (nxt - today).days,
    }


async def collect(cfg: dict, csv_path: Path, now: datetime | None = None, evolution: bool = False,
                  charts_dir: Path | None = None) -> dict:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    base = (cfg.get("portfolio") or {}).get("base_currency", "EUR")
    cap = (cfg.get("crypto") or {}).get("max_portfolio_pct")
    header = {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "file": str(csv_path),
        "aportacion_mensual": next_contribution((cfg.get("portfolio") or {}).get("aportacion_mensual"),
                                                now_local.date()),
    }
    if not csv_path.exists():
        return {**header, "exists": False, "issues": [{"message": "no existe el archivo de cartera"}]}

    movements, row_issues = read_movements(csv_path.read_text(encoding="utf-8-sig"))
    positions, pos_issues = build_positions(movements)
    open_pos = [p for p in positions.values() if p.cantidad > _EPS]
    currencies = sorted({p.moneda for p in positions.values()} - {base})
    fx_syms = [fx_symbol(c, base) for c in currencies]
    prices, price_issues = await fetch_prices([p.activo for p in open_pos if p.tipo != CASH_TYPE] + fx_syms)
    fx = {c: prices.pop(s) for c, s in zip(currencies, fx_syms) if s in prices}
    summary = summarize(positions, prices, fx, base=base, crypto_cap_pct=cap)
    data = {
        **header,
        "exists": True,
        "movements": len(movements),
        "last_movement": movements[-1].fecha.isoformat() if movements else None,
        **summary,
        "issues": row_issues + pos_issues + price_issues + summary["issues"],
    }
    if evolution and open_pos:
        data.update(await _evolution(cfg, movements, summary, currencies, fx, base, now, now_local.date(),
                                     charts_dir or DEFAULT_CHARTS_DIR / now_local.date().isoformat(), data["issues"]))
    return data


async def _evolution(cfg: dict, movements: list[Movement], summary: dict, currencies: list[str], fx: dict,
                     base: str, now: datetime, today: date, charts_dir: Path, issues: list[dict]) -> dict:
    """Serie diaria + resumen + gráfico (los fallos se anotan en `issues`, nunca detienen el informe)."""
    spec = (cfg.get("portfolio") or {}).get("grafico") or {}
    start = evolution_start(movements, spec.get("desde"))
    if start is None or start > today:
        return {}
    symbols = sorted({m.activo for m in movements if m.tipo != CASH_TYPE})
    hist, fx_hist, h_issues = await fetch_histories(symbols, currencies, start, now, base)
    points, e_issues = build_evolution(movements, hist, fx_hist, start, today, fx, base)
    points[-1] = today_point(summary, today)
    issues.extend(h_issues + e_issues)
    out = {"evolucion": points, "evolucion_resumen": evolution_summary(points)}
    try:
        out["chart"] = make_portfolio_chart(points, movements, charts_dir, spec.get("objetivo_liquidez_pct"))
    except Exception as exc:  # pragma: no cover - el gráfico nunca debe romper el informe
        issues.append({"message": f"gráfico de evolución: {type(exc).__name__}: {exc}"})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valora la cartera del usuario a partir de sus movimientos.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--csv", default=None, help="Por defecto, portfolio.file de config.yaml")
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-chart", action="store_true", help="Sin evolución diaria ni gráfico")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    csv_path = Path(args.csv) if args.csv else _HERE / (cfg.get("portfolio") or {}).get("file", "inputs/cartera.csv")
    data = asyncio.run(collect(cfg, csv_path, evolution=not args.no_chart))
    date_ = datetime.fromisoformat(data["generated_at_local"]).date().isoformat()
    out = Path(args.output) if args.output else DEFAULT_DATA_DIR / f"portfolio_{date_}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Cartera guardada en {out}")
    if not data["exists"]:
        print(f"  (sin archivo de cartera en {csv_path}: el informe dará recomendaciones genéricas)")
        return 0
    t, c = data["totals"], data["crypto"]
    print(f"  Movimientos: {data['movements']} | Posiciones abiertas: {len(data['positions'])}")
    print(f"  Valor: {t['valor_eur']} EUR | Coste: {t['coste_eur']} | G/P no realizada: {t['pnl_no_realizado_eur']}")
    if t.get("liquidez_eur"):
        print(f"  Liquidez: {t['liquidez_eur']} EUR")
    if data.get("aportacion_mensual"):
        a = data["aportacion_mensual"]
        print(f"  Próxima aportación: {a['importe']} {a['moneda']} el {a['proxima_fecha']} (en {a['dias_hasta']} días)")
    if data["positions"]:
        print(f"  Cripto: {c['peso_pct']}% (tope {c['tope_pct']}%)")
    else:
        print("  Sin posiciones abiertas (añade tus movimientos a cartera.csv)")
    if data.get("evolucion_resumen"):
        r = data["evolucion_resumen"]
        print(f"  Evolución desde {r['desde']} ({r['dias']} días): G/P {r['pnl_eur']} EUR "
              f"(1 día {r['pnl_cambio_1d_eur']}, 7 días {r['pnl_cambio_7d_eur']})")
    if data.get("chart"):
        print(f"  Gráfico de evolución: {data['chart']}")
    for issue in data["issues"]:
        print(f"  ⚠ {issue.get('activo') or issue.get('row') or ''}: {issue['message']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
