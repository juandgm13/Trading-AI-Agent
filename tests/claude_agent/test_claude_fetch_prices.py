"""Tests de claude_agent/fetch_prices.py (precios + indicadores técnicos del informe diario)."""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from claude_agent import fetch_prices as fp

NOW = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)


def _df(closes, volume=1000):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D", tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": volume}, index=idx)


def _payload(closes, error=None):
    ts = [1_700_000_000 + i * 86400 for i in range(len(closes))]
    return {"chart": {"error": error, "result": None if error else [{
        "timestamp": ts,
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes,
                                  "close": closes, "volume": [None] * len(closes)}]},
    }]}}


# ── parse_yahoo_chart ───────────────────────────────────────────────────────

def test_parse_yahoo_chart_drops_null_closes_and_fills_volume():
    df = fp.parse_yahoo_chart(_payload([1.0, None, 3.0]))
    assert list(df["Close"]) == [1.0, 3.0]
    assert (df["Volume"] == 0).all()
    assert str(df.index.tz) == "UTC"


@pytest.mark.parametrize("payload, msg", [
    (_payload([1.0], error={"code": "Not Found"}), "Yahoo error"),
    ({"chart": {"result": []}}, "sin datos"),
    ({}, "sin datos"),
    (_payload([None, None]), "sin cierres"),
])
def test_parse_yahoo_chart_errors(payload, msg):
    with pytest.raises(ValueError, match=msg):
        fp.parse_yahoo_chart(payload)


# ── indicadores ──────────────────────────────────────────────────────────────

def test_rsi_only_up_is_100_and_only_down_is_0():
    up = pd.Series(np.arange(1, 40, dtype=float))
    down = pd.Series(np.arange(40, 1, -1, dtype=float))
    assert fp.rsi(up).iloc[-1] == 100.0
    assert fp.rsi(down).iloc[-1] == pytest.approx(0.0)
    assert pd.isna(fp.rsi(up).iloc[5])  # periodo de calentamiento


def test_macd_positive_in_uptrend():
    line, sig, hist = fp.macd(pd.Series(np.linspace(10, 50, 100)))
    assert line.iloc[-1] > 0 and sig.iloc[-1] > 0
    assert hist.iloc[-1] == pytest.approx(line.iloc[-1] - sig.iloc[-1])


def test_pct_change():
    s = pd.Series([100.0, 110.0])
    assert fp.pct_change(s, 1) == 10.0
    assert fp.pct_change(s, 5) is None
    assert fp.pct_change(pd.Series([0.0, 1.0]), 1) is None


@pytest.mark.parametrize("price, s50, s200, prev, expected", [
    (110, 100, 90, 95, "alcista"),
    (80, 90, 100, 95, "bajista"),
    (95, 100, 90, 95, "lateral"),
    (110, 100, 90, None, "lateral"),
    (110, None, 90, 95, "datos_insuficientes"),
])
def test_classify_trend(price, s50, s200, prev, expected):
    assert fp.classify_trend(price, s50, s200, prev) == expected


@pytest.mark.parametrize("value, zone", [(75, "sobrecompra"), (25, "sobreventa"), (50, "neutral"), (None, None)])
def test_rsi_zone(value, zone):
    assert fp.rsi_zone(value) == zone


def test_swing_levels_finds_support_and_resistance():
    closes = [10, 9, 8, 9, 10, 11, 12, 11, 10, 9, 10, 10.5] * 3
    sup, res = fp.swing_levels(_df(closes), window=2, lookback=100)
    price = closes[-1]
    assert sup and all(s < price for s in sup)
    assert res and all(r > price for r in res)
    assert sup == sorted(sup, reverse=True)


def test_compute_indicators_uptrend_full_history():
    df = _df(np.linspace(50, 150, 300))
    ind = fp.compute_indicators(df)
    assert ind["price"] == 150.0
    assert ind["trend"] == "alcista"
    assert ind["rsi14"] == 100.0 and ind["rsi_zone"] == "sobrecompra"
    assert ind["change_pct"]["1d"] > 0 and ind["change_pct"]["1y"] > 0
    assert ind["sma"]["200"] is not None
    assert ind["price_vs_sma_pct"]["200"] > 0
    assert ind["macd"]["momentum"] in {"mejorando", "empeorando"}
    assert ind["volume_vs_avg20"] == 1.0
    assert ind["dist_from_high_52w_pct"] <= 0 <= ind["dist_from_low_52w_pct"]
    json.dumps(ind)


def test_compute_indicators_short_history():
    ind = fp.compute_indicators(_df(np.linspace(10, 12, 30), volume=0))
    assert ind["sma"]["200"] is None and ind["trend"] == "datos_insuficientes"
    assert ind["sma_cross_last_20d"] is None
    assert ind["volume_vs_avg20"] is None
    assert ind["change_pct"]["3m"] is None


def test_golden_and_death_cross_detection():
    golden = np.concatenate([np.linspace(100, 60, 230), np.linspace(60, 140, 60)])
    death = np.concatenate([np.linspace(60, 100, 230), np.linspace(100, 20, 60)])
    found_golden = found_death = False
    for cut in range(240, 291):
        g = fp.compute_indicators(_df(golden[:cut]))["sma_cross_last_20d"]
        d = fp.compute_indicators(_df(death[:cut]))["sma_cross_last_20d"]
        found_golden |= g == "cruce_dorado"
        found_death |= d == "cruce_de_la_muerte"
    assert found_golden and found_death


def test_recent_bars_and_safe_name():
    bars = fp.recent_bars(_df([1.0, 2.0, 3.0]), n=2)
    assert len(bars) == 2 and bars[-1][4] == 3.0 and isinstance(bars[-1][5], int)
    assert fp.safe_name("^IBEX") == "IDX_IBEX"
    assert fp.safe_name("EURUSD=X") == "EURUSD_X"


def test_make_chart_writes_png(tmp_path):
    path = fp.make_chart("SAN.MC", "Santander", _df(np.linspace(10, 20, 250)), tmp_path / "c")
    assert path.endswith("SAN.MC.png")
    assert (tmp_path / "c" / "SAN.MC.png").stat().st_size > 1000


# ── fetch_history / collect / main ──────────────────────────────────────────

class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


def test_compute_indicators_crypto_calendar_uses_365_days():
    rng = np.random.default_rng(0)
    closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, 400))
    stock = fp.compute_indicators(_df(closes))
    crypto = fp.compute_indicators(_df(closes), calendar="cripto")
    assert crypto["change_pct"]["1w"] == fp.pct_change(pd.Series(closes), 7)
    assert crypto["change_pct"]["1m"] == fp.pct_change(pd.Series(closes), 30)
    assert crypto["change_pct"]["1y"] == fp.pct_change(pd.Series(closes), 365)
    assert stock["change_pct"]["1y"] == fp.pct_change(pd.Series(closes), 252)
    assert crypto["volatility_20d_annual_pct"] > stock["volatility_20d_annual_pct"]  # √365 > √252
    with pytest.raises(KeyError):
        fp.compute_indicators(_df(closes), calendar="otro")


def test_round_keeps_significant_digits_below_one():
    assert fp._r(0.0000034567) == 0.000003457
    assert fp._r(0.5) == 0.5 and fp._r(-0.012345, 3) == -0.01235
    assert fp._r(123.456) == 123.46 and fp._r(0) == 0 and fp._r(None) is None


def test_safe_name_keeps_crypto_symbol():
    assert fp.safe_name("BTC-EUR") == "BTC-EUR"


@pytest.mark.asyncio
async def test_fetch_history_ok_and_http_error():
    session = MagicMock()
    session.get.return_value = _Resp(200, _payload([1.0, 2.0]))
    df = await fp.fetch_history(session, "AAPL")
    assert len(df) == 2
    assert session.get.call_args.kwargs["params"] == {"range": "2y", "interval": "1d"}
    session.get.return_value = _Resp(404, {})
    with pytest.raises(ValueError, match="HTTP 404"):
        await fp.fetch_history(session, "XXX")


CFG = {"timezone": "Europe/Madrid", "watchlist": {
    "espana": [{"symbol": "SAN.MC", "name": "Santander"}],
    "eeuu": [{"symbol": "AAPL", "name": "Apple"}, {"symbol": "BAD", "name": "Bad"}]}}


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_collect_indicators_charts_and_issues(tmp_path):
    async def hist(session, sym, *a, **k):
        if sym == "BAD":
            raise ValueError("HTTP 404")
        return _df(np.linspace(10, 20, 260))

    def chart(sym, name, df, out_dir):
        if sym == "AAPL":
            raise RuntimeError("sin fuentes")
        return str(out_dir / f"{sym}.png")

    with patch.object(fp, "fetch_history", side_effect=hist), \
         patch.object(fp, "make_chart", side_effect=chart), \
         patch.object(fp.aiohttp, "ClientSession", return_value=_FakeSession()):
        data = await fp.collect(CFG, now=NOW, chart_symbols={"SAN.MC", "AAPL", "ZZZ"}, charts_dir=tmp_path)

    assert set(data["symbols"]) == {"SAN.MC", "AAPL"}
    assert data["symbols"]["SAN.MC"]["chart"] == str(tmp_path / "SAN.MC.png")
    assert "chart" not in data["symbols"]["AAPL"]
    assert len(data["symbols"]["SAN.MC"]["recent_bars"]) == fp.RECENT_BARS
    msgs = {i["symbol"]: i["message"] for i in data["issues"]}
    assert "HTTP 404" in msgs["BAD"] and "chart" in msgs["AAPL"] and "watchlist" in msgs["ZZZ"]
    json.dumps(data)


@pytest.mark.asyncio
async def test_collect_default_charts_dir():
    with patch.object(fp, "fetch_history", new=AsyncMock(return_value=_df(np.linspace(1, 2, 30)))), \
         patch.object(fp, "make_chart", return_value="x.png") as mk, \
         patch.object(fp.aiohttp, "ClientSession", return_value=_FakeSession()):
        await fp.collect(CFG, now=NOW, chart_symbols={"AAPL"})
    assert mk.call_args.args[3] == fp.DEFAULT_CHARTS_DIR / "2026-11-10"


def test_main_writes_json_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fp, "DEFAULT_DATA_DIR", tmp_path)
    ind = fp.compute_indicators(_df(np.linspace(10, 20, 260)))
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00",
            "symbols": {"SAN.MC": {**ind, "chart": "c.png"}},
            "issues": [{"symbol": "BAD", "message": "HTTP 404"}]}
    with patch.object(fp, "collect", new=AsyncMock(return_value=fake)) as col:
        assert fp.main(["--charts", "SAN.MC, AAPL", "--charts-dir", str(tmp_path / "ch")]) == 0
    assert col.call_args.kwargs["chart_symbols"] == {"SAN.MC", "AAPL"}
    assert json.loads((tmp_path / "prices_2026-11-10.json").read_text(encoding="utf-8"))["symbols"]
    out = capsys.readouterr().out
    assert "gráfico=c.png" in out and "BAD: HTTP 404" in out


def test_main_explicit_output(tmp_path):
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "symbols": {}, "issues": []}
    out = tmp_path / "p.json"
    with patch.object(fp, "collect", new=AsyncMock(return_value=fake)):
        fp.main(["--output", str(out)])
    assert out.exists()
