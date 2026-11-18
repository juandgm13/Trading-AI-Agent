"""
Tipos de interés de referencia para el informe diario pre-apertura.

Descarga, de fuentes públicas sin credenciales:
- API de datos del BCE: tipo de la facilidad de depósito (DFR), Euríbor a 12 meses (media mensual) y
  rendimiento al contado a 1 año de la deuda AAA de la zona euro (referencia de un tipo fijo a 1 año);
- FRED (Reserva Federal de St. Louis): límite superior del tipo oficial de la Fed.

Decide si los tipos están "altos" comparando la referencia elegida en `config.yaml`
(`renta_fija.referencia`, por defecto el Euríbor a 12 meses) con `renta_fija.umbral_pct`. Si lo están,
el informe incluye el apartado "Dónde aparcar la liquidez a tipo fijo".

Uso:
    python claude_agent/fetch_rates.py            # → data/rates_FECHA.json
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # permite ejecutar el script directamente
    sys.path.insert(0, str(_HERE.parent))

from claude_agent.fetch_news import DEFAULT_CONFIG, load_config  # noqa: E402

DEFAULT_DATA_DIR = _HERE / "data"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/{key}?lastNObservations={n}&format=csvdata"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
_TIMEOUT = 15
RETRY_DELAY = 2
DEFAULT_THRESHOLD = 3.0
DEFAULT_REFERENCE = "euribor_12m"

# nombre → (fuente, serie, descripción)
SERIES: dict[str, tuple[str, str, str]] = {
    "bce_deposito": ("ecb", "FM/D.U2.EUR.4F.KR.DFR.LEV", "Tipo de la facilidad de depósito del BCE"),
    "euribor_12m": ("ecb", "FM/M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA", "Euríbor a 12 meses (media mensual)"),
    "deuda_aaa_1a": ("ecb", "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y",
                     "Rendimiento a 1 año de la deuda AAA de la zona euro"),
    "fed_superior": ("fred", "DFEDTARU", "Límite superior del tipo oficial de la Fed"),
}


def parse_ecb_csv(text: str) -> list[tuple[str, float]]:
    """CSV de la API del BCE → [(periodo, valor)] en orden cronológico (ignora valores vacíos)."""
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        value = (row.get("OBS_VALUE") or "").strip()
        if value:
            rows.append((row["TIME_PERIOD"], float(value)))
    return sorted(rows)


def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    """CSV de FRED (fecha, valor; '.' = sin dato) → [(fecha, valor)] en orden cronológico."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    next(reader, None)  # cabecera
    for row in reader:
        if len(row) >= 2 and row[1].strip() not in ("", "."):
            rows.append((row[0], float(row[1])))
    return sorted(rows)


async def _get_text(session: aiohttp.ClientSession, url: str, attempts: int = 3) -> str:
    """GET con reintentos: FRED corta a veces la conexión o tarda en responder."""
    for attempt in range(attempts):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=_TIMEOUT)) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}")
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    raise RuntimeError("unreachable")  # pragma: no cover


async def fetch_series(session: aiohttp.ClientSession, source: str, series: str) -> list[tuple[str, float]]:
    """Últimas observaciones de una serie (BCE: las 40 últimas; FRED: los últimos ~90 días)."""
    if source == "ecb":
        return parse_ecb_csv(await _get_text(session, ECB_URL.format(key=series, n=40)))
    start = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
    return parse_fred_csv(await _get_text(session, FRED_URL.format(series=series, start=start)))


def summarize(observations: list[tuple[str, float]]) -> dict:
    """Último valor, su fecha y el cambio frente a la observación de hace ~1 mes / la anterior."""
    period, value = observations[-1]
    out = {"valor": round(value, 3), "fecha": period}
    if len(observations) >= 2:
        out["anterior"] = {"valor": round(observations[-2][1], 3), "fecha": observations[-2][0]}
    return out


def assess(rates: dict, threshold: float, reference: str) -> dict:
    """¿Tipos altos? Compara la referencia con el umbral; si falta, prueba con el tipo del BCE."""
    for key in (reference, "bce_deposito", "deuda_aaa_1a"):
        if key in rates:
            value = rates[key]["valor"]
            altos = value >= threshold
            cmp = "≥" if altos else "<"
            return {"tipos_altos": altos, "referencia_usada": key,
                    "motivo": f"{SERIES[key][2]}: {value:.2f} % {cmp} umbral {threshold:.2f} %"}
    return {"tipos_altos": None, "referencia_usada": None,
            "motivo": "sin datos de tipos: comprobar con una búsqueda web"}


async def collect(cfg: dict, now: datetime | None = None) -> dict:
    tz = ZoneInfo(cfg.get("timezone", "Europe/Madrid"))
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(tz)
    rf = cfg.get("renta_fija") or {}
    threshold = float(rf.get("umbral_pct", DEFAULT_THRESHOLD))
    reference = rf.get("referencia", DEFAULT_REFERENCE)

    async with aiohttp.ClientSession(trust_env=True) as session:
        results = await asyncio.gather(
            *(fetch_series(session, src, ser) for src, ser, _ in SERIES.values()), return_exceptions=True
        )

    rates: dict[str, dict] = {}
    issues: list[dict] = []
    for (name, (src, ser, desc)), res in zip(SERIES.items(), results):
        if isinstance(res, Exception):
            issues.append({"serie": name, "message": f"{type(res).__name__}: {res}"})
        elif not res:
            issues.append({"serie": name, "message": "sin observaciones"})
        else:
            rates[name] = {"descripcion": desc, "fuente": "BCE" if src == "ecb" else "FRED", **summarize(res)}

    return {
        "generated_at": now.isoformat(),
        "generated_at_local": now_local.isoformat(),
        "source": "API de datos del BCE + FRED (St. Louis Fed)",
        "umbral_pct": threshold,
        "referencia": reference,
        **assess(rates, threshold, reference),
        "rates": rates,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tipos de interés de referencia (BCE, Euríbor, Fed).")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    data = asyncio.run(collect(load_config(args.config)))
    date = datetime.fromisoformat(data["generated_at_local"]).date().isoformat()
    out = Path(args.output) if args.output else DEFAULT_DATA_DIR / f"rates_{date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Tipos guardados en {out}")
    for name, r in data["rates"].items():
        print(f"  {name:13} {r['valor']:>7} % ({r['fecha']})")
    print(f"  Tipos altos: {data['tipos_altos']} — {data['motivo']}")
    for issue in data["issues"]:
        print(f"  ⚠ {issue['serie']}: {issue['message']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
