"""Tests de claude_agent/portfolio.py (cartera del usuario a partir de cartera.csv)."""
import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from claude_agent import portfolio as pf

NOW = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)
HEADER = "fecha,activo,nombre,tipo,operacion,cantidad,precio,moneda,comision,broker,notas\n"


def _mv(op, qty, price, activo="SAN.MC", fecha="2026-01-01", moneda="EUR", fee=0.0, tipo="accion", broker=""):
    return pf.Movement(date.fromisoformat(fecha), activo, "", tipo, op, qty, price, moneda, fee, broker)


# ── lectura ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("1,5", 1.5), ("2.5", 2.5),
    ("10 €", 10.0), ("$3", 3.0), ("", None), (None, None),
])
def test_num(text, expected):
    assert pf._num(text) == expected


def test_num_default():
    assert pf._num("", 0.0) == 0.0


@pytest.mark.parametrize("text", ["2026-11-09", "09/11/2026", "09-11-2026"])
def test_date_formats(text):
    assert pf._date(text) == date(2026, 11, 9)


def test_date_invalid():
    with pytest.raises(ValueError, match="AAAA-MM-DD"):
        pf._date("ayer")


def test_read_movements_valid_semicolon_and_errors():
    text = (
        "Fecha;Activo;Nombre;Tipo;Operacion;Cantidad;Precio;Moneda;Comision;Broker;Notas\n"
        "2026-02-01;san.mc;Santander;accion;Compra;100;9,5;eur;2;MyInvestor;\n"
        "2026-01-01;BTC-EUR;Bitcoin;;compra;0,01;80000;EUR;;Coinbase;\n"
        "2026-03-01;SAN.MC;;;dividendo;;12;EUR;;;\n"
        "\n"
        "2026-04-01;XXX;;;regalo;1;1;EUR;;;\n"
        "2026-04-01;;;;compra;1;1;EUR;;;\n"
        "2026-04-01;AAA;;;compra;0;1;EUR;;;\n"
        "2026-04-01;AAA;;;compra;1;-1;EUR;;;\n"
        "mañana;AAA;;;compra;1;1;EUR;;;\n"
        "2026-04-01;AAA;;;venta;;1;EUR;;;\n"
    )
    movs, issues = pf.read_movements(text)
    assert [m.activo for m in movs] == ["BTC-EUR", "SAN.MC", "SAN.MC"]  # ordenados por fecha
    btc, san, div = movs
    assert btc.tipo == "cripto" and btc.comision == 0.0  # tipo inferido por el sufijo -EUR
    assert san.precio == 9.5 and san.moneda == "EUR" and san.operacion == "compra" and san.broker == "MyInvestor"
    assert div.operacion == "dividendo" and div.cantidad == 0.0 and div.tipo == "otro"
    rows = {i["row"]: i["message"] for i in issues}
    assert set(rows) == {6, 7, 8, 9, 10, 11}  # nº de línea real (la línea 5 está vacía)
    assert "no válida" in rows[6] and "activo" in rows[7] and "mayor que 0" in rows[8]
    assert "precio" in rows[9] and "fecha" in rows[10] and "mayor que 0" in rows[11]


def test_read_movements_empty_and_missing_columns():
    assert pf.read_movements("") == ([], [])
    assert pf.read_movements(HEADER) == ([], [])
    movs, issues = pf.read_movements("fecha,activo\n2026-01-01,SPY\n")
    assert movs == [] and "operacion, precio" in issues[0]["message"]


# ── posiciones ───────────────────────────────────────────────────────────────

def test_build_positions_average_cost_sales_and_dividends():
    movs = [
        _mv("compra", 100, 9.5, fee=2, broker="MyInvestor"),
        _mv("compra", 100, 10.5, fecha="2026-02-01"),
        _mv("dividendo", 0, 12, fecha="2026-03-01", fee=1),
        _mv("venta", 50, 12, fecha="2026-04-01", fee=2),
    ]
    positions, issues = pf.build_positions(movs)
    pos = positions["SAN.MC"]
    assert issues == []
    assert pos.cantidad == 150
    assert pos.precio_medio == pytest.approx(10.01)              # (950+2+1050)/200
    assert pos.realizado == pytest.approx(11 + (600 - 2 - 50 * 10.01))
    assert pos.dividendos == 11
    assert pos.first_buy == date(2026, 1, 1) and pos.brokers == {"MyInvestor"}
    assert pos.movimientos == 4


def test_build_positions_oversell_close_and_currency_mismatch():
    movs = [
        _mv("compra", 10, 5),
        _mv("venta", 15, 6, fecha="2026-02-01"),                 # vende más de lo que tiene → ajusta
        _mv("venta", 1, 6, fecha="2026-03-01"),                  # ya no queda nada → se ignora
        _mv("compra", 1, 5, fecha="2026-04-01", moneda="USD"),   # moneda distinta → se ignora
    ]
    positions, issues = pf.build_positions(movs)
    pos = positions["SAN.MC"]
    assert pos.cantidad == 0 and pos.coste == 0 and pos.first_buy is None
    assert pos.precio_medio is None
    assert pos.realizado == pytest.approx(10)
    assert len(issues) == 3
    assert "se ajusta" in issues[0]["message"] and "moneda USD" in issues[2]["message"]


def test_fx_symbol():
    assert pf.fx_symbol("USD") == "EURUSD=X"


# ── valoración ───────────────────────────────────────────────────────────────

def _positions():
    movs = [
        _mv("compra", 2, 650, activo="SPY", moneda="USD", tipo="etf", fee=2),
        _mv("compra", 100, 10, tipo="accion"),
        _mv("compra", 0.01, 50000, activo="BTC-EUR", tipo="cripto"),
        _mv("compra", 1, 100, activo="CLOSED", fecha="2026-01-01"),
        _mv("venta", 1, 120, activo="CLOSED", fecha="2026-02-01"),
        _mv("compra", 5, 20, activo="NOPRICE"),
        _mv("compra", 1, 1000, activo="JPYCO", moneda="JPY"),
    ]
    return pf.build_positions(movs)[0]


def test_summarize_values_weights_and_crypto_cap():
    prices = {"SPY": 700.0, "SAN.MC": 12.0, "BTC-EUR": 60000.0, "JPYCO": 1100.0}
    out = pf.summarize(_positions(), prices, fx={"USD": 1.25}, crypto_cap_pct=25)
    rows = {r["activo"]: r for r in out["positions"]}
    spy = rows["SPY"]
    assert spy["valor"] == 1400 and spy["valor_eur"] == 1120 and spy["coste_eur"] == pytest.approx(1041.6)
    assert spy["pnl"] == 98 and spy["pnl_pct"] == pytest.approx(7.53)
    assert rows["NOPRICE"]["valor_eur"] == 100 and rows["NOPRICE"]["pnl"] is None  # valorado a coste
    assert rows["JPYCO"]["valor_eur"] is None                                       # sin tipo de cambio
    assert out["closed_positions"][0]["activo"] == "CLOSED"
    assert out["totals"]["valor_eur"] == pytest.approx(1120 + 1200 + 600 + 100)
    assert out["totals"]["pnl_realizado_eur"] == 20
    assert sum(r["peso_pct"] for r in out["positions"]) == pytest.approx(100, abs=0.05)
    assert out["positions"][0]["activo"] == "SAN.MC"                                # ordenado por valor
    c = out["crypto"]
    assert c["peso_pct"] == pytest.approx(600 / 3020 * 100, abs=0.01) and not c["excede_tope"]
    # Margen: (0.25·3020 − 600) / 0.75 → tras comprarlo, cripto = 25 % exacto
    assert c["margen_hasta_tope_eur"] == pytest.approx((0.25 * 3020 - 600) / 0.75, abs=0.01)
    msgs = " ".join(i["message"] for i in out["issues"])
    assert "sin precio actual" in msgs and "JPY/EUR" in msgs
    assert set(out["por_tipo_pct"]) == {"etf", "accion", "cripto"}


def test_summarize_crypto_over_cap_and_empty():
    positions = pf.build_positions([_mv("compra", 1, 100, activo="BTC-EUR", tipo="cripto"),
                                    _mv("compra", 1, 100, activo="SPY")])[0]
    out = pf.summarize(positions, {"BTC-EUR": 300.0, "SPY": 100.0}, fx={}, crypto_cap_pct=25)
    assert out["crypto"]["peso_pct"] == 75 and out["crypto"]["excede_tope"] is True
    assert out["crypto"]["margen_hasta_tope_eur"] == 0
    empty = pf.summarize({}, {}, fx={}, crypto_cap_pct=None)
    assert empty["totals"]["valor_eur"] == 0 and empty["totals"]["pnl_no_realizado_pct"] is None
    assert empty["crypto"]["peso_pct"] is None and empty["crypto"]["margen_hasta_tope_eur"] is None
    assert empty["por_tipo_pct"] == {}


def test_summarize_realized_in_foreign_currency_without_fx():
    positions = pf.build_positions([_mv("compra", 1, 100, activo="X", moneda="USD"),
                                    _mv("venta", 1, 150, activo="X", moneda="USD", fecha="2026-02-01")])[0]
    out = pf.summarize(positions, {}, fx={})
    assert out["totals"]["pnl_realizado_eur"] == 0
    assert out["closed_positions"][0]["realizado"] == 50


# ── orquestación ─────────────────────────────────────────────────────────────

class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _hist(close):
    return pd.DataFrame({"Close": [close - 1, close]})


@pytest.mark.asyncio
async def test_fetch_prices_yahoo_coinbase_fallback_errors_and_empty():
    async def hist(session, sym, range_="2y", interval="1d"):
        assert range_ == "5d"
        if sym in ("BAD", "PEPE-USD", "ZZZ-USD"):
            raise ValueError("HTTP 404")
        return _hist(0.0000035 if sym == "TINY-EUR" else 10.0)

    async def coinbase(session, product):
        if product == "ZZZ-USD":
            raise ValueError("HTTP 404")
        return 0.0000041

    with patch.object(pf, "fetch_history", side_effect=hist), \
         patch.object(pf, "_coinbase_price", side_effect=coinbase) as cb, \
         patch.object(pf.aiohttp, "ClientSession", return_value=_FakeSession()):
        prices, issues = await pf.fetch_prices(["SPY", "BAD", "PEPE-USD", "ZZZ-USD", "TINY-EUR"])
    assert prices == {"SPY": 10.0, "PEPE-USD": 0.0000041, "TINY-EUR": 0.0000035}  # sin perder precisión
    assert {c.args[1] for c in cb.call_args_list} == {"PEPE-USD", "ZZZ-USD"}     # "BAD" no es par cripto
    msgs = {i["activo"]: i["message"] for i in issues}
    assert "HTTP 404" in msgs["BAD"] and "Coinbase: HTTP 404" in msgs["ZZZ-USD"]
    assert await pf.fetch_prices([]) == ({}, [])


class _TickerResp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


@pytest.mark.asyncio
async def test_coinbase_price_ok_and_error():
    session = MagicMock()
    session.get.return_value = _TickerResp(200, {"price": "0.0000041"})
    assert await pf._coinbase_price(session, "PEPE-USD") == 0.0000041
    assert "PEPE-USD" in session.get.call_args.args[0]
    session.get.return_value = _TickerResp(404, {})
    with pytest.raises(ValueError, match="HTTP 404"):
        await pf._coinbase_price(session, "XXX-USD")


@pytest.mark.parametrize("symbol, expected", [
    ("BTC-EUR", True), ("PEPE-USD", True), ("ETH-USDC", True), ("SAN.MC", False), ("EURUSD=X", False), ("SPY", False),
])
def test_is_crypto_pair(symbol, expected):
    assert pf._is_crypto_pair(symbol) is expected


@pytest.mark.asyncio
async def test_collect_with_csv(tmp_path):
    csv_path = tmp_path / "cartera.csv"
    csv_path.write_text(
        HEADER
        + "2026-01-10,SPY,ETF S&P 500,etf,compra,2,650,USD,1,MyInvestor,\n"
        + "2026-03-01,BTC-EUR,Bitcoin,cripto,compra,0.01,50000,EUR,0,Coinbase,\n"
        + "2026-04-01,XXX,,,regalo,1,1,EUR,,,\n",
        encoding="utf-8",
    )
    fetch = AsyncMock(return_value=({"SPY": 700.0, "BTC-EUR": 60000.0, "EURUSD=X": 1.25}, []))
    cfg = {"timezone": "Europe/Madrid", "crypto": {"max_portfolio_pct": 25}}
    with patch.object(pf, "fetch_prices", new=fetch):
        data = await pf.collect(cfg, csv_path, now=NOW)
    assert sorted(fetch.call_args.args[0]) == ["BTC-EUR", "EURUSD=X", "SPY"]
    assert data["exists"] and data["movements"] == 2 and data["last_movement"] == "2026-03-01"
    assert data["fx"] == {"USD": 1.25}
    assert data["crypto"]["tope_pct"] == 25
    assert data["totals"]["valor_eur"] == 1120 + 600
    assert any(i.get("row") == 4 for i in data["issues"])
    json.dumps(data)


def test_summarize_cash_counts_in_total_not_in_pnl_or_crypto():
    movs = [_mv("compra", 1, 100, activo="SPY", tipo="etf"),
            _mv("compra", 0.01, 10000, activo="BTC-EUR", tipo="cripto"),
            _mv("compra", 700, 1, activo="EUR", tipo="liquidez"),
            _mv("compra", 250, 1, activo="USDC", moneda="USD", tipo="liquidez", broker="Coinbase"),
            _mv("venta", 100, 1, activo="EUR", tipo="liquidez")]
    out = pf.summarize(pf.build_positions(movs)[0], {"SPY": 150.0, "BTC-EUR": 10000.0},
                       fx={"USD": 1.25}, crypto_cap_pct=25)
    t = out["totals"]
    assert t["liquidez_eur"] == 600 + 200 and t["invertido_eur"] == 250 and t["valor_eur"] == 1050
    assert t["coste_eur"] == 200 and t["pnl_no_realizado_eur"] == 50 and t["pnl_no_realizado_pct"] == 25
    assert out["liquidez"] == {"valor_eur": 800, "peso_pct": pytest.approx(76.19, abs=0.01)}
    assert out["crypto"]["peso_pct"] == pytest.approx(100 / 1050 * 100, abs=0.01)
    assert out["por_tipo_pct"]["liquidez"] == pytest.approx(76.19, abs=0.01)
    rows = {r["activo"]: r for r in out["positions"]}
    assert rows["USDC"]["precio_actual"] == 1.0 and rows["USDC"]["pnl_eur"] == 0
    assert rows["USDC"]["broker"] == "Coinbase"
    assert not out["issues"]


@pytest.mark.parametrize("today, spec, expected, days", [
    (date(2026, 11, 9), {"importe": 200, "dia": 1}, "2026-12-01", 22),
    (date(2026, 12, 1), {"importe": 200, "dia": 1}, "2026-12-01", 0),
    (date(2026, 12, 15), {"importe": 200}, "2027-01-01", 17),
    (date(2027, 2, 10), {"importe": 50, "dia": 31, "moneda": "usd"}, "2027-02-28", 18),
])
def test_next_contribution(today, spec, expected, days):
    out = pf.next_contribution(spec, today)
    assert out["proxima_fecha"] == expected and out["dias_hasta"] == days
    assert out["importe"] == spec["importe"] and out["moneda"] == spec.get("moneda", "EUR").upper()


@pytest.mark.parametrize("spec", [None, {}, {"importe": 0}])
def test_next_contribution_disabled(spec):
    assert pf.next_contribution(spec, date(2026, 11, 9)) is None


@pytest.mark.asyncio
async def test_collect_cash_is_not_priced_and_contribution_is_reported(tmp_path):
    csv_path = tmp_path / "cartera.csv"
    csv_path.write_text(
        HEADER
        + "2026-01-10,AAPL,Apple,accion,compra,1,200,USD,0,,\n"
        + "2026-11-09,EUR,Liquidez,liquidez,compra,7000,1,EUR,0,,\n"
        + "2026-11-09,USDC,USD Coin,liquidez,compra,2500,1,USD,0,Coinbase,\n",
        encoding="utf-8",
    )
    fetch = AsyncMock(return_value=({"AAPL": 250.0, "EURUSD=X": 1.25}, []))
    cfg = {"portfolio": {"aportacion_mensual": {"importe": 200, "moneda": "EUR", "dia": 1}}}
    with patch.object(pf, "fetch_prices", new=fetch):
        data = await pf.collect(cfg, csv_path, now=NOW)
    assert sorted(fetch.call_args.args[0]) == ["AAPL", "EURUSD=X"]
    assert data["totals"]["liquidez_eur"] == 7000 + 2000 and data["totals"]["invertido_eur"] == 200
    assert data["aportacion_mensual"]["proxima_fecha"] == "2026-12-01"
    assert data["issues"] == []


@pytest.mark.asyncio
async def test_collect_missing_and_empty_csv(tmp_path):
    data = await pf.collect({}, tmp_path / "nope.csv", now=NOW)
    assert data["exists"] is False and data["issues"]
    empty = tmp_path / "cartera.csv"
    empty.write_text(HEADER, encoding="utf-8")
    with patch.object(pf, "fetch_prices", new=AsyncMock(return_value=({}, []))):
        data = await pf.collect({}, empty, now=NOW)
    assert data["exists"] and data["movements"] == 0 and data["last_movement"] is None
    assert data["positions"] == []


def test_real_csv_template_is_valid():
    text = (pf._HERE / "inputs" / "cartera.csv").read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].split(",") == pf.COLUMNS
    assert pf.read_movements(text)[1] == []
    positions, issues = pf.build_positions(pf.read_movements(text)[0])
    assert issues == []
    assert {p.tipo for p in positions.values()} <= {"accion", "etf", "fondo", "cripto", "liquidez", "otro"}


def test_main_prints_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pf, "DEFAULT_DATA_DIR", tmp_path)
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "exists": True, "movements": 3,
            "positions": [{}], "totals": {"valor_eur": 100, "coste_eur": 90, "pnl_no_realizado_eur": 10},
            "crypto": {"peso_pct": 20, "tope_pct": 25},
            "issues": [{"row": 4, "message": "mal"}, {"activo": "X", "message": "sin precio"}]}
    with patch.object(pf, "collect", new=AsyncMock(return_value=fake)) as col:
        assert pf.main(["--csv", str(tmp_path / "c.csv")]) == 0
    assert col.call_args.args[1] == tmp_path / "c.csv"
    assert (tmp_path / "portfolio_2026-11-10.json").exists()
    out = capsys.readouterr().out
    assert "Liquidez" not in out and "Próxima aportación" not in out
    assert "Cripto: 20% (tope 25%)" in out and "4: mal" in out and "X: sin precio" in out


def test_main_prints_cash_and_contribution(tmp_path, capsys):
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "exists": True, "movements": 2,
            "positions": [{}], "totals": {"valor_eur": 7100, "coste_eur": 90, "pnl_no_realizado_eur": 10,
                                          "liquidez_eur": 7000},
            "aportacion_mensual": {"importe": 200, "moneda": "EUR", "proxima_fecha": "2026-12-01", "dias_hasta": 21},
            "crypto": {"peso_pct": 0, "tope_pct": 25}, "issues": []}
    with patch.object(pf, "collect", new=AsyncMock(return_value=fake)):
        pf.main(["--output", str(tmp_path / "p.json")])
    out = capsys.readouterr().out
    assert "Liquidez: 7000 EUR" in out and "Próxima aportación: 200 EUR el 2026-12-01 (en 21 días)" in out


def test_main_empty_portfolio(tmp_path, capsys):
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "exists": True, "movements": 0, "positions": [],
            "totals": {"valor_eur": 0, "coste_eur": 0, "pnl_no_realizado_eur": 0},
            "crypto": {"peso_pct": None, "tope_pct": 25}, "issues": []}
    with patch.object(pf, "collect", new=AsyncMock(return_value=fake)):
        pf.main(["--output", str(tmp_path / "p.json")])
    assert "Sin posiciones abiertas" in capsys.readouterr().out


def test_main_without_csv_uses_config_path(tmp_path, capsys):
    fake = {"generated_at_local": "2026-11-10T08:00:00+01:00", "exists": False, "issues": []}
    out = tmp_path / "p.json"
    with patch.object(pf, "collect", new=AsyncMock(return_value=fake)) as col:
        assert pf.main(["--output", str(out)]) == 0
    assert col.call_args.args[1] == pf._HERE / "inputs" / "cartera.csv"
    assert out.exists() and "recomendaciones genéricas" in capsys.readouterr().out


def test_public_brokers_only_names_coinbase():
    assert pf.public_brokers({"Coinbase", "MyInvestor", "Trade Republic", " "}) == "Coinbase, otro bróker"
    assert pf.public_brokers({"coinbase advanced"}) == "coinbase advanced"
    assert pf.public_brokers(set()) == ""
