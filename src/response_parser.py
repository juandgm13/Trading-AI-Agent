"""
Parsers y validadores para las respuestas JSON de los agentes LLM.

Problema: Ollama puede devolver texto en prosa en lugar de JSON puro.

Estas funciones:
  1. Extraen el primer objeto JSON del texto (directo, bloque markdown, o embebido).
  2. Validan que los campos requeridos existan y tengan valores del conjunto permitido.
  3. Aplican defaults seguros para campos opcionales o con valor incorrecto.
  4. Lanzan ParseError si la respuesta es completamente inutilizable.
"""
import json
import re


class ParseError(Exception):
    """No se pudo extraer o validar un JSON utilizable de la respuesta del modelo."""


# ── Extracción de JSON ────────────────────────────────────────────────────────

def extract_json(text: str) -> dict:
    """
    Extrae el primer objeto JSON de un texto libre.

    Estrategias (en orden de preferencia):
      1. Parseo directo del texto completo.
      2. Primer bloque ```json...``` o ```...``` de markdown.
      3. Primer {...} encontrado en el texto libre.

    Raises:
        ParseError: si ninguna estrategia produce un dict JSON válido.
    """
    if not text or not text.strip():
        raise ParseError("Respuesta vacía del modelo")

    # 1. Intento directo
    try:
        result = json.loads(text.strip())
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Bloque markdown ```json...``` o ```...```
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # 3. Primer { ... } en el texto libre (greedy hacia atrás para coger el más largo)
    for m in re.finditer(r"\{", text):
        candidate = text[m.start():]
        # Intentar parsear truncando desde el final hasta encontrar JSON válido
        for end in range(len(candidate), 0, -1):
            try:
                result = json.loads(candidate[:end])
                if isinstance(result, dict):
                    return result
                break  # pragma: no cover
            except json.JSONDecodeError:
                continue

    raise ParseError(
        f"No se encontró JSON válido en la respuesta del modelo. "
        f"Primeros 300 caracteres: {text[:300]!r}"
    )


# ── Helpers de coerción ───────────────────────────────────────────────────────

def _coerce_enum(value, valid: set, default: str) -> str:
    if isinstance(value, str) and value.upper() in valid:
        return value.upper()
    return default


def _coerce_float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value, default: int, lo: int = 0, hi: int = 100) -> int:
    try:
        v = int(value)
        return max(lo, min(hi, v))
    except (TypeError, ValueError):
        return default


def _coerce_list(value) -> list:
    return value if isinstance(value, list) else []


# ── Validadores por agente ────────────────────────────────────────────────────

def validate_technical_report(text: str) -> dict:
    """
    Extrae y valida la respuesta del TechnicalAnalysisAgent.

    Campos con default seguro si faltan o son inválidos:
      trend → NEUTRAL, signal → HOLD, target_price → None,
      stop_loss → None, confidence → 50, summary → "".

    Raises:
        ParseError: si no se puede extraer ningún JSON del texto.
    """
    data = extract_json(text)
    return {
        "trend":        _coerce_enum(data.get("trend"),  {"BULLISH", "BEARISH", "NEUTRAL", "SIDEWAYS"}, "NEUTRAL"),
        "signal":       _coerce_enum(data.get("signal"), {"BUY", "SELL", "HOLD", "WAIT"}, "HOLD"),
        "target_price": _coerce_float_or_none(data.get("target_price")),
        "stop_loss":    _coerce_float_or_none(data.get("stop_loss")),
        "confidence":   _coerce_int(data.get("confidence"), default=50),
        "summary":      str(data.get("summary", "")),
    }


def validate_market_context(text: str) -> dict:
    """
    Extrae y valida la respuesta del MarketContextAgent.

    Campos con default seguro si faltan o son inválidos:
      market_sentiment → NEUTRAL, risk_appetite → NEUTRAL,
      dominant_themes → [], macro_risks → [], confidence → 50, summary → "".

    Raises:
        ParseError: si no se puede extraer ningún JSON del texto.
    """
    data = extract_json(text)
    return {
        "market_sentiment": _coerce_enum(data.get("market_sentiment"), {"BULLISH", "BEARISH", "NEUTRAL"}, "NEUTRAL"),
        "risk_appetite":    _coerce_enum(data.get("risk_appetite"),    {"RISK_ON", "RISK_OFF", "NEUTRAL"}, "NEUTRAL"),
        "dominant_themes":  _coerce_list(data.get("dominant_themes")),
        "macro_risks":      _coerce_list(data.get("macro_risks")),
        "confidence":       _coerce_int(data.get("confidence"), default=50),
        "summary":          str(data.get("summary", "")),
    }


def validate_company_analysis(text: str) -> dict:
    """
    Extrae y valida la respuesta del CompanyAnalysisAgent.

    Campos con default seguro si faltan o son inválidos:
      sentiment → NEUTRAL, key_events → [], entry_risk → MEDIUM,
      confidence → 50, summary → "".

    Raises:
        ParseError: si no se puede extraer ningún JSON del texto.
    """
    data = extract_json(text)
    return {
        "sentiment":  _coerce_enum(data.get("sentiment"),  {"POSITIVE", "NEGATIVE", "NEUTRAL"}, "NEUTRAL"),
        "key_events": _coerce_list(data.get("key_events")),
        "entry_risk": _coerce_enum(data.get("entry_risk"), {"LOW", "MEDIUM", "HIGH"}, "MEDIUM"),
        "confidence": _coerce_int(data.get("confidence"), default=50),
        "summary":    str(data.get("summary", "")),
    }


def validate_trading_decision(text: str) -> dict:
    """
    Extrae y valida la respuesta del TradingAgent.

    El campo 'decision' es estrictamente requerido: no se puede inferir
    de forma segura si abrir o no una posición sin él.

    Campos con default seguro si faltan: symbol → "", qty → None, reasoning → "".

    Raises:
        ParseError: si no se puede extraer JSON, o si 'decision' es inválido.
    """
    data = extract_json(text)
    decision = _coerce_enum(data.get("decision", ""), {"OPEN_POSITION", "HOLD", "REJECT"}, "")
    if not decision:
        raise ParseError(
            f"Campo 'decision' ausente o inválido: {data.get('decision')!r}. "
            f"Valores válidos: OPEN_POSITION, HOLD, REJECT."
        )

    raw_qty = data.get("qty")
    qty = _coerce_int(raw_qty, default=0, lo=1, hi=10_000) if raw_qty is not None else None

    return {
        "decision":  decision,
        "symbol":    str(data.get("symbol", "")),
        "qty":       qty,
        "reasoning": str(data.get("reasoning", "")),
    }
