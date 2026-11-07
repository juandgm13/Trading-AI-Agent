"""Tests de claude_agent/fetch_crypto.py (universo Coinbase, radar, técnico y sentimiento)."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from claude_agent import fetch_crypto as fc

NOW = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)

PRODUCTS = [
    {"base_currency": "BTC", "quote_currency": "EUR", "status": "online", "trading_disabled": False},
    {"base_currency": "BTC", "quote_currency": "USD", "status": "online"},
    {"base_currency": "PEPE", "quote_currency": "USD", "status": "online"},
    {"base_currency": "USDT", "quote_currency": "USD", "status": "online"},
    {"base_currency": "GBPC", "quote_currency": "GBP", "status": "online"},           # sin EUR/USD/USDC
    {"base_currency": "NODATA", "quote_currency": "USD", "status": "online"},        # sin datos Coinpaprika
    {"base_currency": "OLD", "quote_currency": "EUR", "status": "delisted"},
    {"base_currency": "OFF", "quote_currency": "EUR", "status": "online", "trading_disabled": True},
    {"base_currency": "CXL", "quote_currency": "EUR", "status": "online", "cancel_only": True},
    {"quote_currency": "EUR", "status": "online"},                                   # sin base
]


def _ticker(sym, name, rank, price, d24=1.0, d7=2.0, d30=3.0, d1y=4.0):
    return {"symbol": sym, "name": name, "rank": rank, "quotes": {"USD": {
        "price": price, "market_cap": price * 1000, "volume_24h": 10.0, "percent_change_24h": d24,
        "percent_change_7d": d7, "percent_change_30d": d30, "percent_change_1y": d1y,
        "percent_from_price_ath": -20.0}}}


TICKERS = [
    _ticker("BTC", "Bitcoin", 1, 70000, d24=-1, d7=-5, d30=-10),
    _ticker("PEPE", "Pepe", 49, 0.0000035, d24=3, d7=20, d30=30),
    _ticker("PEPE", "Pepe clon", 900, 0.1),                     # símbolo repetido peor ranking → se ignora
    _ticker("USDT", "Tether USD", 3, 1.0, d24=0, d7=0, d30=0),
    _ticker("GBPC", "Libra coin", 0, 5.0, d24=None, d7=None, d30=None),  # rank 0 = sin ranking
    {"symbol": "", "quotes": {"USD": {"price": 1}}},            # sin símbolo
    {"symbol": "NOQ", "rank": 5},                               # sin cotización
]

FNG = {"data": [
    {"value": str(v), "value_classification": c, "timestamp": str(1_794_009_600 - i * 86400)}
    for i, (v, c) in enumerate([(25, "Extreme Fear"), (28, "Fear"), (40, "Fear"), (50, "Neutral"),
                                (55, "Greed"), (60, "Greed"), (70, "Greed"), (80, "Extreme Greed")])
]}

GLOBAL = {"market_cap_usd": 2_500, "market_cap_change_24h": -0.2, "volume_24h_usd": 50,
          "bitcoin_dominance_percentage": 57.2, "market_cap_ath_value": 5_000,
          "market_cap_ath_date": "2025-10-05T10:10:00Z"}

RATES = {"data": {"currency": "USD", "rates": {"EUR": "0.9"}}}


def _candles(n, start_price=100.0, t0=1_700_000_000):
    """Formato Coinbase: [time, low, high, open, close, volume], más reciente primero."""
    rows = []
    for i in range(n):
        c = start_price + i
        rows.append([t0 + i * 86400, c * 0.99, c * 1.01, c, c, 5.0])
    return rows[::-1]


# ── helpers puros ────────────────────────────────────────────────────────────

def test_coin_code():
    assert fc.coin_code("btc-eur") == "BTC"
    assert fc.coin_code(" pepe ") == "PEPE"


def test_parse_coinbase_products_all_quotes():
    out = fc.parse_coinbase_products(PRODUCTS)
    assert out == {"BTC": ["EUR", "USD"], "GBPC": ["GBP"], "NODATA": ["USD"], "PEPE": ["USD"], "USDT": ["USD"]}
    assert fc.parse_coinbase_products(None) == {}


@pytest.mark.parametrize("quotes, expected", [
    (["EUR", "USD"], "EUR"), (["USD", "USDT"], "USD"), (["USDC"], "USDC"), (["GBP", "BTC"], "GBP"), ([], None), (None, None),
])
def test_pick_quote(quotes, expected):
    assert fc.pick_quote(quotes) == expected


def test_parse_coinbase_candles_sorts_dedups_and_errors():
    page1, page2 = _candles(3, t0=1_700_000_000 + 2 * 86400), _candles(3)  # se solapan en 1 vela
    df = fc.parse_coinbase_candles([page1, page2])
    assert len(df) == 5 and df.index.is_monotonic_increasing
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert str(df.index.tz) == "UTC"
    with pytest.raises(ValueError, match="sin velas"):
        fc.parse_coinbase_candles([[], None, [["bad"]]])


def test_parse_usd_eur():
    assert fc.parse_usd_eur(RATES) == 0.9
    with pytest.raises(ValueError):
        fc.parse_usd_eur({"data": {"rates": {"EUR": "0"}}})
    with pytest.raises(TypeError):
        fc.parse_usd_eur({})


def test_parse_tickers_best_rank_and_skips_invalid():
    out = fc.parse_tickers(TICKERS)
    assert set(out) == {"BTC", "PEPE", "USDT", "GBPC"}
    assert out["PEPE"]["name"] == "Pepe" and out["PEPE"]["rank"] == 49
    assert out["GBPC"]["rank"] is None
    assert out["BTC"]["change_pct"] == {"24h": -1, "7d": -5, "30d": -10, "1y": 4.0}
    # el de peor ranking no sustituye; uno con ranking sí sustituye a uno sin ranking
    out2 = fc.parse_tickers([_ticker("X", "a", None, 1), _ticker("X", "b", 7, 1), _ticker("X", "c", 9, 1)])
    assert out2["X"]["name"] == "b"
    assert fc.parse_tickers(None) == {}


def test_is_stablecoin():
    assert fc.is_stablecoin("USDT", None)
    assert fc.is_stablecoin("NEWUSD", {"name": "New USD", "price_usd": 1.001})
    assert not fc.is_stablecoin("BTC", {"name": "Bitcoin", "price_usd": 70000})
    assert not fc.is_stablecoin("ONE", {"name": "One token", "price_usd": 1.0})  # precio 1 pero no es "USD"
    assert not fc.is_stablecoin("X", None) and not fc.is_stablecoin("X", {"price_usd": None})


def test_build_universe_and_screener():
    coinbase = fc.parse_coinbase_products(PRODUCTS)
    universe, unmatched = fc.build_universe(coinbase, fc.parse_tickers(TICKERS), usd_eur=0.9)
    assert [r["symbol"] for r in universe] == ["BTC", "USDT", "PEPE", "GBPC"]  # por ranking, sin ranking al final
    assert unmatched == ["NODATA"]
    btc = universe[0]
    assert btc["coinbase_quotes"] == ["EUR", "USD"] and btc["price_eur"] == 63000
    assert universe[1]["stablecoin"] and not btc["stablecoin"]
    assert universe[2]["price_usd"] == 0.0000035
    no_rate, _ = fc.build_universe(coinbase, fc.parse_tickers(TICKERS), usd_eur=None)
    assert no_rate[0]["price_eur"] is None

    sc = fc.build_screener(universe, top=2, movers=1, movers_rank=100)
    assert [r["symbol"] for r in sc["top_market_cap"]] == ["BTC", "PEPE"]  # sin stablecoins
    assert sc["gainers_7d"][0]["symbol"] == "PEPE" and sc["losers_7d"][0]["symbol"] == "BTC"
    assert sc["gainers_30d"][0]["symbol"] == "PEPE" and sc["losers_30d"][0]["symbol"] == "BTC"
    assert sc["breadth_24h"] == {"up": 1, "down": 1, "total": 2}  # GBPC sin dato no cuenta
    assert fc.build_screener(universe, movers_rank=10)["gainers_7d"] == [{**sc["losers_7d"][0]}]


def test_parse_fear_greed():
    out = fc.parse_fear_greed(FNG)
    assert out["value"] == 25 and out["clasificacion"] == "miedo extremo"
    assert out["yesterday"] == 28 and out["week_ago"] == 80 and out["date"] == "2026-11-07"
    short = fc.parse_fear_greed({"data": [{"value": "50", "value_classification": "Raro", "timestamp": "0"}]})
    assert short["clasificacion"] == "Raro" and short["yesterday"] is None and short["week_ago"] is None
    for payload in ({}, {"data": []}, None):
        with pytest.raises(ValueError):
            fc.parse_fear_greed(payload)


def test_parse_global():
    out = fc.parse_global(GLOBAL)
    assert out["bitcoin_dominance_pct"] == 57.2 and out["dist_from_ath_pct"] == -50.0
    assert out["market_cap_ath_date"] == "2025-10-05"
    minimal = fc.parse_global({"market_cap_usd": 1})
    assert minimal["dist_from_ath_pct"] is None and minimal["market_cap_ath_date"] is None
    with pytest.raises(ValueError):
        fc.parse_global({})


# ── selección del análisis detallado ─────────────────────────────────────────

def test_portfolio_crypto_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "_HERE", tmp_path)
    assert fc.portfolio_crypto_codes({}) == []
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "cartera.csv").write_text(
        "fecha,activo,tipo,operacion,cantidad,precio,moneda\n"
        "2026-01-01,PEPE-USD,cripto,compra,1000,0.00001,USD\n"
        "2026-01-01,btc-eur,,compra,0.1,50000,EUR\n"
        "2026-01-01,ETH-EUR,cripto,compra,1,2000,EUR\n"
        "2026-02-01,ETH-EUR,cripto,venta,1,2500,EUR\n"          # cerrada → no cuenta
        "2026-01-01,SPY,etf,compra,1,500,USD\n", encoding="utf-8")
    assert fc.portfolio_crypto_codes({}) == ["BTC", "PEPE"]


def test_select_detailed_order_dedup_and_reasons():
    universe = [{"symbol": s, "stablecoin": s == "USDT"} for s in ("BTC", "USDT", "ETH", "SOL")]
    cfg = {"crypto": {"auto_top_n": 2, "watchlist": [{"symbol": "BTC"}, {"symbol": "XRP"}]}}
    with patch.object(fc, "portfolio_crypto_codes", return_value=["PEPE", "BTC"]):
        out = fc.select_detailed(cfg, universe, ["pepe-usd", "DOGE"])
    assert out == [("BTC", ["núcleo", "cartera", "top 2"]), ("XRP", ["núcleo"]), ("PEPE", ["cartera", "pedida"]),
                   ("ETH", ["top 2"]), ("DOGE", ["pedida"])]


# ── HTTP ─────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


@pytest.mark.asyncio
async def test_get_json_ok_and_error():
    session = MagicMock()
    session.get.return_value = _Resp(200, {"ok": 1})
    assert await fc._get_json(session, "https://x", params={"a": 1}) == {"ok": 1}
    assert session.get.call_args.kwargs["params"] == {"a": 1}
    session.get.return_value = _Resp(429, {})
    with pytest.raises(ValueError, match="HTTP 429"):
        await fc._get_json(session, "https://x")


@pytest.mark.asyncio
async def test_fetch_coinbase_history_pages_backwards():
    calls = []

    async def get_json(session, url, params=None):
        calls.append((url, params))
        return _candles(2, t0=1_700_000_000 + len(calls) * 10 * 86400)

    with patch.object(fc, "_get_json", side_effect=get_json):
        df = await fc.fetch_coinbase_history(None, "PEPE-USD", NOW, pages=2)
    assert len(calls) == 2 and "PEPE-USD" in calls[0][0]
    assert calls[0][1]["end"] == NOW.isoformat() and calls[1][1]["end"] == calls[0][1]["start"]
    assert calls[0][1]["granularity"] == 86400 and len(df) == 4


# ── collect ──────────────────────────────────────────────────────────────────

class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


CFG = {"timezone": "Europe/Madrid", "crypto": {
    "max_portfolio_pct": 25, "auto_top_n": 3, "screener_top": 5,
    "watchlist": [{"symbol": "BTC", "name": "Bitcoin"}, {"symbol": "MISSING", "name": "No está"}]}}


def _df(n=400):
    return fc.parse_coinbase_candles([_candles(n)])


def _patches(get_json, hist=None, chart=None):
    async def default_hist(session, product, now, pages=fc.CANDLE_PAGES):
        if product.startswith("GBPC"):
            raise ValueError("HTTP 404")
        return _df()

    return (
        patch.object(fc, "_get_json", side_effect=get_json),
        patch.object(fc, "fetch_coinbase_history", side_effect=hist or default_hist),
        patch.object(fc, "make_chart", side_effect=chart or (lambda s, n, df, d: str(d / f"{s}.png"))),
        patch.object(fc, "portfolio_crypto_codes", return_value=[]),
        patch.object(fc.aiohttp, "ClientSession", return_value=_FakeSession()),
    )


def _all_ok(session, url, params=None):
    return {fc.COINBASE_PRODUCTS_URL: PRODUCTS, fc.TICKERS_URL: TICKERS, fc.COINBASE_RATES_URL: RATES,
            fc.FEAR_GREED_URL: FNG, fc.GLOBAL_URL: GLOBAL}[url]


@pytest.mark.asyncio
async def test_collect_full(tmp_path):
    async def get_json(session, url, params=None):
        return _all_ok(session, url, params)

    def chart(sym, name, df, out_dir):
        if sym.startswith("BTC"):
            raise RuntimeError("sin fuentes")
        return str(out_dir / f"{sym}.png")

    p = _patches(get_json, chart=chart)
    with p[0], p[1] as hist, p[2], p[3], p[4]:
        data = await fc.collect(CFG, now=NOW, chart_symbols={"pepe-usd", "BTC", "GBPC"}, charts_dir=tmp_path)

    assert data["coinbase_count"] == 5 and data["usd_eur"] == 0.9
    assert data["max_portfolio_pct"] == 25 and data["fear_greed"]["value"] == 25
    assert data["market"]["bitcoin_dominance_pct"] == 57.2
    assert [r["symbol"] for r in data["universe"]] == ["BTC", "USDT", "PEPE", "GBPC"]
    assert data["universe_without_data"] == ["NODATA"]
    assert data["screener"]["top_market_cap"][0]["symbol"] == "BTC"
    assert set(data["symbols"]) == {"BTC", "PEPE"}
    btc, pepe = data["symbols"]["BTC"], data["symbols"]["PEPE"]
    assert btc["product"] == "BTC-EUR" and btc["price_eur"] == btc["price"] and btc["reasons"][0] == "núcleo"
    assert pepe["product"] == "PEPE-USD" and pepe["price_eur"] == pytest.approx(pepe["price"] * 0.9)
    assert pepe["chart"] == str(tmp_path / "PEPE-USD.png") and "chart" not in btc
    assert btc["coinbase_available"] is True and btc["rank"] == 1 and btc["name"] == "Bitcoin"
    products = {c.args[1] for c in hist.call_args_list}
    assert products == {"BTC-EUR", "PEPE-USD", "GBPC-GBP"}
    msgs = {i.get("symbol") or i.get("source"): i["message"] for i in data["issues"]}
    assert "Coinbase" in msgs["MISSING"] and "HTTP 404" in msgs["GBPC"] and "chart" in msgs["BTC"]
    json.dumps(data)


@pytest.mark.asyncio
async def test_collect_sources_fail_gracefully():
    async def get_json(session, url, params=None):
        if url == fc.COINBASE_PRODUCTS_URL:
            raise ValueError("HTTP 503")
        if url == fc.TICKERS_URL:
            raise ValueError("HTTP 429")
        if url == fc.COINBASE_RATES_URL:
            return {"data": {}}                         # TypeError
        if url == fc.FEAR_GREED_URL:
            return {"data": []}                         # ValueError
        return {"market_cap_usd": 1, "market_cap_ath_value": "x"}  # TypeError al dividir

    async def hist(session, product, now, pages=fc.CANDLE_PAGES):
        if product == "BTC-EUR":
            raise ValueError("HTTP 404")                # sin lista de Coinbase: prueba EUR y luego USD
        if product.startswith("MISSING"):
            raise ValueError("HTTP 404")
        return _df()

    p = _patches(get_json, hist=hist)
    with p[0], p[1] as h, p[2], p[3], p[4]:
        data = await fc.collect(CFG, now=NOW)

    assert data["coinbase_count"] is None and data["usd_eur"] is None
    assert data["fear_greed"] is None and data["market"] is None and data["screener"] is None
    assert data["universe"] == [] and set(data["symbols"]) == {"BTC"}
    btc = data["symbols"]["BTC"]
    assert btc["product"] == "BTC-USD" and btc["coinbase_available"] is None and btc["price_eur"] is None
    assert btc["name"] == "Bitcoin" and btc["rank"] is None
    assert "MISSING-EUR" in str(data["issues"]) and "MISSING-USD" in str(data["issues"])
    sources = {i.get("source") for i in data["issues"]}
    assert {"coinbase", "coinpaprika_tickers", "coinbase_rates", "fear_greed", "coinpaprika"} <= sources
    assert {c.args[1] for c in h.call_args_list} >= {"BTC-EUR", "BTC-USD"}


@pytest.mark.asyncio
async def test_collect_coinbase_empty_and_default_charts_dir():
    async def get_json(session, url, params=None):
        if url == fc.COINBASE_PRODUCTS_URL:
            return [{"base_currency": "BTC", "quote_currency": "EUR", "status": "delisted"}]
        return _all_ok(session, url, params)

    p = _patches(get_json)
    with p[0], p[1], p[2] as mk, p[3], p[4]:
        data = await fc.collect(CFG, now=NOW, chart_symbols={"BTC"})
    assert any("sin pares operables" in i["message"] for i in data["issues"])
    assert mk.call_args.args[3] == fc.DEFAULT_CHARTS_DIR / "2026-11-10"


# ── main ─────────────────────────────────────────────────────────────────────

def test_main_writes_json_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fc, "DEFAULT_DATA_DIR", tmp_path)
    ind = fc.compute_indicators(_df(), calendar="cripto")
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "coinbase_count": 410, "universe": [{}],
            "fear_greed": {"value": 25, "clasificacion": "miedo extremo"},
            "symbols": {"BTC": {**ind, "rank": 1, "currency": "EUR", "reasons": ["núcleo"], "chart": "c.png"},
                        "PEPE": {**ind, "rank": None, "currency": "USD", "reasons": ["pedida"]}},
            "issues": [{"symbol": "GBPC", "message": "HTTP 404"}, {"source": "coinbase", "message": "x"}]}
    with patch.object(fc, "collect", new=AsyncMock(return_value=fake)) as col:
        assert fc.main(["--charts", "BTC, PEPE", "--charts-dir", str(tmp_path / "ch")]) == 0
    assert col.call_args.kwargs["chart_symbols"] == {"BTC", "PEPE"}
    assert json.loads((tmp_path / "crypto_2026-11-10.json").read_text(encoding="utf-8"))["symbols"]
    out = capsys.readouterr().out
    assert "Coinbase: 410 monedas" in out and "miedo extremo" in out and "gráfico=c.png" in out
    assert "(pedida)" in out and "GBPC: HTTP 404" in out and "coinbase: x" in out


def test_main_explicit_output_without_fear_greed(tmp_path):
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "coinbase_count": None, "universe": [],
            "fear_greed": None, "symbols": {}, "issues": []}
    out = tmp_path / "c.json"
    with patch.object(fc, "collect", new=AsyncMock(return_value=fake)):
        fc.main(["--output", str(out)])
    assert out.exists()
