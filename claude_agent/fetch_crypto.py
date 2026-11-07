"""
Criptomonedas para el informe diario: precios, indicadores, disponibilidad en Coinbase y sentimiento.

Solo fuentes públicas, sin claves:
- Coinbase Exchange API (`/products`): qué monedas se pueden comprar en el exchange del usuario (par EUR).
- Yahoo Finance: 2 años de velas diarias en EUR → mismos indicadores que `fetch_prices.py`
  (calendario de 365 días: las criptomonedas cotizan todos los días).
- alternative.me: índice Fear & Greed (miedo/codicia) del mercado cripto.
- Coinpaprika: capitalización total del mercado y dominancia de Bitcoin.

Si una fuente falla se anota en `issues` y el resto sigue (el informe nunca se bloquea por esto).

Uso:
    python claude_agent/fetch_crypto.py                         # → data/crypto_FECHA.json
    python claude_agent/fetch_crypto.py --charts BTC-EUR,ETH-EUR   # + PNG en reports/charts/FECHA/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_HERE.parent))

from claude_agent.fetch_news import (  # noqa: E402
    DEFAULT_CONFIG,
    _crypto_watchlist_from_config,
    load_config,
)
from claude_agent.fetch_prices import (  # noqa: E402
    DEFAULT_CHARTS_DIR,
    compute_indicators,
    fetch_history,
    make_chart,
    recent_bars,
)

COINBASE_PRODUCTS_URL = "https://api.exchange.coinbase.com/products"
FEAR_GREED_URL = "https://api.alternative.me/fng/"
GLOBAL_URL = "https://api.coinpaprika.com/v1/global"
DEFAULT_DATA_DIR = _HERE / "data"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TradingReport/1.0)"}
_TIMEOUT = 15

_FNG_ES = {
    "Extreme Fear": "miedo extremo",
    "Fear": "miedo",
    "Neutral": "neutral",
    "Greed": "codicia",
    "Extreme Greed": "codicia extrema",
}


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict | None = None):
    async with session.get(
        url, params=params, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
    ) as resp:
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        return await resp.json(content_type=None)


# ── Coinbase ─────────────────────────────────────────────────────────────────

def parse_coinbase_products(products: list[dict], quote: str = "EUR") -> list[str]:
    """Monedas base con par `quote` activo y operable en Coinbase (ordenadas)."""
    bases = {
        p.get("base_currency")
        for p in products or []
        if p.get("quote_currency") == quote
        and p.get("status") == "online"
        and not p.get("trading_disabled")
        and not p.get("cancel_only")
        and p.get("base_currency")
    }
    return sorted(bases)


def base_currency(symbol: str) -> str:
    """BTC-EUR → BTC."""
    return symbol.split("-")[0].upper()


# ── Sentimiento y mercado global ────────────────────────────────────────────

def parse_fear_greed(payload: dict) -> dict:
    data = (payload or {}).get("data") or []
    if not data:
        raise ValueError("Fear & Greed: respuesta sin datos")

    def item(d: dict) -> dict:
        label = d.get("value_classification")
        return {
            "value": int(d["value"]),
            "classification": label,
            "clasificacion": _FNG_ES.get(label, label),
            "date": datetime.fromtimestamp(int(d["timestamp"]), tz=timezone.utc).date().isoformat(),
        }

    now = item(data[0])
    history = [item(d) for d in data]
    week_ago = history[7]["value"] if len(history) > 7 else None
    return {**now, "yesterday": history[1]["value"] if len(history) > 1 else None, "week_ago": week_ago}


def parse_global(payload: dict) -> dict:
    if not payload or "market_cap_usd" not in payload:
        raise ValueError("Coinpaprika: respuesta sin datos")
    ath = payload.get("market_cap_ath_value")
    cap = payload["market_cap_usd"]
    return {
        "market_cap_usd": cap,
        "market_cap_change_24h_pct": payload.get("market_cap_change_24h"),
        "volume_24h_usd": payload.get("volume_24h_usd"),
        "bitcoin_dominance_pct": payload.get("bitcoin_dominance_percentage"),
        "market_cap_ath_usd": ath,
        "market_cap_ath_date": (payload.get("market_cap_ath_date") or "")[:10] or None,
        "dist_from_ath_pct": round((cap / ath - 1) * 100, 2) if ath else None,
    }


# ── Orquestación ─────────────────────────────────────────────────────────────

async def collect(cfg: dict, now: datetime | None = None, chart_symbols: set[str] | None = None,
                  charts_dir: Path | None = None) -> dict:
    crypto_cfg = cfg.get("crypto") or {}
    quote = crypto_cfg.get("quote_currency", "EUR")
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    watchlist = _crypto_watchlist_from_config(cfg)
    chart_symbols = chart_symbols or set()
    charts_dir = charts_dir or (DEFAULT_CHARTS_DIR / now_local.date().isoformat())
    issues: list[dict] = []

    async with aiohttp.ClientSession(trust_env=True) as session:
        products, fng, glob, *histories = await asyncio.gather(
            _get_json(session, COINBASE_PRODUCTS_URL),
            _get_json(session, FEAR_GREED_URL, params={"limit": 8}),
            _get_json(session, GLOBAL_URL),
            *(fetch_history(session, sym) for sym, _, _ in watchlist),
            return_exceptions=True,
        )

    available: list[str] | None = None
    if isinstance(products, Exception):
        issues.append({"source": "coinbase", "message": f"{type(products).__name__}: {products}"})
    else:
        available = parse_coinbase_products(products, quote)
        if not available:
            issues.append({"source": "coinbase", "message": f"sin pares {quote} operables"})
            available = None

    def parse_or_issue(name: str, res, parser):
        if isinstance(res, Exception):
            issues.append({"source": name, "message": f"{type(res).__name__}: {res}"})
            return None
        try:
            return parser(res)
        except (ValueError, KeyError, TypeError) as exc:
            issues.append({"source": name, "message": f"{type(exc).__name__}: {exc}"})
            return None

    fear_greed = parse_or_issue("fear_greed", fng, parse_fear_greed)
    market = parse_or_issue("coinpaprika", glob, parse_global)

    symbols: dict[str, dict] = {}
    for (sym, name, _), res in zip(watchlist, histories):
        if isinstance(res, Exception):
            issues.append({"symbol": sym, "message": f"{type(res).__name__}: {res}"})
            continue
        on_exchange = None if available is None else base_currency(sym) in available
        entry = {
            "name": name,
            "currency": quote,
            "coinbase_available": on_exchange,
            **compute_indicators(res, calendar="cripto"),
            "recent_bars": recent_bars(res),
        }
        if sym in chart_symbols:
            try:
                entry["chart"] = make_chart(sym, name, res, charts_dir)
            except Exception as exc:
                issues.append({"symbol": sym, "message": f"chart: {exc}"})
        symbols[sym] = entry

    for sym in sorted(chart_symbols - {s for s, _, _ in watchlist}):
        issues.append({"symbol": sym, "message": "no está en la watchlist cripto de config.yaml"})

    return {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "source": "Yahoo Finance (velas diarias en EUR, 2 años) · Coinbase · alternative.me · Coinpaprika",
        "exchange": crypto_cfg.get("exchange", "coinbase"),
        "quote_currency": quote,
        "max_portfolio_pct": crypto_cfg.get("max_portfolio_pct"),
        "coinbase_available": available,
        "fear_greed": fear_greed,
        "market": market,
        "symbols": symbols,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Criptomonedas: precios, indicadores y sentimiento.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=None)
    parser.add_argument("--charts", default="", help="Símbolos separados por comas para generar PNG")
    parser.add_argument("--charts-dir", default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    chart_symbols = {s.strip() for s in args.charts.split(",") if s.strip()}
    data = asyncio.run(collect(
        cfg,
        chart_symbols=chart_symbols,
        charts_dir=Path(args.charts_dir) if args.charts_dir else None,
    ))
    date = datetime.fromisoformat(data["generated_at_local"]).date().isoformat()
    out = Path(args.output) if args.output else DEFAULT_DATA_DIR / f"crypto_{date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    fng = data["fear_greed"]
    print(f"Cripto guardado en {out}")
    print(f"  Monedas OK: {len(data['symbols'])} | Incidencias: {len(data['issues'])}")
    if fng:
        print(f"  Fear & Greed: {fng['value']} ({fng['clasificacion']})")
    for sym, d in data["symbols"].items():
        flag = {True: "Coinbase ✓", False: "NO en Coinbase", None: "Coinbase ?"}[d["coinbase_available"]]
        line = f"  {sym:10} {d['price']:>10} {d['change_pct']['1d']}% tendencia={d['trend']} RSI={d['rsi14']} {flag}"
        if "chart" in d:
            line += f" gráfico={d['chart']}"
        print(line)
    for issue in data["issues"]:
        print(f"  ⚠ {issue.get('symbol') or issue.get('source')}: {issue['message']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
