"""Tests de claude_agent/fetch_rates.py (tipos de interés de referencia y umbral de "tipos altos")."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from claude_agent import fetch_rates as fr

NOW = datetime(2026, 11, 18, 7, 0, tzinfo=timezone.utc)

ECB_CSV = (
    "KEY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
    "FM.M.X,M,2026-10,3.2385909,A\n"
    "FM.M.X,M,2026-09,3.247,A\n"
    "FM.M.X,M,2026-08,,A\n"
)
FRED_CSV = "observation_date,DFEDTARU\n2026-11-16,4.00\n2026-11-17,.\n2026-11-18,4.25\n"


def test_parse_ecb_csv_sorts_and_skips_empty():
    assert fr.parse_ecb_csv(ECB_CSV) == [("2026-09", 3.247), ("2026-10", 3.2385909)]


def test_parse_fred_csv_skips_missing():
    assert fr.parse_fred_csv(FRED_CSV) == [("2026-11-16", 4.0), ("2026-11-18", 4.25)]
    assert fr.parse_fred_csv("") == []


def test_summarize_with_and_without_previous():
    assert fr.summarize([("a", 1.0), ("b", 2.12345)]) == {
        "valor": 2.123, "fecha": "b", "anterior": {"valor": 1.0, "fecha": "a"}}
    assert fr.summarize([("a", 1.0)]) == {"valor": 1.0, "fecha": "a"}


@pytest.mark.parametrize("rates, ref, expected, used", [
    ({"euribor_12m": {"valor": 3.24}}, "euribor_12m", True, "euribor_12m"),
    ({"euribor_12m": {"valor": 2.5}}, "euribor_12m", False, "euribor_12m"),
    ({"bce_deposito": {"valor": 3.0}}, "euribor_12m", True, "bce_deposito"),   # cae al tipo del BCE
    ({"deuda_aaa_1a": {"valor": 1.0}}, "euribor_12m", False, "deuda_aaa_1a"),
    ({}, "euribor_12m", None, None),
])
def test_assess(rates, ref, expected, used):
    out = fr.assess(rates, 3.0, ref)
    assert out["tipos_altos"] is expected and out["referencia_usada"] == used
    assert ("umbral 3.00 %" in out["motivo"]) if used else ("sin datos" in out["motivo"])


class _Resp:
    def __init__(self, status, text):
        self.status, self._text = status, text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return self._text


@pytest.mark.asyncio
async def test_get_text_ok_http_error_and_retries(monkeypatch):
    monkeypatch.setattr(fr, "RETRY_DELAY", 0)
    session = MagicMock()
    session.get.return_value = _Resp(200, "ok")
    assert await fr._get_text(session, "u") == "ok"

    session.get.return_value = _Resp(500, "")
    with pytest.raises(ValueError, match="HTTP 500"):
        await fr._get_text(session, "u")

    session.get.side_effect = [asyncio.TimeoutError(), _Resp(200, "tarde")]
    assert await fr._get_text(session, "u") == "tarde"

    session.get.side_effect = aiohttp.ClientError("caída")
    with pytest.raises(aiohttp.ClientError):
        await fr._get_text(session, "u", attempts=2)


@pytest.mark.asyncio
async def test_fetch_series_builds_urls():
    with patch.object(fr, "_get_text", new=AsyncMock(side_effect=[ECB_CSV, FRED_CSV])) as get:
        assert (await fr.fetch_series(None, "ecb", "FM/X"))[-1] == ("2026-10", 3.2385909)
        assert (await fr.fetch_series(None, "fred", "DFEDTARU"))[-1] == ("2026-11-18", 4.25)
    urls = [c.args[1] for c in get.call_args_list]
    assert "data-api.ecb.europa.eu/service/data/FM/X" in urls[0] and "format=csvdata" in urls[0]
    assert "id=DFEDTARU" in urls[1] and "cosd=" in urls[1]


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_collect_rates_issues_and_assessment():
    async def series(session, source, key):
        if key == "DFEDTARU":
            raise ValueError("HTTP 503")
        if key.startswith("YC/"):
            return []
        return [("2026-10", 3.24)] if "EURIBOR" in key else [("2026-11-18", 2.5)]

    with patch.object(fr, "fetch_series", side_effect=series), \
         patch.object(fr.aiohttp, "ClientSession", return_value=_FakeSession()):
        data = await fr.collect({"renta_fija": {"umbral_pct": 3, "referencia": "euribor_12m"}}, now=NOW)

    assert set(data["rates"]) == {"bce_deposito", "euribor_12m"}
    assert data["rates"]["euribor_12m"]["fuente"] == "BCE"
    assert data["tipos_altos"] is True and data["umbral_pct"] == 3.0
    msgs = {i["serie"]: i["message"] for i in data["issues"]}
    assert "HTTP 503" in msgs["fed_superior"] and msgs["deuda_aaa_1a"] == "sin observaciones"
    assert data["generated_at_local"].startswith("2026-11-18T08:00")
    json.dumps(data)


@pytest.mark.asyncio
async def test_collect_defaults_without_config_block():
    with patch.object(fr, "fetch_series", new=AsyncMock(return_value=[("2026-11-17", 4.0)])), \
         patch.object(fr.aiohttp, "ClientSession", return_value=_FakeSession()):
        data = await fr.collect({}, now=NOW)
    assert data["umbral_pct"] == fr.DEFAULT_THRESHOLD and data["referencia"] == fr.DEFAULT_REFERENCE
    assert data["rates"]["fed_superior"]["fuente"] == "FRED"


def test_main_writes_json_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fr, "DEFAULT_DATA_DIR", tmp_path)
    fake = {"generated_at_local": "2026-11-18T08:00:00+01:00", "tipos_altos": True, "motivo": "m",
            "rates": {"euribor_12m": {"valor": 3.24, "fecha": "2026-10"}},
            "issues": [{"serie": "fed_superior", "message": "HTTP 503"}]}
    with patch.object(fr, "collect", new=AsyncMock(return_value=fake)):
        assert fr.main([]) == 0
    assert json.loads((tmp_path / "rates_2026-11-18.json").read_text(encoding="utf-8"))["tipos_altos"] is True
    out = capsys.readouterr().out
    assert "euribor_12m" in out and "Tipos altos: True" in out and "fed_superior: HTTP 503" in out


def test_main_explicit_output(tmp_path):
    fake = {"generated_at_local": "2026-11-18T08:00:00+01:00", "tipos_altos": None, "motivo": "sin datos",
            "rates": {}, "issues": []}
    out = tmp_path / "sub" / "r.json"
    with patch.object(fr, "collect", new=AsyncMock(return_value=fake)):
        assert fr.main(["--output", str(out)]) == 0
    assert out.exists()
