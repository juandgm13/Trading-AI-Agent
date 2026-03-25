import asyncio
import sys
import threading
import subprocess
import tkinter as tk
from datetime import datetime
from tkinter import scrolledtext, messagebox
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

from agents.monitor import run_monitor_sync

load_dotenv()

# Estado compartido del monitor
_monitor_stop_event: asyncio.Event | None = None
_monitor_force_event: asyncio.Event | None = None
_monitor_thread: threading.Thread | None = None


class _TextRedirector:
    """Redirige mensajes de log al widget ScrolledText de Tkinter."""

    def __init__(self, widget: scrolledtext.ScrolledText):
        self.widget = widget

    def write(self, text: str):
        self.widget.configure(state="normal")
        if text and text.strip():
            ts = datetime.now().strftime("%H:%M:%S")
            self.widget.insert(tk.END, f"[{ts}] {text}")
        else:
            self.widget.insert(tk.END, text)
        self.widget.see(tk.END)
        self.widget.configure(state="disabled")

    def flush(self):
        pass


def _toggle_monitor(
    log: scrolledtext.ScrolledText,
    btn_monitor: tk.Button,
    btn_analyze: tk.Button,
    market_label: tk.Label,
    root: tk.Tk,
):
    global _monitor_stop_event, _monitor_force_event, _monitor_thread

    redirector = _TextRedirector(log)

    if _monitor_thread and _monitor_thread.is_alive():
        # Detener monitor
        if _monitor_stop_event:
            _monitor_stop_event.set()
        btn_monitor.configure(text="Monitor ON", bg="SystemButtonFace")
        btn_analyze.configure(state="disabled")
        redirector.write("[monitor] Stop signal sent...\n")
    else:
        # Arrancar monitor en un thread nuevo con su propio event loop
        # asyncio.Event debe crearse dentro del thread donde se ejecuta el loop
        btn_monitor.configure(text="Monitor OFF", bg="#ff6b6b")
        btn_analyze.configure(state="normal")
        redirector.write("[monitor] Starting...\n")

        def _on_market_status(is_open: bool, next_open: str | None):
            """Callback invocado desde el thread del monitor — actualiza el label en el thread de Tkinter."""
            def _update():
                if is_open:
                    market_label.configure(text="", bg="SystemButtonFace")
                else:
                    if next_open:
                        try:
                            from datetime import datetime
                            next_open_utc = datetime.fromisoformat(next_open.replace("Z", "+00:00"))
                            next_open_local = next_open_utc.astimezone().strftime("%H:%M %Z")
                        except Exception:
                            next_open_local = "unknown"
                    else:
                        next_open_local = "unknown"
                    market_label.configure(
                        text=f"⚠  Market closed — next open: {next_open_local}",
                        bg="#fff3cd",
                    )
            root.after(0, _update)

        def monitor_thread_fn():
            global _monitor_stop_event, _monitor_force_event
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _monitor_stop_event = asyncio.Event()
            _monitor_force_event = asyncio.Event()

            def on_log(msg):
                redirector.write(msg + "\n")

            try:
                loop.run_until_complete(
                    __import__("agents.monitor", fromlist=["run_monitor"]).run_monitor(
                        on_log=on_log,
                        stop_event=_monitor_stop_event,
                        force_event=_monitor_force_event,
                        market_status_callback=_on_market_status,
                    )
                )
            finally:
                loop.close()
                btn_monitor.configure(text="Monitor ON", bg="SystemButtonFace")
                btn_analyze.configure(state="disabled")
                root.after(0, lambda: market_label.configure(text="", bg="SystemButtonFace"))

        _monitor_thread = threading.Thread(target=monitor_thread_fn, daemon=True)
        _monitor_thread.start()


def _force_analysis(log: scrolledtext.ScrolledText):
    """Envía señal de análisis inmediato al monitor activo."""
    global _monitor_force_event
    redirector = _TextRedirector(log)

    if _monitor_force_event and _monitor_thread and _monitor_thread.is_alive():
        _monitor_force_event.set()
        redirector.write("[monitor] Forced analysis requested — will run next cycle\n")
    else:
        redirector.write("[monitor] Monitor is not active\n")


def _open_dashboard():
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "ui" / "app.py")],
        cwd=str(PROJECT_ROOT),
        startupinfo=startupinfo,
    )


def _reset_db_and_logs(log: scrolledtext.ScrolledText):
    """Borra todos los datos de la BD y trunca los archivos de log."""
    if not messagebox.askyesno(
        "Confirm reset",
        "Delete ALL database data and log files?\n\nThis action cannot be undone.",
        icon="warning",
    ):
        return

    from src.storage import StorageLayer
    db_path = PROJECT_ROOT / "trading_agent.db"
    try:
        StorageLayer(str(db_path)).reset_all()
    except Exception as e:
        messagebox.showerror("Error", f"Error al resetear la BD:\n{e}")
        return

    log_dir = PROJECT_ROOT / "logs"
    cleared = []
    for log_file in log_dir.glob("*.log*"):
        try:
            log_file.write_text("", encoding="utf-8")
            cleared.append(log_file.name)
        except Exception:
            pass

    redirector = _TextRedirector(log)
    redirector.write("[reset] Database cleared.\n")
    if cleared:
        redirector.write(f"[reset] Logs cleared: {', '.join(cleared)}\n")


def _run_news_ingestion(log: scrolledtext.ScrolledText):
    """Ejecuta fetch + clasificación de noticias de mercado en un thread propio."""
    redirector = _TextRedirector(log)

    def _thread_fn():
        import os
        import asyncio
        from agent_framework.config.config_loader import load_from_yaml
        from agent_framework.events import EventBus
        from agent_framework.models.model_registry import ModelRegistry
        from agent_framework.providers import OllamaProvider
        from src.storage import StorageLayer
        from src.news_sources import AlpacaNewsSource, RSSNewsSource, YahooSymbolSource, NewsAggregator
        from src.news_service import NewsIngestionService
        from agents.economics_agent import build_news_classifier_agent

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        from src.logger import get_logger
        logger = get_logger(on_log=lambda msg: redirector.write(msg + "\n"))

        async def _run():
            logger.info("Iniciando ingesta de noticias de mercado...", source="news")

            api_key = os.environ.get("ALPACA_API_KEY", "")
            secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
            ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

            logger.info("Cargando configuración de modelos...", source="news")
            config = load_from_yaml(PROJECT_ROOT / "agents" / "trading_agent.yaml")

            model_registry = ModelRegistry()
            cfg = config.models.get("news_classifier")
            if cfg:
                logger.info(f"Modelo clasificador: {cfg.model} ({ollama_url})", source="news")
                model_registry.register("news_classifier", OllamaProvider(cfg.model, base_url=ollama_url))
            else:
                logger.warning("No se encontró configuración 'news_classifier' en YAML", source="news")

            bus = EventBus()

            logger.info("Inicializando fuentes: Alpaca News, RSS, Yahoo Finance...", source="news")
            alpaca_news = AlpacaNewsSource(api_key, secret_key, logger=logger)
            rss_source = RSSNewsSource(logger=logger)
            yahoo_source = YahooSymbolSource(logger=logger)
            aggregator = NewsAggregator(alpaca_news, rss_source, yahoo_source, logger=logger)

            logger.info("Construyendo agente clasificador...", source="news")
            news_classifier_agent = await build_news_classifier_agent(config, bus, model_registry)

            storage = StorageLayer(str(PROJECT_ROOT / "trading_agent.db"))
            news_service = NewsIngestionService(
                aggregator, news_classifier_agent, storage, logger=logger
            )

            logger.info("Obteniendo artículos de mercado (Alpaca + RSS)...", source="news")
            fetched, classified = await news_service.run_market_ingestion()
            logger.info(
                f"Completado — {fetched} artículos obtenidos, {classified} clasificados.",
                source="news",
            )

        try:
            loop.run_until_complete(_run())
        except Exception as e:
            logger.error(str(e), source="news")
        finally:
            loop.close()

    threading.Thread(target=_thread_fn, daemon=True).start()


def _run_market_context_analysis(log: scrolledtext.ScrolledText):
    """Ejecuta fetch + clasificación de noticias + análisis de contexto global de mercado (MarketContextAgent)."""
    redirector = _TextRedirector(log)

    def _thread_fn():
        import os
        import json
        import asyncio
        from agent_framework.config.config_loader import load_from_yaml
        from agent_framework.events import EventBus
        from agent_framework.models.model_registry import ModelRegistry
        from agent_framework.providers import OllamaProvider
        from src.storage import StorageLayer
        from src.news_sources import AlpacaNewsSource, RSSNewsSource, YahooSymbolSource, NewsAggregator
        from src.news_service import NewsIngestionService
        from src.response_parser import validate_market_context
        from agents.economics_agent import (
            build_news_classifier_agent,
            build_market_context_agent,
            build_market_context_prompt,
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        from src.logger import get_logger
        logger = get_logger(on_log=lambda msg: redirector.write(msg + "\n"))

        async def _run():
            logger.info("Iniciando análisis de contexto global de mercado...", source="market")

            api_key = os.environ.get("ALPACA_API_KEY", "")
            secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
            ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

            config = load_from_yaml(PROJECT_ROOT / "agents" / "trading_agent.yaml")
            model_registry = ModelRegistry()
            bus = EventBus()

            for role in ("news_classifier", "economics"):
                cfg = config.models.get(role)
                if cfg:
                    model_registry.register(role, OllamaProvider(cfg.model, base_url=ollama_url))
                else:
                    logger.warning(f"No se encontró configuración '{role}' en YAML", source="market")

            alpaca_news = AlpacaNewsSource(api_key, secret_key, logger=logger)
            rss_source = RSSNewsSource(logger=logger)
            yahoo_source = YahooSymbolSource(logger=logger)
            aggregator = NewsAggregator(alpaca_news, rss_source, yahoo_source, logger=logger)

            news_classifier_agent = await build_news_classifier_agent(config, bus, model_registry)
            storage = StorageLayer(str(PROJECT_ROOT / "trading_agent.db"))
            news_service = NewsIngestionService(aggregator, news_classifier_agent, storage, logger=logger)

            logger.info("Obteniendo y clasificando noticias de mercado...", source="market")
            fetched, classified = await news_service.run_market_ingestion()
            logger.info(f"Noticias: {fetched} obtenidas, {classified} clasificadas.", source="market")

            market_articles = storage.get_articles_by_scope("market", limit=20)
            high_impact_company = storage.get_articles_by_scope("company", limit=10)
            all_context_news = market_articles + high_impact_company
            logger.info(
                f"Construyendo contexto con {len(all_context_news)} artículos "
                f"({len(market_articles)} market + {len(high_impact_company)} company)...",
                source="market",
            )

            market_context_agent = await build_market_context_agent(config, bus, model_registry)
            prompt = build_market_context_prompt(all_context_news)
            response = await market_context_agent.run(prompt)
            report = validate_market_context(response)
            storage.save_market_context(json.dumps(report), len(all_context_news))

            logger.info(
                f"Contexto global actualizado — sentiment: {report.get('market_sentiment', '?')} | "
                f"risk: {report.get('risk_appetite', '?')} | "
                f"confidence: {report.get('confidence', '?')}",
                source="market",
            )

        try:
            loop.run_until_complete(_run())
        except Exception as e:
            logger.error(str(e), source="market")
        finally:
            loop.close()

    threading.Thread(target=_thread_fn, daemon=True).start()


def _clear_log(log: scrolledtext.ScrolledText):
    log.configure(state="normal")
    log.delete("1.0", tk.END)
    log.configure(state="disabled")


def main():
    root = tk.Tk()
    root.title("Trading AI Agent")
    root.geometry("960x540")
    root.resizable(True, True)

    # ── Toolbar ───────────────────────────────────────────────────────────────
    toolbar = tk.Frame(root, pady=8, padx=8)
    toolbar.pack(fill="x")

    # ── Label de estado del mercado ───────────────────────────────────────────
    market_label = tk.Label(
        root,
        text="",
        font=("Arial", 10, "bold"),
        anchor="w",
        padx=8,
        pady=4,
    )
    market_label.pack(fill="x")

    log = scrolledtext.ScrolledText(root, state="disabled", font=("Consolas", 10))

    btn_monitor = tk.Button(
        toolbar,
        text="Monitor ON",
        width=14,
        height=2,
    )

    btn_analyze = tk.Button(
        toolbar,
        text="Analyze now",
        width=16,
        height=2,
        state="disabled",
        command=lambda: _force_analysis(log),
    )

    btn_news = tk.Button(
        toolbar,
        text="Analyze news",
        width=14,
        height=2,
        command=lambda: _run_news_ingestion(log),
    )

    btn_market_ctx = tk.Button(
        toolbar,
        text="Market context",
        width=15,
        height=2,
        command=lambda: _run_market_context_analysis(log),
    )

    btn_monitor.configure(
        command=lambda: _toggle_monitor(log, btn_monitor, btn_analyze, market_label, root)
    )
    btn_monitor.pack(side="left", padx=4)
    btn_analyze.pack(side="left", padx=4)
    btn_news.pack(side="left", padx=4)
    btn_market_ctx.pack(side="left", padx=4)

    tk.Button(
        toolbar,
        text="Open dashboard",
        width=18,
        height=2,
        command=_open_dashboard,
    ).pack(side="left", padx=4)

    tk.Button(
        toolbar,
        text="Clear log",
        width=12,
        height=2,
        command=lambda: _clear_log(log),
    ).pack(side="right", padx=4)

    tk.Button(
        toolbar,
        text="Reset BD + Logs",
        width=15,
        height=2,
        fg="white",
        bg="#c0392b",
        activebackground="#e74c3c",
        activeforeground="white",
        command=lambda: _reset_db_and_logs(log),
    ).pack(side="right", padx=4)

    # ── Log ───────────────────────────────────────────────────────────────────
    log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    root.mainloop()


if __name__ == "__main__":
    main()
