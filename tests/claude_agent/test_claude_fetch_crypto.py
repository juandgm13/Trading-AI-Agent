"""Tests de claude_agent/fetch_crypto.py (criptomonedas del informe diario)."""
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
    {"base_currency": "ETH", "quote_currency": "EUR", "status": "online"},
    {"base_currency": "SOL", "quote_currency": "USD", "status": "online"},           # sin par EUR
    {"base_currency": "OLD", "quote_currency": "EUR", "status": "delisted"},
    {"base_currency": "OFF", "quote_currency": "EUR", "status": "online", "trading_disabled": True},
    {"base_currency": "CXL", "quote_currency": "EUR", "status": "online", "cancel_only": True},
    {"quote_currency": "EUR", "status": "online"},                                   # sin base
]

FNG = {"data": [
    {"value": str(v), "value_classification": c, "timestamp": str(1_794_009_600 - i * 86400)}
    for i, (v, c) in enumerate([(25, "Extreme Fear"), (28, "Fear"), (40, "Fear"), (50, "Neutral"),
                                (55, "Greed"), (60, "Greed"), (70, "Greed"), (80, "Extreme Greed")])
]}

GLOBAL = {"market_cap_usd": 2_500, "market_cap_change_24h": -0.2, "volume_24h_usd": 50,
          "bitcoin_dominance_percentage": 57.2, "market_cap_ath_value": 5_000,
          "market_cap_ath_date": "2025-10-05T10:10:00Z"}


def _df(n=400):
    idx = pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC")
    c = pd.Series(np.linspace(100, 200, n), index=idx)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 10.0}, index=idx)


# ── parsers ──────────────────────────────────────────────────────────────────

def test_parse_coinbase_products_filters_tradable_eur_pairs():
    assert fc.parse_coinbase_products(PRODUCTS) == ["BTC", "ETH"]
    assert fc.parse_coinbase_products(PRODUCTS, quote="USD") == ["SOL"]
    assert fc.parse_coinbase_products(None) == []


def test_base_currency():
    assert fc.base_currency("btc-eur") == "BTC"
    assert fc.base_currency("ETH") == "ETH"


def test_parse_fear_greed_translates_and_keeps_history():
    out = fc.parse_fear_greed(FNG)
    assert out["value"] == 25 and out["clasificacion"] == "miedo extremo"
    assert out["classification"] == "Extreme Fear"
    assert out["yesterday"] == 28 and out["week_ago"] == 80
    assert out["date"] == "2026-11-07"


def test_parse_fear_greed_short_history_and_unknown_label():
    out = fc.parse_fear_greed({"data": [{"value": "50", "value_classification": "Raro", "timestamp": "0"}]})
    assert out["clasificacion"] == "Raro" and out["yesterday"] is None and out["week_ago"] is None


@pytest.mark.parametrize("payload", [{}, {"data": []}, None])
def test_parse_fear_greed_empty(payload):
    with pytest.raises(ValueError):
        fc.parse_fear_greed(payload)


def test_parse_global():
    out = fc.parse_global(GLOBAL)
    assert out["bitcoin_dominance_pct"] == 57.2
    assert out["dist_from_ath_pct"] == -50.0
    assert out["market_cap_ath_date"] == "2025-10-05"
    minimal = fc.parse_global({"market_cap_usd": 1})
    assert minimal["dist_from_ath_pct"] is None and minimal["market_cap_ath_date"] is None
    with pytest.raises(ValueError):
        fc.parse_global({})


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


# ── collect ──────────────────────────────────────────────────────────────────

class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


CFG = {"timezone": "Europe/Madrid", "crypto": {
    "quote_currency": "EUR", "max_portfolio_pct": 25,
    "watchlist": [{"symbol": "BTC-EUR", "name": "Bitcoin"}, {"symbol": "SOL-EUR", "name": "Solana"},
                  {"symbol": "BAD-EUR", "name": "Bad"}]}}


def _patches(get_json, hist=None, chart=None):
    async def default_hist(session, sym, *a, **k):
        if sym == "BAD-EUR":
            raise ValueError("HTTP 404")
        return _df()

    return (
        patch.object(fc, "_get_json", side_effect=get_json),
        patch.object(fc, "fetch_history", side_effect=hist or default_hist),
        patch.object(fc, "make_chart", side_effect=chart or (lambda s, n, df, d: str(d / f"{s}.png"))),
        patch.object(fc.aiohttp, "ClientSession", return_value=_FakeSession()),
    )


@pytest.mark.asyncio
async def test_collect_full(tmp_path):
    async def get_json(session, url, params=None):
        return {fc.COINBASE_PRODUCTS_URL: PRODUCTS, fc.FEAR_GREED_URL: FNG, fc.GLOBAL_URL: GLOBAL}[url]

    def chart(sym, name, df, out_dir):
        if sym == "SOL-EUR":
            raise RuntimeError("sin fuentes")
        return str(out_dir / f"{sym}.png")

    p1, p2, p3, p4 = _patches(get_json, chart=chart)
    with p1, p2, p3, p4:
        data = await fc.collect(CFG, now=NOW, chart_symbols={"BTC-EUR", "SOL-EUR", "ZZZ"}, charts_dir=tmp_path)

    assert data["coinbase_available"] == ["BTC", "ETH"]
    assert data["max_portfolio_pct"] == 25
    assert data["fear_greed"]["value"] == 25
    assert data["market"]["bitcoin_dominance_pct"] == 57.2
    btc, sol = data["symbols"]["BTC-EUR"], data["symbols"]["SOL-EUR"]
    assert btc["coinbase_available"] is True and sol["coinbase_available"] is False
    assert btc["currency"] == "EUR" and btc["chart"] == str(tmp_path / "BTC-EUR.png")
    assert btc["change_pct"]["1y"] == fc.compute_indicators(_df(), calendar="cripto")["change_pct"]["1y"]
    assert "chart" not in sol
    msgs = {i.get("symbol") or i.get("source"): i["message"] for i in data["issues"]}
    assert "HTTP 404" in msgs["BAD-EUR"] and "chart" in msgs["SOL-EUR"] and "watchlist" in msgs["ZZZ"]
    json.dumps(data)


@pytest.mark.asyncio
async def test_collect_sources_fail_gracefully():
    async def get_json(session, url, params=None):
        if url == fc.COINBASE_PRODUCTS_URL:
            raise ValueError("HTTP 503")
        if url == fc.FEAR_GREED_URL:
            return {"data": []}  # parse error
        return {"market_cap_usd": 1, "market_cap_ath_value": "x"}  # TypeError al dividir

    p1, p2, p3, p4 = _patches(get_json)
    with p1, p2, p3, p4:
        data = await fc.collect(CFG, now=NOW)

    assert data["coinbase_available"] is None
    assert data["fear_greed"] is None and data["market"] is None
    assert data["symbols"]["BTC-EUR"]["coinbase_available"] is None
    sources = {i.get("source") for i in data["issues"]}
    assert {"coinbase", "fear_greed", "coinpaprika"} <= sources


@pytest.mark.asyncio
async def test_collect_coinbase_without_eur_pairs_and_default_dir():
    async def get_json(session, url, params=None):
        if url == fc.COINBASE_PRODUCTS_URL:
            return [{"base_currency": "BTC", "quote_currency": "USD", "status": "online"}]
        if url == fc.FEAR_GREED_URL:
            raise ValueError("HTTP 500")
        return GLOBAL

    p1, p2, p3, p4 = _patches(get_json)
    with p1, p2, p3 as mk, p4:
        data = await fc.collect(CFG, now=NOW, chart_symbols={"BTC-EUR"})
    assert data["coinbase_available"] is None
    assert any("sin pares EUR" in i["message"] for i in data["issues"])
    assert mk.call_args.args[3] == fc.DEFAULT_CHARTS_DIR / "2026-11-10"


# ── main ─────────────────────────────────────────────────────────────────────

def test_main_writes_json_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fc, "DEFAULT_DATA_DIR", tmp_path)
    ind = fc.compute_indicators(_df(), calendar="cripto")
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00",
            "fear_greed": {"value": 25, "clasificacion": "miedo extremo"},
            "symbols": {"BTC-EUR": {**ind, "coinbase_available": True, "chart": "c.png"},
                        "SOL-EUR": {**ind, "coinbase_available": False},
                        "ETH-EUR": {**ind, "coinbase_available": None}},
            "issues": [{"symbol": "BAD-EUR", "message": "HTTP 404"}, {"source": "coinbase", "message": "x"}]}
    with patch.object(fc, "collect", new=AsyncMock(return_value=fake)) as col:
        assert fc.main(["--charts", "BTC-EUR, SOL-EUR", "--charts-dir", str(tmp_path / "ch")]) == 0
    assert col.call_args.kwargs["chart_symbols"] == {"BTC-EUR", "SOL-EUR"}
    assert json.loads((tmp_path / "crypto_2026-11-10.json").read_text(encoding="utf-8"))["symbols"]
    out = capsys.readouterr().out
    assert "miedo extremo" in out and "gráfico=c.png" in out and "NO en Coinbase" in out
    assert "Coinbase ?" in out and "BAD-EUR: HTTP 404" in out and "coinbase: x" in out


def test_main_explicit_output_without_fear_greed(tmp_path):
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "fear_greed": None, "symbols": {}, "issues": []}
    out = tmp_path / "c.json"
    with patch.object(fc, "collect", new=AsyncMock(return_value=fake)):
        fc.main(["--output", str(out)])
    assert out.exists()
