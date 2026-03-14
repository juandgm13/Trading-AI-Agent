"""
Tests unitarios de AgentLogger y get_logger.

No requieren credenciales ni red. Cubren:
  - Métodos debug/info/warning/error con y sin on_log callback
  - Prefijo [source] en mensajes
  - log_prompt con y sin system_prompt
  - get_logger con y sin LOG_LEVEL en entorno
"""
import os
import pytest
from unittest.mock import MagicMock


# ── Helpers ────────────────────────────────────────────────────────────────────

def _logger(level="INFO", on_log=None):
    from src.logger import AgentLogger
    return AgentLogger(level=level, on_log=on_log)


# ── on_log callback ────────────────────────────────────────────────────────────

def test_info_calls_on_log():
    calls = []
    logger = _logger("INFO", on_log=calls.append)
    logger.info("hello")
    assert len(calls) == 1
    assert "[INFO]" in calls[0]
    assert "hello" in calls[0]


def test_info_with_source_prefixes_message():
    calls = []
    logger = _logger("INFO", on_log=calls.append)
    logger.info("msg", source="monitor")
    assert "[monitor]" in calls[0]
    assert "msg" in calls[0]


def test_warning_calls_on_log():
    calls = []
    logger = _logger("INFO", on_log=calls.append)
    logger.warning("watch out")
    assert any("[WARNING]" in c for c in calls)
    assert any("watch out" in c for c in calls)


def test_error_calls_on_log():
    calls = []
    logger = _logger("INFO", on_log=calls.append)
    logger.error("boom")
    assert any("[ERROR]" in c for c in calls)
    assert any("boom" in c for c in calls)


def test_debug_calls_on_log_when_level_is_debug():
    calls = []
    logger = _logger("DEBUG", on_log=calls.append)
    logger.debug("verbose detail")
    assert any("[DEBUG]" in c for c in calls)
    assert any("verbose detail" in c for c in calls)


def test_debug_does_not_call_on_log_when_level_is_info():
    calls = []
    logger = _logger("INFO", on_log=calls.append)
    logger.debug("should not appear")
    # on_log solo se llama para mensajes >= level configurado
    assert not any("should not appear" in c for c in calls)


def test_no_on_log_does_not_raise():
    logger = _logger("INFO")  # sin callback
    logger.info("silent")
    logger.warning("silent")
    logger.error("silent")
    logger.debug("silent")


# ── log_prompt ─────────────────────────────────────────────────────────────────

def test_log_prompt_without_system_prompt(tmp_path):
    from src.logger import AgentLogger
    logger = AgentLogger(level="DEBUG", log_dir=str(tmp_path))
    # No debe lanzar excepción
    logger.log_prompt(
        agent_name="TechnicalAgent",
        prompt="Analyze AAPL",
        response='{"trend": "BULLISH"}',
    )
    prompt_log = tmp_path / "prompts.log"
    content = prompt_log.read_text(encoding="utf-8")
    assert "TechnicalAgent" in content
    assert "Analyze AAPL" in content
    assert "BULLISH" in content
    assert "SYSTEM PROMPT" not in content


def test_log_prompt_with_system_prompt(tmp_path):
    from src.logger import AgentLogger
    logger = AgentLogger(level="DEBUG", log_dir=str(tmp_path))
    logger.log_prompt(
        agent_name="TradingAgent",
        prompt="Should I buy?",
        response='{"decision": "HOLD"}',
        system_prompt="You are a trading assistant.",
    )
    content = (tmp_path / "prompts.log").read_text(encoding="utf-8")
    assert "SYSTEM PROMPT" in content
    assert "You are a trading assistant." in content
    assert "decision" in content


# ── get_logger ─────────────────────────────────────────────────────────────────

def test_get_logger_uses_log_level_env(monkeypatch):
    from src.logger import get_logger
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    logger = get_logger()
    assert logger.is_debug is True


def test_get_logger_defaults_to_info_when_no_env(monkeypatch):
    from src.logger import get_logger
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    logger = get_logger()
    assert logger.is_debug is False


def test_get_logger_passes_on_log_callback():
    from src.logger import get_logger
    calls = []
    logger = get_logger(on_log=calls.append)
    logger.info("test callback")
    assert any("test callback" in c for c in calls)


# ── Niveles desconocidos ───────────────────────────────────────────────────────

def test_unknown_level_defaults_to_info():
    from src.logger import AgentLogger
    logger = AgentLogger(level="VERBOSE")  # nivel desconocido
    assert logger.is_debug is False
    assert logger._numeric_level == 20  # INFO por defecto
