"""
Criptomonedas para el informe diario: **todas las monedas de Coinbase**, radar de mercado,
análisis técnico detallado y sentimiento.

Solo fuentes públicas, sin claves:
- Coinbase Exchange API (`/products`): universo = toda moneda con algún par operable (USD, EUR, USDC…).
- Coinpaprika (`/v1/tickers`): ranking, precio, capitalización y variaciones de las ~2.000 mayores monedas
  en una sola petición → datos de todo el universo de Coinbase. (`/v1/global`: capitalización y dominancia.)
- Coinbase Exchange API (`/products/{par}/candles`): velas diarias (~600 días) para el análisis técnico
  detallado (par en EUR si existe; si no, USD). Mismos indicadores que `fetch_prices.py`, calendario de 365 días.
- Coinbase API (`/v2/exchange-rates`): cambio USD → EUR.
- alternative.me: índice Fear & Greed (miedo/codicia) del mercado cripto.

Análisis técnico detallado para: `crypto.watchlist` (núcleo) + top `crypto.auto_top_n` por capitalización
(sin stablecoins) + monedas cripto de `inputs/cartera.csv` + las pedidas con `--charts`.
Si una fuente falla se anota en `issues` y el resto sigue (el informe nunca se bloquea por esto).

Uso:
    python claude_agent/fetch_crypto.py                       # → data/crypto_FECHA.json
    python claude_agent/fetch_crypto.py --charts BTC,PEPE      # + PNG en reports/charts/FECHA/ (cualquier moneda de Coinbase)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import pandas as pd

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
    make_chart,
    recent_bars,
)

COINBASE_PRODUCTS_URL = "https://api.exchange.coinbase.com/products"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
COINBASE_RATES_URL = "https://api.coinbase.com/v2/exchange-rates"
TICKERS_URL = "https://api.coinpaprika.com/v1/tickers"
FEAR_GREED_URL = "https://api.alternative.me/fng/"
GLOBAL_URL = "https://api.coinpaprika.com/v1/global"
DEFAULT_DATA_DIR = _HERE / "data"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TradingReport/1.0)"}
_TIMEOUT = 20
CANDLES_PER_PAGE = 300          # máximo de Coinbase por petición
CANDLE_PAGES = 2                # ~600 días: MM200 y máximo/mínimo de 52 semanas
MAX_CONCURRENCY = 5             # la API pública de Coinbase limita las peticiones por segundo
PREFERRED_QUOTES = ("EUR", "USD", "USDC")
STABLECOINS = {
    "USDT", "USDC", "DAI", "PYUSD", "EURC", "USDS", "FDUSD", "TUSD", "GUSD", "USDP", "RLUSD", "USDG",
    "USD1", "EURT", "LUSD", "FRAX", "GHO", "USDE", "SUSD", "CRVUSD", "BUSD", "UST", "USTC",
}

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


def coin_code(symbol: str) -> str:
    """BTC-EUR → BTC · btc → BTC."""
    return symbol.strip().split("-")[0].upper()


# ── Coinbase ─────────────────────────────────────────────────────────────────

def parse_coinbase_products(products: list[dict]) -> dict[str, list[str]]:
    """{moneda: [cotizaciones operables]} para todo par online, operable y no cancel-only."""
    out: dict[str, set[str]] = {}
    for p in products or []:
        base, quote = p.get("base_currency"), p.get("quote_currency")
        if (base and quote and p.get("status") == "online"
                and not p.get("trading_disabled") and not p.get("cancel_only")):
            out.setdefault(base.upper(), set()).add(quote.upper())
    return {b: sorted(q) for b, q in sorted(out.items())}


def pick_quote(quotes: list[str] | None) -> str | None:
    """Cotización preferida para las velas: EUR > USD > USDC > la primera disponible."""
    if not quotes:
        return None
    for q in PREFERRED_QUOTES:
        if q in quotes:
            return q
    return quotes[0]


def parse_coinbase_candles(pages: list[list]) -> pd.DataFrame:
    """Velas de Coinbase ([time, low, high, open, close, volume], más reciente primero) → OHLCV ascendente."""
    rows = [r for page in pages for r in (page or []) if isinstance(r, list) and len(r) >= 6]
    if not rows:
        raise ValueError("Coinbase: sin velas")
    df = pd.DataFrame(rows, columns=["t", "Low", "High", "Open", "Close", "Volume"]).drop_duplicates("t")
    df.index = pd.to_datetime(df.pop("t"), unit="s", utc=True)
    df.index.name = None
    return df.sort_index()[["Open", "High", "Low", "Close", "Volume"]].astype(float)


async def fetch_coinbase_history(session: aiohttp.ClientSession, product: str, now: datetime,
                                 pages: int = CANDLE_PAGES) -> pd.DataFrame:
    """Velas diarias de los últimos `pages`×300 días (una petición por página)."""
    results = []
    end = now
    for _ in range(pages):
        start = end - timedelta(days=CANDLES_PER_PAGE)
        results.append(await _get_json(session, COINBASE_CANDLES_URL.format(product=product), params={
            "granularity": 86400, "start": start.isoformat(), "end": end.isoformat(),
        }))
        end = start
    return parse_coinbase_candles(results)


def parse_usd_eur(payload: dict) -> float:
    rate = float(((payload or {}).get("data") or {}).get("rates", {}).get("EUR"))
    if rate <= 0:
        raise ValueError("cambio USD→EUR no válido")
    return rate


# ── Coinpaprika: universo y mercado global ──────────────────────────────────

def parse_tickers(tickers: list[dict]) -> dict[str, dict]:
    """{símbolo: datos} quedándose, ante símbolos repetidos, con la moneda de mejor ranking."""
    best: dict[str, dict] = {}
    for t in tickers or []:
        sym = (t.get("symbol") or "").upper()
        rank = t.get("rank") or None
        usd = (t.get("quotes") or {}).get("USD") or {}
        if not sym or not usd:
            continue
        current = best.get(sym)
        if current is None or (rank is not None and (current["rank"] is None or rank < current["rank"])):
            best[sym] = {
                "name": t.get("name"),
                "rank": rank,
                "price_usd": usd.get("price"),
                "market_cap_usd": usd.get("market_cap"),
                "volume_24h_usd": usd.get("volume_24h"),
                "change_pct": {k: usd.get(f"percent_change_{k}") for k in ("24h", "7d", "30d", "1y")},
                "dist_from_ath_pct": usd.get("percent_from_price_ath"),
            }
    return best


def is_stablecoin(symbol: str, data: dict | None) -> bool:
    if symbol in STABLECOINS:
        return True
    if not data or data.get("price_usd") is None:
        return False
    name = (data.get("name") or "").lower()
    return abs(data["price_usd"] - 1) < 0.03 and "usd" in name


def _r(value, nd: int = 2):
    return None if value is None else round(float(value), nd)


def _price(value):
    """Precio con 6 cifras significativas (no pierde precisión en monedas de 0,0000035 $)."""
    return None if value is None else float(f"{float(value):.6g}")


def build_universe(coinbase: dict[str, list[str]], tickers: dict[str, dict],
                   usd_eur: float | None) -> tuple[list[dict], list[str]]:
    """Todas las monedas de Coinbase con sus datos de mercado (ordenadas por ranking) + las que no tienen datos."""
    rows, unmatched = [], []
    for sym, quotes in coinbase.items():
        data = tickers.get(sym)
        if data is None:
            unmatched.append(sym)
            continue
        price = data["price_usd"]
        rows.append({
            "symbol": sym,
            "name": data["name"],
            "rank": data["rank"],
            "coinbase_quotes": quotes,
            "price_usd": _price(price),
            "price_eur": _price(price * usd_eur) if price is not None and usd_eur else None,
            "market_cap_usd": data["market_cap_usd"],
            "volume_24h_usd": _r(data["volume_24h_usd"], 0),
            "change_pct": {k: _r(v) for k, v in data["change_pct"].items()},
            "dist_from_ath_pct": _r(data["dist_from_ath_pct"]),
            "stablecoin": is_stablecoin(sym, data),
        })
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0))
    return rows, unmatched


def build_screener(universe: list[dict], top: int = 20, movers: int = 5, movers_rank: int = 100) -> dict:
    """Radar: mayores por capitalización, mayores subidas/bajadas (solo top `movers_rank`) y amplitud."""
    coins = [r for r in universe if not r["stablecoin"]]
    liquid = [r for r in coins if r["rank"] is not None and r["rank"] <= movers_rank]

    def brief(r: dict) -> dict:
        return {"symbol": r["symbol"], "name": r["name"], "rank": r["rank"], "price_eur": r["price_eur"],
                "price_usd": r["price_usd"], "change_pct": r["change_pct"]}

    def ranked(period: str, reverse: bool) -> list[dict]:
        valid = [r for r in liquid if r["change_pct"].get(period) is not None]
        return [brief(r) for r in sorted(valid, key=lambda r: r["change_pct"][period], reverse=reverse)[:movers]]

    def breadth(period: str) -> dict:
        vals = [r["change_pct"].get(period) for r in coins if r["change_pct"].get(period) is not None]
        return {"up": sum(v > 0 for v in vals), "down": sum(v < 0 for v in vals), "total": len(vals)}

    return {
        "top_market_cap": [brief(r) for r in coins[:top]],
        "movers_universe": f"top {movers_rank} por capitalización (sin stablecoins)",
        "gainers_7d": ranked("7d", True),
        "losers_7d": ranked("7d", False),
        "gainers_30d": ranked("30d", True),
        "losers_30d": ranked("30d", False),
        "breadth_24h": breadth("24h"),
        "breadth_7d": breadth("7d"),
    }


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

    history = [item(d) for d in data]
    return {
        **history[0],
        "yesterday": history[1]["value"] if len(history) > 1 else None,
        "week_ago": history[7]["value"] if len(history) > 7 else None,
    }


# ── Selección del análisis detallado ─────────────────────────────────────────

def portfolio_crypto_codes(cfg: dict) -> list[str]:
    """Monedas cripto con posición en `inputs/cartera.csv` (si existe)."""
    from claude_agent.portfolio import build_positions, read_movements

    path = _HERE / (cfg.get("portfolio") or {}).get("file", "inputs/cartera.csv")
    if not path.exists():
        return []
    movements, _ = read_movements(path.read_text(encoding="utf-8-sig"))
    positions, _ = build_positions(movements)
    return sorted({coin_code(p.activo) for p in positions.values() if p.tipo == "cripto" and p.cantidad > 0})


def select_detailed(cfg: dict, universe: list[dict], extra: list[str]) -> list[tuple[str, list[str]]]:
    """(código, motivos) sin duplicados, en orden: núcleo → cartera → top N → pedidas."""
    crypto_cfg = cfg.get("crypto") or {}
    top_n = int(crypto_cfg.get("auto_top_n", 15))
    chosen: dict[str, list[str]] = {}

    def add(code: str, reason: str) -> None:
        chosen.setdefault(coin_code(code), [])
        if reason not in chosen[coin_code(code)]:
            chosen[coin_code(code)].append(reason)

    for sym, _, _ in _crypto_watchlist_from_config(cfg):
        add(sym, "núcleo")
    for code in portfolio_crypto_codes(cfg):
        add(code, "cartera")
    for r in [r for r in universe if not r["stablecoin"]][:top_n]:
        add(r["symbol"], f"top {top_n}")
    for code in extra:
        add(code, "pedida")
    return list(chosen.items())


# ── Orquestación ─────────────────────────────────────────────────────────────

async def collect(cfg: dict, now: datetime | None = None, chart_symbols: set[str] | None = None,
                  charts_dir: Path | None = None) -> dict:
    crypto_cfg = cfg.get("crypto") or {}
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    chart_codes = {coin_code(s) for s in (chart_symbols or set())}
    charts_dir = charts_dir or (DEFAULT_CHARTS_DIR / now_local.date().isoformat())
    names = {coin_code(s): n for s, n, _ in _crypto_watchlist_from_config(cfg)}
    issues: list[dict] = []

    def failed(source: str, exc: Exception) -> None:
        issues.append({"source": source, "message": f"{type(exc).__name__}: {exc}"})

    async with aiohttp.ClientSession(trust_env=True) as session:
        products, tickers, rates, fng, glob = await asyncio.gather(
            _get_json(session, COINBASE_PRODUCTS_URL),
            _get_json(session, TICKERS_URL),
            _get_json(session, COINBASE_RATES_URL, params={"currency": "USD"}),
            _get_json(session, FEAR_GREED_URL, params={"limit": 8}),
            _get_json(session, GLOBAL_URL),
            return_exceptions=True,
        )

        def parsed(source: str, res, parser):
            if isinstance(res, Exception):
                failed(source, res)
                return None
            try:
                return parser(res)
            except (ValueError, KeyError, TypeError) as exc:
                failed(source, exc)
                return None

        coinbase = parsed("coinbase", products, parse_coinbase_products) or None
        if products is not None and not isinstance(products, Exception) and not coinbase:
            issues.append({"source": "coinbase", "message": "sin pares operables"})
        ticker_data = parsed("coinpaprika_tickers", tickers, parse_tickers) or {}
        usd_eur = parsed("coinbase_rates", rates, parse_usd_eur)
        fear_greed = parsed("fear_greed", fng, parse_fear_greed)
        market = parsed("coinpaprika", glob, parse_global)

        universe, unmatched = build_universe(coinbase or {}, ticker_data, usd_eur)
        by_symbol = {r["symbol"]: r for r in universe}
        detailed = select_detailed(cfg, universe, sorted(chart_codes))

        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def history(code: str):
            # Sin lista de Coinbase se prueba EUR y luego USD
            quotes = (coinbase or {}).get(code) if coinbase else list(PREFERRED_QUOTES[:2])
            if coinbase and not quotes:
                raise LookupError("no se puede comprar en Coinbase")
            errors = []
            for quote in ([pick_quote(quotes)] if coinbase else quotes):
                async with sem:
                    try:
                        return quote, await fetch_coinbase_history(session, f"{code}-{quote}", now)
                    except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        errors.append(f"{code}-{quote}: {exc}")
            raise ValueError("; ".join(errors))

        histories = await asyncio.gather(*(history(code) for code, _ in detailed), return_exceptions=True)

    symbols: dict[str, dict] = {}
    for (code, reasons), res in zip(detailed, histories):
        if isinstance(res, Exception):
            issues.append({"symbol": code, "message": f"{type(res).__name__}: {res}"})
            continue
        quote, df = res
        info = by_symbol.get(code) or {}
        entry = {
            "name": info.get("name") or names.get(code, code),
            "rank": info.get("rank"),
            "reasons": reasons,
            "product": f"{code}-{quote}",
            "currency": quote,
            "coinbase_quotes": (coinbase or {}).get(code),
            "coinbase_available": None if coinbase is None else True,
            **compute_indicators(df, calendar="cripto"),
            "recent_bars": recent_bars(df),
        }
        entry["price_eur"] = entry["price"] if quote == "EUR" else (
            _price(entry["price"] * usd_eur) if quote in ("USD", "USDC") and usd_eur else None)
        if code in chart_codes:
            try:
                entry["chart"] = make_chart(f"{code}-{quote}", entry["name"], df, charts_dir)
            except Exception as exc:
                issues.append({"symbol": code, "message": f"chart: {exc}"})
        symbols[code] = entry

    return {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "source": "Coinbase (universo, velas diarias, cambio) · Coinpaprika · alternative.me",
        "exchange": crypto_cfg.get("exchange", "coinbase"),
        "max_portfolio_pct": crypto_cfg.get("max_portfolio_pct"),
        "usd_eur": usd_eur,
        "coinbase_count": len(coinbase) if coinbase else None,
        "fear_greed": fear_greed,
        "market": market,
        "screener": build_screener(universe, top=int(crypto_cfg.get("screener_top", 20))) if universe else None,
        "symbols": symbols,
        "universe": universe,
        "universe_without_data": unmatched,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Criptomonedas de Coinbase: radar, indicadores y sentimiento.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=None)
    parser.add_argument("--charts", default="", help="Monedas separadas por comas (BTC,PEPE o BTC-EUR) para PNG")
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
    print(f"  Coinbase: {data['coinbase_count']} monedas | con datos de mercado: {len(data['universe'])} | "
          f"análisis detallado: {len(data['symbols'])} | incidencias: {len(data['issues'])}")
    if fng:
        print(f"  Fear & Greed: {fng['value']} ({fng['clasificacion']})")
    for code, d in data["symbols"].items():
        line = (f"  {code:8} #{d['rank'] or '-':<4} {d['price']:>12} {d['currency']} {d['change_pct']['1d']}% "
                f"tendencia={d['trend']} RSI={d['rsi14']} ({', '.join(d['reasons'])})")
        if "chart" in d:
            line += f" gráfico={d['chart']}"
        print(line)
    for issue in data["issues"]:
        print(f"  ⚠ {issue.get('symbol') or issue.get('source')}: {issue['message']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
