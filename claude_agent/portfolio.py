"""
Cartera del usuario a partir de sus movimientos (`claude_agent/inputs/cartera.csv`).

El usuario edita el CSV como una tabla (Excel, LibreOffice, Numbers, GitHub…). Este script
reconstruye las posiciones abiertas con el método de **precio medio ponderado**, obtiene el
último precio público de Yahoo Finance, convierte a EUR y calcula pesos, ganancias/pérdidas
y la exposición a criptomonedas frente al tope de `config.yaml` (`crypto.max_portfolio_pct`).

Columnas del CSV (cabecera obligatoria, separador `,` o `;`, decimales con `.` o `,`):
    fecha,activo,nombre,tipo,operacion,cantidad,precio,moneda,comision,broker,notas
- activo:    ticker de Yahoo Finance (SAN.MC, AAPL, SPY) o par de Coinbase para cripto (BTC-EUR, PEPE-USD…)
- tipo:      accion | etf | fondo | cripto | otro
- operacion: compra | venta | dividendo   (dividendo: `precio` = importe neto cobrado; `cantidad` se ignora)
- precio:    precio por unidad en `moneda`; comision: gastos totales de la operación en `moneda`

Simplificación: la ganancia en EUR de un activo en divisa se calcula con el tipo de cambio actual
(no se guarda el cambio histórico de cada compra).

Uso:
    python claude_agent/portfolio.py            # → data/portfolio_FECHA.json
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
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_HERE.parent))

from claude_agent.fetch_news import DEFAULT_CONFIG, load_config  # noqa: E402
from claude_agent.fetch_prices import fetch_history  # noqa: E402

DEFAULT_DATA_DIR = _HERE / "data"
COINBASE_TICKER_URL = "https://api.exchange.coinbase.com/products/{product}/ticker"
COLUMNS = ["fecha", "activo", "nombre", "tipo", "operacion", "cantidad", "precio", "moneda",
           "comision", "broker", "notas"]
REQUIRED = ("fecha", "activo", "operacion", "precio")
OPERATIONS = ("compra", "venta", "dividendo")
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
        price = prices.get(pos.activo)
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
    cost = sum(r["coste_eur"] or 0 for r in rows)
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
            "valor_eur": round(total, 2),
            "coste_eur": round(cost, 2),
            "pnl_no_realizado_eur": round(total - cost, 2),
            "pnl_no_realizado_pct": round((total / cost - 1) * 100, 2) if cost else None,
            "pnl_realizado_eur": round(realized_base, 2),
        },
        "por_tipo_pct": {t: round(v / total * 100, 2) for t, v in by_type.items()} if total else {},
        "crypto": crypto,
        "positions": rows,
        "closed_positions": closed,
        "fx": fx,
        "issues": issues,
    }


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


async def collect(cfg: dict, csv_path: Path, now: datetime | None = None) -> dict:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    base = (cfg.get("portfolio") or {}).get("base_currency", "EUR")
    cap = (cfg.get("crypto") or {}).get("max_portfolio_pct")
    header = {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "file": str(csv_path),
    }
    if not csv_path.exists():
        return {**header, "exists": False, "issues": [{"message": "no existe el archivo de cartera"}]}

    movements, row_issues = read_movements(csv_path.read_text(encoding="utf-8-sig"))
    positions, pos_issues = build_positions(movements)
    open_pos = [p for p in positions.values() if p.cantidad > _EPS]
    currencies = sorted({p.moneda for p in positions.values()} - {base})
    fx_syms = [fx_symbol(c, base) for c in currencies]
    prices, price_issues = await fetch_prices([p.activo for p in open_pos] + fx_syms)
    fx = {c: prices.pop(s) for c, s in zip(currencies, fx_syms) if s in prices}
    summary = summarize(positions, prices, fx, base=base, crypto_cap_pct=cap)
    return {
        **header,
        "exists": True,
        "movements": len(movements),
        "last_movement": movements[-1].fecha.isoformat() if movements else None,
        **summary,
        "issues": row_issues + pos_issues + price_issues + summary["issues"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valora la cartera del usuario a partir de sus movimientos.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--csv", default=None, help="Por defecto, portfolio.file de config.yaml")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    csv_path = Path(args.csv) if args.csv else _HERE / (cfg.get("portfolio") or {}).get("file", "inputs/cartera.csv")
    data = asyncio.run(collect(cfg, csv_path))
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
    if data["positions"]:
        print(f"  Cripto: {c['peso_pct']}% (tope {c['tope_pct']}%)")
    else:
        print("  Sin posiciones abiertas (añade tus movimientos a cartera.csv)")
    for issue in data["issues"]:
        print(f"  ⚠ {issue.get('activo') or issue.get('row') or ''}: {issue['message']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
