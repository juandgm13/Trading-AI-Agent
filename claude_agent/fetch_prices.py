"""
Precios históricos + indicadores técnicos para el informe diario pre-apertura.

Descarga 2 años de velas diarias públicas de Yahoo Finance (sin credenciales) para la watchlist de
`config.yaml` y calcula indicadores de forma determinista (pandas), para que el informe use
cifras reales y no estimaciones del modelo.

Uso:
    python claude_agent/fetch_prices.py                          # → data/prices_FECHA.json
    python claude_agent/fetch_prices.py --charts SAN.MC,AAPL     # + PNG en docs/informes/charts/FECHA/
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
import pandas as pd

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_HERE.parent))

from claude_agent.fetch_news import DEFAULT_CONFIG, _watchlist_from_config, load_config  # noqa: E402

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
DEFAULT_DATA_DIR = _HERE / "data"
# Los gráficos se guardan directamente en la web (GitHub Pages sirve docs/): sin duplicar PNG en el repo
DEFAULT_CHARTS_DIR = _HERE.parent / "docs" / "informes" / "charts"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TradingReport/1.0)"}
_TIMEOUT = 15
RECENT_BARS = 60
# Nº de velas por periodo: la bolsa cotiza ~252 días/año; las criptomonedas, los 365.
CALENDARS = {
    "bolsa": {"1w": 5, "1m": 21, "3m": 63, "1y": 252},
    "cripto": {"1w": 7, "1m": 30, "3m": 90, "1y": 365},
}


# ── Descarga ─────────────────────────────────────────────────────────────────

def parse_yahoo_chart(payload: dict) -> pd.DataFrame:
    """Convierte la respuesta de /v8/finance/chart en DataFrame OHLCV (índice fecha UTC)."""
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        raise ValueError(f"Yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError("Yahoo: respuesta sin datos")
    res = results[0]
    timestamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    df = pd.DataFrame(
        {
            "Open": quote.get("open"),
            "High": quote.get("high"),
            "Low": quote.get("low"),
            "Close": quote.get("close"),
            "Volume": quote.get("volume"),
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True),
    )
    df = df.dropna(subset=["Close"])
    if df.empty:
        raise ValueError("Yahoo: sin cierres válidos")
    df["Volume"] = df["Volume"].fillna(0)
    return df


async def fetch_history(
    session: aiohttp.ClientSession, symbol: str, range_: str = "2y", interval: str = "1d"
) -> pd.DataFrame:
    async with session.get(
        CHART_URL.format(symbol=symbol),
        params={"range": range_, "interval": interval},
        headers=_HEADERS,
        timeout=aiohttp.ClientTimeout(total=_TIMEOUT),
    ) as resp:
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        payload = await resp.json(content_type=None)
    return parse_yahoo_chart(payload)


# ── Indicadores ──────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    out[(loss == 0) & gain.notna()] = 100.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def pct_change(close: pd.Series, bars: int) -> float | None:
    if len(close) <= bars:
        return None
    prev = close.iloc[-1 - bars]
    return round((close.iloc[-1] / prev - 1) * 100, 2) if prev else None


def swing_levels(df: pd.DataFrame, window: int = 5, lookback: int = 120, n: int = 3):
    """Soportes (mínimos locales bajo el precio) y resistencias (máximos locales sobre él)."""
    recent = df.tail(lookback)
    price = float(df["Close"].iloc[-1])
    lows, highs = recent["Low"], recent["High"]
    span = 2 * window + 1
    is_low = lows == lows.rolling(span, center=True).min()
    is_high = highs == highs.rolling(span, center=True).max()
    supports = sorted({round(float(v), 2) for v in lows[is_low] if v < price}, reverse=True)[:n]
    resistances = sorted({round(float(v), 2) for v in highs[is_high] if v > price})[:n]
    return supports, resistances


def classify_trend(price: float, sma50: float | None, sma200: float | None, sma50_prev: float | None) -> str:
    """Regla simple para medio/largo plazo: alcista / bajista / lateral."""
    if sma50 is None or sma200 is None:
        return "datos_insuficientes"
    rising = sma50_prev is not None and sma50 > sma50_prev
    falling = sma50_prev is not None and sma50 < sma50_prev
    if price > sma50 > sma200 and rising:
        return "alcista"
    if price < sma50 < sma200 and falling:
        return "bajista"
    return "lateral"


def rsi_zone(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 70:
        return "sobrecompra"
    if value <= 30:
        return "sobreventa"
    return "neutral"


def _r(value, nd: int = 2):
    """Redondea a `nd` decimales; por debajo de 1 conserva 4 cifras significativas (cripto como PEPE ≈ 0,0000035)."""
    if value is None or pd.isna(value):
        return None
    value = float(value)
    if value != 0 and abs(value) < 1:
        return float(f"{value:.{max(nd, 4)}g}")
    return round(value, nd)


def compute_indicators(df: pd.DataFrame, calendar: str = "bolsa") -> dict:
    periods = CALENDARS[calendar]
    year_bars = periods["1y"]
    close = df["Close"]
    price = float(close.iloc[-1])
    sma = {n: close.rolling(n).mean() for n in (20, 50, 200)}
    last = {n: _r(s.iloc[-1]) for n, s in sma.items()}
    sma50_prev = _r(sma[50].iloc[-11]) if len(close) > 60 else None

    cross = None
    if len(close) >= 201:
        diff = (sma[50] - sma[200]).dropna().tail(20)
        if len(diff) >= 2 and diff.iloc[0] < 0 < diff.iloc[-1]:
            cross = "cruce_dorado"
        elif len(diff) >= 2 and diff.iloc[0] > 0 > diff.iloc[-1]:
            cross = "cruce_de_la_muerte"

    rsi_val = _r(rsi(close).iloc[-1], 1)
    m_line, m_sig, m_hist = macd(close)
    hist_now = _r(m_hist.iloc[-1], 3)
    hist_prev = _r(m_hist.iloc[-2], 3) if len(m_hist) > 1 else None

    returns = close.pct_change().dropna()
    vol20 = _r(returns.tail(20).std() * (year_bars ** 0.5) * 100, 1) if len(returns) >= 20 else None
    year = df.tail(year_bars)
    hi52, lo52 = float(year["High"].max()), float(year["Low"].min())
    vol_avg20 = float(df["Volume"].tail(20).mean())
    supports, resistances = swing_levels(df)

    def vs(level):
        return _r((price / level - 1) * 100) if level else None

    return {
        "last_date": df.index[-1].date().isoformat(),
        "price": _r(price),
        "change_pct": {
            "1d": pct_change(close, 1),
            "1w": pct_change(close, periods["1w"]),
            "1m": pct_change(close, periods["1m"]),
            "3m": pct_change(close, periods["3m"]),
            "1y": pct_change(close, year_bars) if len(close) > year_bars
            else pct_change(close, len(close) - 1),
        },
        "sma": {"20": last[20], "50": last[50], "200": last[200]},
        "price_vs_sma_pct": {"50": vs(last[50]), "200": vs(last[200])},
        "trend": classify_trend(price, last[50], last[200], sma50_prev),
        "sma_cross_last_20d": cross,
        "rsi14": rsi_val,
        "rsi_zone": rsi_zone(rsi_val),
        "macd": {
            "line": _r(m_line.iloc[-1], 3),
            "signal": _r(m_sig.iloc[-1], 3),
            "histogram": hist_now,
            "momentum": None if hist_now is None or hist_prev is None
            else ("mejorando" if hist_now > hist_prev else "empeorando"),
        },
        "volatility_20d_annual_pct": vol20,
        "high_52w": _r(hi52),
        "low_52w": _r(lo52),
        "dist_from_high_52w_pct": vs(hi52),
        "dist_from_low_52w_pct": vs(lo52),
        "supports": supports,
        "resistances": resistances,
        "volume_vs_avg20": _r(df["Volume"].iloc[-1] / vol_avg20) if vol_avg20 else None,
    }


def recent_bars(df: pd.DataFrame, n: int = RECENT_BARS) -> list[list]:
    """Últimas n velas compactas: [fecha, open, high, low, close, volume]."""
    return [
        [idx.date().isoformat(), _r(r.Open), _r(r.High), _r(r.Low), _r(r.Close), int(r.Volume)]
        for idx, r in df.tail(n).iterrows()
    ]


# ── Gráficos ─────────────────────────────────────────────────────────────────

def make_chart(symbol: str, name: str, df: pd.DataFrame, out_dir: Path, bars: int = 180) -> str:
    """Velas diarias con MM50/MM200 y volumen (estilo de src/chart_generator.py)."""
    import logging

    import matplotlib

    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import mplfinance as mpf

    out_dir.mkdir(parents=True, exist_ok=True)
    close = df["Close"]
    plot_df = df.tail(bars)
    addplots = []
    for n, color in ((50, "tab:blue"), (200, "tab:orange")):
        ma = close.rolling(n).mean().tail(bars)
        if ma.notna().any():
            addplots.append(mpf.make_addplot(ma, color=color, width=1.2))
    path = out_dir / f"{safe_name(symbol)}.png"
    mpf.plot(
        plot_df,
        type="candle",
        style="charles",
        title=f"{name} ({symbol}) — diario · MM50 azul · MM200 naranja",
        volume=True,
        ylabel="Precio",
        ylabel_lower="Volumen",
        addplot=addplots or None,
        savefig=dict(fname=str(path), dpi=110, bbox_inches="tight"),
        figsize=(11, 6),
    )
    return str(path)


def safe_name(symbol: str) -> str:
    """Nombre de archivo seguro: ^IBEX → IDX_IBEX, EURUSD=X → EURUSD_X (BTC-EUR se queda igual)."""
    return symbol.replace("^", "IDX_").replace("=", "_").replace("/", "_")


# ── Orquestación ─────────────────────────────────────────────────────────────

async def collect(cfg: dict, now: datetime | None = None, chart_symbols: set[str] | None = None,
                  charts_dir: Path | None = None) -> dict:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    watchlist = _watchlist_from_config(cfg)
    chart_symbols = chart_symbols or set()
    charts_dir = charts_dir or (DEFAULT_CHARTS_DIR / now_local.date().isoformat())

    async with aiohttp.ClientSession(trust_env=True) as session:
        results = await asyncio.gather(
            *(fetch_history(session, sym) for sym, _, _ in watchlist), return_exceptions=True
        )

    symbols: dict[str, dict] = {}
    issues: list[dict] = []
    for (sym, name, group), res in zip(watchlist, results):
        if isinstance(res, Exception):
            issues.append({"symbol": sym, "message": f"{type(res).__name__}: {res}"})
            continue
        entry = {"name": name, "group": group, **compute_indicators(res), "recent_bars": recent_bars(res)}
        if sym in chart_symbols:
            try:
                entry["chart"] = make_chart(sym, name, res, charts_dir)
            except Exception as exc:
                issues.append({"symbol": sym, "message": f"chart: {exc}"})
        symbols[sym] = entry

    missing = chart_symbols - {s for s, _, _ in watchlist}
    for sym in sorted(missing):
        issues.append({"symbol": sym, "message": "no está en la watchlist de config.yaml"})

    return {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "source": "Yahoo Finance (velas diarias, 2 años)",
        "symbols": symbols,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Precios e indicadores técnicos de la watchlist.")
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
    out = Path(args.output) if args.output else DEFAULT_DATA_DIR / f"prices_{date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Precios guardados en {out}")
    print(f"  Símbolos OK: {len(data['symbols'])} | Incidencias: {len(data['issues'])}")
    for sym, d in data["symbols"].items():
        line = f"  {sym:10} {d['price']:>10} {d['change_pct']['1d']}% tendencia={d['trend']} RSI={d['rsi14']}"
        if "chart" in d:
            line += f" gráfico={d['chart']}"
        print(line)
    for issue in data["issues"]:
        print(f"  ⚠ {issue['symbol']}: {issue['message']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
