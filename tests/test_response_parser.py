"""
Tests unitarios de src/response_parser.py.

No requieren Ollama ni credenciales — todos los inputs son strings hardcodeados.
Cubren:
  - extract_json: JSON directo, bloque markdown, JSON embebido en prosa, fallo total
  - validate_technical_watching_report: nuevo schema con key_supports/key_resistances
  - validate_technical_report: campos válidos, defaults, ParseError (entry agent)
  - validate_market_context: campos válidos, defaults, ParseError
  - validate_company_analysis: campos válidos, defaults, ParseError
  - validate_trading_decision: decision requerida, defaults, ParseError (incluye OPPORTUNITY_FOUND)
"""
import json
import pytest
from src.response_parser import (
    ParseError,
    extract_json,
    validate_technical_watching_report,
    validate_technical_report,
    validate_market_context,
    validate_company_analysis,
    validate_trading_decision,
    validate_news_classification,
)


# ── extract_json ──────────────────────────────────────────────────────────────

def test_extract_json_direct():
    text = '{"key": "value", "num": 42}'
    assert extract_json(text) == {"key": "value", "num": 42}


def test_extract_json_markdown_block():
    text = 'Here is the result:\n```json\n{"trend": "BULLISH"}\n```\nDone.'
    assert extract_json(text) == {"trend": "BULLISH"}


def test_extract_json_markdown_block_no_lang():
    text = "```\n{\"signal\": \"BUY\"}\n```"
    assert extract_json(text) == {"signal": "BUY"}


def test_extract_json_embedded_in_prose():
    text = 'The analysis is complete. {"trend": "BEARISH", "confidence": 70} Hope that helps!'
    result = extract_json(text)
    assert result["trend"] == "BEARISH"
    assert result["confidence"] == 70


def test_extract_json_empty_raises():
    with pytest.raises(ParseError):
        extract_json("")


def test_extract_json_whitespace_raises():
    with pytest.raises(ParseError):
        extract_json("   \n  ")


def test_extract_json_plain_prose_raises():
    with pytest.raises(ParseError):
        extract_json("The market looks bullish today with strong momentum in tech stocks.")


def test_extract_json_invalid_json_raises():
    with pytest.raises(ParseError):
        extract_json("{trend: BULLISH, signal: BUY}")  # sin comillas


# ── validate_technical_watching_report ────────────────────────────────────────

def test_validate_technical_watching_valid():
    text = json.dumps({
        "trend": "BULLISH", "signal": "BUY",
        "key_supports": [170.0, 165.0], "key_resistances": [190.0],
        "confidence": 75, "summary": "Strong uptrend with clear levels.",
    })
    result = validate_technical_watching_report(text)
    assert result["trend"] == "BULLISH"
    assert result["signal"] == "BUY"
    assert result["key_supports"] == [170.0, 165.0]
    assert result["key_resistances"] == [190.0]
    assert result["confidence"] == 75
    assert result["summary"] == "Strong uptrend with clear levels."


def test_validate_technical_watching_defaults_on_missing_fields():
    result = validate_technical_watching_report('{"summary": "ok"}')
    assert result["trend"] == "NEUTRAL"
    assert result["signal"] == "HOLD"
    assert result["key_supports"] == []
    assert result["key_resistances"] == []
    assert result["confidence"] == 50


def test_validate_technical_watching_empty_lists():
    text = json.dumps({"trend": "SIDEWAYS", "signal": "WAIT", "key_supports": [], "key_resistances": [], "confidence": 40})
    result = validate_technical_watching_report(text)
    assert result["key_supports"] == []
    assert result["key_resistances"] == []


def test_validate_technical_watching_invalid_list_defaults_to_empty():
    text = json.dumps({"trend": "BULLISH", "signal": "BUY", "key_supports": "not_a_list", "confidence": 60})
    result = validate_technical_watching_report(text)
    assert result["key_supports"] == []
    assert result["key_resistances"] == []


def test_validate_technical_watching_no_target_stop_fields():
    """validate_technical_watching_report no debe tener target_price ni stop_loss."""
    text = json.dumps({"trend": "BULLISH", "signal": "BUY", "confidence": 70})
    result = validate_technical_watching_report(text)
    assert "target_price" not in result
    assert "stop_loss" not in result


def test_validate_technical_watching_raises_on_no_json():
    with pytest.raises(ParseError):
        validate_technical_watching_report("The stock looks bullish but no JSON here.")


# ── validate_technical_report ─────────────────────────────────────────────────

def test_validate_technical_valid():
    text = json.dumps({
        "trend": "BULLISH", "signal": "BUY",
        "target_price": 185.0, "stop_loss": 170.0,
        "confidence": 75, "summary": "Strong uptrend.",
    })
    result = validate_technical_report(text)
    assert result["trend"] == "BULLISH"
    assert result["signal"] == "BUY"
    assert result["target_price"] == pytest.approx(185.0)
    assert result["stop_loss"] == pytest.approx(170.0)
    assert result["confidence"] == 75
    assert result["summary"] == "Strong uptrend."


def test_validate_technical_defaults_on_missing_fields():
    result = validate_technical_report('{"summary": "ok"}')
    assert result["trend"] == "NEUTRAL"
    assert result["signal"] == "HOLD"
    assert result["target_price"] is None
    assert result["stop_loss"] is None
    assert result["confidence"] == 50


def test_validate_technical_invalid_enum_defaults():
    text = json.dumps({"trend": "ROCKET", "signal": "YOLO", "confidence": 80})
    result = validate_technical_report(text)
    assert result["trend"] == "NEUTRAL"
    assert result["signal"] == "HOLD"
    assert result["confidence"] == 80


def test_validate_technical_confidence_clamped():
    result = validate_technical_report('{"confidence": 150}')
    assert result["confidence"] == 100

    result = validate_technical_report('{"confidence": -10}')
    assert result["confidence"] == 0


def test_validate_technical_case_insensitive_enum():
    result = validate_technical_report('{"trend": "bullish", "signal": "buy"}')
    assert result["trend"] == "BULLISH"
    assert result["signal"] == "BUY"


def test_validate_technical_from_markdown():
    text = "Here is my analysis:\n```json\n{\"trend\": \"BEARISH\", \"signal\": \"SELL\", \"confidence\": 65, \"summary\": \"Downtrend\"}\n```"
    result = validate_technical_report(text)
    assert result["trend"] == "BEARISH"
    assert result["signal"] == "SELL"


def test_validate_technical_raises_on_no_json():
    with pytest.raises(ParseError):
        validate_technical_report("The stock looks bullish but I cannot commit to a number.")


# ── validate_market_context ───────────────────────────────────────────────────

def test_validate_market_context_valid():
    text = json.dumps({
        "market_sentiment": "BULLISH", "risk_appetite": "RISK_ON",
        "dominant_themes": ["AI growth", "Fed pause"],
        "macro_risks": ["Recession fears"],
        "confidence": 80, "summary": "Markets are optimistic.",
    })
    result = validate_market_context(text)
    assert result["market_sentiment"] == "BULLISH"
    assert result["risk_appetite"] == "RISK_ON"
    assert result["dominant_themes"] == ["AI growth", "Fed pause"]
    assert result["macro_risks"] == ["Recession fears"]
    assert result["confidence"] == 80


def test_validate_market_context_defaults():
    result = validate_market_context('{}')
    assert result["market_sentiment"] == "NEUTRAL"
    assert result["risk_appetite"] == "NEUTRAL"
    assert result["dominant_themes"] == []
    assert result["macro_risks"] == []
    assert result["confidence"] == 50
    assert result["summary"] == ""


def test_validate_market_context_invalid_lists_default_to_empty():
    text = json.dumps({"dominant_themes": "not a list", "macro_risks": 42})
    result = validate_market_context(text)
    assert result["dominant_themes"] == []
    assert result["macro_risks"] == []


def test_validate_market_context_raises_on_no_json():
    with pytest.raises(ParseError):
        validate_market_context("Global markets are trending upward.")


# ── validate_company_analysis ─────────────────────────────────────────────────

def test_validate_company_analysis_valid():
    text = json.dumps({
        "sentiment": "POSITIVE", "key_events": ["Earnings beat"],
        "entry_risk": "LOW", "confidence": 72, "summary": "Good entry.",
    })
    result = validate_company_analysis(text)
    assert result["sentiment"] == "POSITIVE"
    assert result["entry_risk"] == "LOW"
    assert result["key_events"] == ["Earnings beat"]
    assert result["confidence"] == 72


def test_validate_company_analysis_defaults():
    result = validate_company_analysis('{}')
    assert result["sentiment"] == "NEUTRAL"
    assert result["entry_risk"] == "MEDIUM"
    assert result["key_events"] == []
    assert result["confidence"] == 50


def test_validate_company_analysis_raises_on_no_json():
    with pytest.raises(ParseError):
        validate_company_analysis("The company reported strong earnings this quarter.")


# ── validate_trading_decision ─────────────────────────────────────────────────

def test_validate_trading_decision_open_position():
    text = json.dumps({
        "decision": "OPEN_POSITION", "symbol": "AAPL",
        "qty": 5, "reasoning": "Strong signal.",
    })
    result = validate_trading_decision(text)
    assert result["decision"] == "OPEN_POSITION"
    assert result["symbol"] == "AAPL"
    assert result["qty"] == 5
    assert result["reasoning"] == "Strong signal."


def test_validate_trading_decision_hold():
    text = json.dumps({"decision": "HOLD", "symbol": "MSFT", "qty": None, "reasoning": "Waiting."})
    result = validate_trading_decision(text)
    assert result["decision"] == "HOLD"
    assert result["qty"] is None


def test_validate_trading_decision_reject():
    text = json.dumps({"decision": "REJECT", "symbol": "NVDA", "qty": None, "reasoning": "Bad signal."})
    result = validate_trading_decision(text)
    assert result["decision"] == "REJECT"


def test_validate_trading_decision_missing_decision_raises():
    text = json.dumps({"symbol": "AAPL", "qty": 5, "reasoning": "ok"})
    with pytest.raises(ParseError, match="decision"):
        validate_trading_decision(text)


def test_validate_trading_decision_invalid_decision_raises():
    text = json.dumps({"decision": "MAYBE", "symbol": "AAPL"})
    with pytest.raises(ParseError, match="decision"):
        validate_trading_decision(text)


def test_validate_trading_decision_defaults_optional_fields():
    text = json.dumps({"decision": "HOLD"})
    result = validate_trading_decision(text)
    assert result["symbol"] == ""
    assert result["qty"] is None
    assert result["reasoning"] == ""


def test_validate_trading_decision_opportunity_found():
    text = json.dumps({"decision": "OPPORTUNITY_FOUND", "symbol": "AAPL", "reasoning": "Strong trend."})
    result = validate_trading_decision(text)
    assert result["decision"] == "OPPORTUNITY_FOUND"
    assert result["symbol"] == "AAPL"


def test_validate_trading_decision_case_insensitive():
    text = json.dumps({"decision": "open_position", "symbol": "TSLA", "qty": 3})
    result = validate_trading_decision(text)
    assert result["decision"] == "OPEN_POSITION"


def test_validate_trading_decision_raises_on_no_json():
    with pytest.raises(ParseError):
        validate_trading_decision("I think you should buy this stock immediately.")


# ── Casos de cobertura adicional ──────────────────────────────────────────────

def test_extract_json_markdown_block_with_invalid_json_falls_through_to_prose():
    """
    Bloque ```json cuyo contenido tiene { y } pero JSON inválido (clave sin comillas)
    → captura el JSONDecodeError (L52-53) → fallback al JSON en el texto libre.
    """
    text = '```json\n{unquoted_key: "value"}\n```\nFallback: {"decision": "HOLD"}'
    result = extract_json(text)
    assert result["decision"] == "HOLD"


def test_coerce_float_or_none_returns_none_on_unconvertible_value():
    """_coerce_float_or_none devuelve None cuando el valor no es numérico."""
    from src.response_parser import _coerce_float_or_none
    assert _coerce_float_or_none("not-a-number") is None
    assert _coerce_float_or_none([1, 2, 3]) is None
    assert _coerce_float_or_none({"key": "val"}) is None


def test_validate_trading_decision_embedded_in_prose():
    text = 'After careful analysis: {"decision": "HOLD", "symbol": "GOOG", "qty": null, "reasoning": "Mixed signals"} End of report.'
    result = validate_trading_decision(text)
    assert result["decision"] == "HOLD"
    assert result["symbol"] == "GOOG"


# ── validate_news_classification ──────────────────────────────────────────────

def test_validate_news_classification_company():
    text = json.dumps({
        "scope": "company", "ticker": "AAPL", "exchange": None,
        "impact": "HIGH", "reasoning": "Apple earnings beat.",
    })
    result = validate_news_classification(text)
    assert result["scope"] == "company"
    assert result["ticker"] == "AAPL"
    assert result["exchange"] is None
    assert result["impact"] == "HIGH"
    assert result["reasoning"] == "Apple earnings beat."


def test_validate_news_classification_irrelevant_impact_is_null():
    text = json.dumps({
        "scope": "irrelevant", "ticker": None, "exchange": None,
        "impact": "HIGH",  # debería ignorarse y quedar None
        "reasoning": "Sports news.",
    })
    result = validate_news_classification(text)
    assert result["scope"] == "irrelevant"
    assert result["impact"] is None


def test_validate_news_classification_defaults_on_missing_fields():
    result = validate_news_classification('{}')
    assert result["scope"] == "irrelevant"
    assert result["ticker"] is None
    assert result["exchange"] is None
    assert result["impact"] is None
    assert result["reasoning"] == ""


def test_validate_news_classification_invalid_scope_defaults_to_irrelevant():
    text = json.dumps({"scope": "global", "impact": "MEDIUM"})
    result = validate_news_classification(text)
    assert result["scope"] == "irrelevant"
    assert result["impact"] is None


def test_validate_news_classification_article_summary_present():
    """validate_news_classification extrae article_summary cuando está presente."""
    text = json.dumps({
        "scope": "market", "ticker": None, "exchange": None,
        "impact": "HIGH", "reasoning": "Fed hike.",
        "article_summary": "Fed raised rates by 25bp. Markets reacted positively.",
    })
    result = validate_news_classification(text)
    assert result["article_summary"] == "Fed raised rates by 25bp. Markets reacted positively."


def test_validate_news_classification_article_summary_absent_defaults_to_none():
    """validate_news_classification retorna article_summary=None cuando no está en el JSON."""
    text = json.dumps({
        "scope": "market", "ticker": None, "exchange": None,
        "impact": "LOW", "reasoning": "Routine.",
    })
    result = validate_news_classification(text)
    assert result["article_summary"] is None


def test_validate_news_classification_article_summary_null_string_normalizes_to_none():
    """validate_news_classification normaliza 'null' string a None en article_summary."""
    text = json.dumps({
        "scope": "company", "ticker": "TSLA", "exchange": None,
        "impact": "MEDIUM", "reasoning": "Minor news.",
        "article_summary": None,
    })
    result = validate_news_classification(text)
    assert result["article_summary"] is None
