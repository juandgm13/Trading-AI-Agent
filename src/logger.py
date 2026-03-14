"""
Sistema de logging estructurado para el Trading AI Agent.

LOG_LEVEL en .env controla la verbosidad:
  DEBUG   → todos los mensajes (ideal para desarrollo)
  INFO    → mensajes informativos normales (default)
  WARNING → solo advertencias y errores
  ERROR   → solo errores críticos

Archivos generados:
  logs/agent.log   → todos los mensajes >= level
  logs/prompts.log → prompts enviados al LLM + respuestas recibidas
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class AgentLogger:
    LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

    def __init__(
        self,
        level: str = "INFO",
        log_dir: str = "logs/",
        on_log=None,
    ):
        """
        Args:
            level:   Nivel mínimo de log (DEBUG|INFO|WARNING|ERROR).
            log_dir: Directorio donde se escriben los archivos de log.
            on_log:  Callback opcional para enviar mensajes a la UI (Tkinter).
        """
        self._on_log = on_log
        self._numeric_level = self.LEVELS.get(level.upper(), 20)

        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # ── Logger principal ───────────────────────────────────────────────────
        self._logger = logging.getLogger("agent")
        self._logger.setLevel(self._numeric_level)
        self._logger.handlers.clear()
        self._logger.propagate = False

        file_handler = RotatingFileHandler(
            log_path / "agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(self._numeric_level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        self._logger.addHandler(file_handler)

        # ── Logger de prompts (separado, siempre en DEBUG) ────────────────────
        self._prompt_logger = logging.getLogger("agent.prompts")
        self._prompt_logger.setLevel(logging.DEBUG)
        self._prompt_logger.handlers.clear()
        self._prompt_logger.propagate = False

        prompt_handler = RotatingFileHandler(
            log_path / "prompts.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        prompt_handler.setLevel(logging.DEBUG)
        prompt_handler.setFormatter(
            logging.Formatter("%(asctime)s\n%(message)s\n" + "─" * 80 + "\n")
        )
        self._prompt_logger.addHandler(prompt_handler)

    # ── Métodos de log ─────────────────────────────────────────────────────────

    def debug(self, msg: str, source: str = ""):
        full = f"[{source}] {msg}" if source else msg
        self._logger.debug(full)
        if self._numeric_level <= 10 and self._on_log:
            self._on_log(f"[DEBUG] {full}")

    def info(self, msg: str, source: str = ""):
        full = f"[{source}] {msg}" if source else msg
        self._logger.info(full)
        if self._on_log:
            self._on_log(f"[INFO] {full}")

    def warning(self, msg: str, source: str = ""):
        full = f"[{source}] {msg}" if source else msg
        self._logger.warning(full)
        if self._on_log:
            self._on_log(f"[WARNING] {full}")

    def error(self, msg: str, source: str = "", exc_info: bool = False):
        full = f"[{source}] {msg}" if source else msg
        self._logger.error(full, exc_info=exc_info)
        if self._on_log:
            self._on_log(f"[ERROR] {full}")

    @property
    def is_debug(self) -> bool:
        """Retorna True si el nivel de log activo es DEBUG."""
        return self._numeric_level <= 10

    def log_prompt(
        self,
        agent_name: str,
        prompt: str,
        response: str,
        system_prompt: str = "",
    ):
        """Registra el contenido completo enviado al LLM y la respuesta recibida."""
        parts = [f"=== {agent_name} ==="]
        if system_prompt:
            parts.append(f"SYSTEM PROMPT:\n{system_prompt}")
        parts.append(f"USER PROMPT:\n{prompt}")
        parts.append(f"RESPONSE:\n{response}")
        self._prompt_logger.debug("\n\n".join(parts))


def get_logger(on_log=None) -> AgentLogger:
    """Crea un AgentLogger con el nivel configurado en LOG_LEVEL del entorno."""
    level = os.environ.get("LOG_LEVEL", "INFO")
    return AgentLogger(level=level, on_log=on_log)
