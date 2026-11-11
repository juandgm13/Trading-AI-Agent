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


# ── último cierre fiable (meta, vela sin cerrar, retraso, reintento 5d) ─────

DAY = 86400
T0 = 1_762_761_600  # 2025-11-10 08:00 UTC (lunes)


def _meta_payload(closes, meta=None, start=T0):
    ts = [start + i * DAY for i in range(len(closes))]
    return {"chart": {"result": [{
        "meta": meta or {}, "timestamp": ts,
        "indicators": {"quote": [{"open": closes, "high": closes, "low": closes,
                                  "close": closes, "volume": [5] * len(closes)}]},
    }]}}


def test_parse_fills_null_last_close_from_meta():
    meta = {"regularMarketPrice": 9.5, "regularMarketTime": T0 + 2 * DAY + 3600,
            "regularMarketDayHigh": 9.8, "exchangeTimezoneName": "Europe/Madrid",
            "currentTradingPeriod": {"regular": {"end": T0 + 2 * DAY + 30000}}}
    df = fp.parse_yahoo_chart(_meta_payload([1.0, 2.0, None], meta))
    assert list(df["Close"]) == [1.0, 2.0, 9.5]
    assert df["High"].iloc[-1] == 9.8 and df["Low"].iloc[-1] == 9.5
    assert df.attrs == {"price_source": "meta", "exchange_tz": "Europe/Madrid", "regular_end": T0 + 2 * DAY + 30000}


def test_parse_appends_missing_bar_from_meta_at_same_hour():
    meta = {"regularMarketPrice": 7.0, "regularMarketTime": T0 + 3 * DAY + 50000, "regularMarketVolume": 99}
    df = fp.parse_yahoo_chart(_meta_payload([1.0, 2.0], meta))
    assert list(df["Close"]) == [1.0, 2.0, 7.0]
    assert df.index[-1] == pd.Timestamp(T0 + 3 * DAY, unit="s", tz="UTC")
    assert df["Volume"].iloc[-1] == 99 and df.attrs["price_source"] == "meta"


@pytest.mark.parametrize("meta", [
    {},                                                             # sin meta
    {"regularMarketPrice": 2.0, "regularMarketTime": T0 + DAY},     # coincide con una vela completa
    {"regularMarketPrice": 2.0, "regularMarketTime": T0 - 5 * DAY}, # meta más antigua
])
def test_parse_keeps_bars_when_meta_not_needed(meta):
    df = fp.parse_yahoo_chart(_meta_payload([1.0, 2.0], meta))
    assert list(df["Close"]) == [1.0, 2.0] and df.attrs["price_source"] == "bars"


def test_patch_with_meta_on_empty_frame_uses_meta_timestamp():
    empty = pd.DataFrame({c: [] for c in ("Open", "High", "Low", "Close", "Volume")},
                         index=pd.DatetimeIndex([], tz="UTC"), dtype=float)
    out, src = fp._patch_with_meta(empty, {"regularMarketPrice": 3.0, "regularMarketTime": T0})
    assert src == "meta" and out.index[0] == pd.Timestamp(T0, unit="s", tz="UTC")


def _bars(dates, tz="Europe/Madrid", end=None):
    idx = pd.DatetimeIndex([pd.Timestamp(d + " 08:00", tz="UTC") for d in dates])
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": range(1, len(dates) + 1), "Volume": 1.0},
                      index=idx, dtype=float)
    df.attrs = {"exchange_tz": tz, "regular_end": end, "price_source": "bars"}
    return df


def test_drop_unclosed_bar():
    now = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
    end = int(pd.Timestamp("2026-11-11 16:35", tz="UTC").timestamp())
    df = _bars(["2026-11-10", "2026-11-11"], end=end)
    out = fp.drop_unclosed_bar(df, now)
    assert len(out) == 1 and out.attrs["exchange_tz"] == "Europe/Madrid"
    assert len(fp.drop_unclosed_bar(df, datetime(2026, 11, 11, 17, 0, tzinfo=timezone.utc))) == 2  # ya cerró
    assert len(fp.drop_unclosed_bar(_bars(["2026-11-09", "2026-11-10"]), now)) == 2              # sin vela de hoy
    assert len(fp.drop_unclosed_bar(_bars(["2026-11-11"]), now)) == 1                            # no deja vacío
    assert len(fp.drop_unclosed_bar(_bars(["2026-11-10", "2026-11-11"]), now)) == 1              # sin hora de cierre


def test_business_days_lag_and_merge():
    now = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)  # miércoles
    assert fp.business_days_lag(_bars(["2026-11-10"]), now) == 1
    assert fp.business_days_lag(_bars(["2026-11-09"]), now) == 2
    assert fp.business_days_lag(_bars(["2026-11-06"]), datetime(2026, 11, 9, 7, tzinfo=timezone.utc)) == 1  # lunes
    base, recent = _bars(["2026-11-06", "2026-11-09"]), _bars(["2026-11-09", "2026-11-10"])
    recent["Close"] = [20.0, 30.0]
    recent.attrs["price_source"] = "meta"
    merged = fp._merge_recent(base, recent)
    assert list(merged["Close"]) == [1.0, 20.0, 30.0] and merged.attrs["price_source"] == "meta"


@pytest.mark.asyncio
async def test_history_with_fallback_paths():
    now = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)
    fresh, stale = _bars(["2026-11-09", "2026-11-10"]), _bars(["2026-11-06", "2026-11-09"])
    recent = _bars(["2026-11-09", "2026-11-10"])

    async def run(results):
        it = iter(results)

        async def hist(session, sym, range_="2y"):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        with patch.object(fp, "fetch_history", side_effect=hist) as m:
            out = await fp._history_with_fallback(None, "X", now)
        return out, m

    out, m = await run([fresh])
    assert out is fresh and m.call_count == 1                          # al día: sin reintento
    out, m = await run([stale, recent])
    assert out.index[-1].date().isoformat() == "2026-11-10" and m.call_args.kwargs == {"range_": "5d"}
    out, _ = await run([stale, ValueError("HTTP 500")])
    assert out is stale                                                 # el reintento falla: serie larga
    out, _ = await run([stale, _bars(["2026-11-06", "2026-11-09"])])
    assert out is stale                                                 # el reintento no trae nada nuevo


@pytest.mark.asyncio
async def test_collect_flags_stale_and_price_source():
    df = _df(np.linspace(10, 20, 260))
    df.attrs = {"price_source": "meta", "exchange_tz": "UTC"}
    with patch.object(fp, "_history_with_fallback", new=AsyncMock(return_value=df)), \
         patch.object(fp.aiohttp, "ClientSession", return_value=_FakeSession()):
        data = await fp.collect({"watchlist": {"eeuu": [{"symbol": "AAPL", "name": "Apple"}]}}, now=NOW)
    entry = data["symbols"]["AAPL"]
    assert entry["stale"] is True and entry["price_source"] == "meta"
    assert "días hábiles de retraso" in data["issues"][0]["message"]
