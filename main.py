import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

from src.ingestion import NewsIngestionModule
from src.llm import LLMAnalysisModule
from src.storage import StorageLayer

load_dotenv()

KEYWORDS = ["stock market", "earnings", "Federal Reserve", "IPO", "merger", "acquisition"]
LANGUAGE = "en"


class _TextRedirector:
    """Redirige stdout al widget de log de Tkinter."""

    def __init__(self, widget: scrolledtext.ScrolledText):
        self.widget = widget

    def write(self, text: str):
        self.widget.configure(state="normal")
        self.widget.insert(tk.END, text)
        self.widget.see(tk.END)
        self.widget.configure(state="disabled")

    def flush(self):
        pass


def _run_pipeline(log: scrolledtext.ScrolledText, btn: tk.Button):
    btn.configure(state="disabled")
    old_stdout = sys.stdout
    sys.stdout = _TextRedirector(log)

    try:
        api_key = os.environ.get("GNEWS_API_KEY")
        if not api_key:
            print("[error] GNEWS_API_KEY no encontrada en .env")
            return

        ingestion = NewsIngestionModule(api_key=api_key)
        storage = StorageLayer(db_path="trading_agent.db")
        llm = LLMAnalysisModule()

        print(f"Fetching articles for: {KEYWORDS}")
        articles = ingestion.fetch_articles(keywords=KEYWORDS, language=LANGUAGE)
        print(f"Fetched {len(articles)} articles\n")

        counts = {"new": 0, "retry": 0, "analyzed": 0, "skipped": 0, "errors": 0}

        for article in articles:
            result = storage.check_and_mark(article)

            if not result.is_new and result.is_analyzed:
                print(f"[SKIP] {article.title}")
                counts["skipped"] += 1
                continue

            status = "NEW" if result.is_new else "RETRY"
            print(f"[{status}] {article.title}")
            counts["new" if result.is_new else "retry"] += 1

            try:
                llm_output = llm.analyze(title=article.title, content=article.content)
                print(
                    f"  Company: {llm_output.company} ({llm_output.ticker})"
                    f" | {llm_output.impact_level} {llm_output.impact_type}"
                    f" | confidence: {llm_output.confidence}%"
                )
                storage.save_analysis(article, llm_output.__dict__, {"sent": False})
                counts["analyzed"] += 1
            except (ValueError, RuntimeError) as e:
                print(f"  [llm error] {e}")
                counts["errors"] += 1

        print(
            f"\nSummary: {counts['new']} new | {counts['retry']} retried"
            f" | {counts['analyzed']} analyzed | {counts['skipped']} skipped"
            f" | {counts['errors']} errors"
        )
    finally:
        sys.stdout = old_stdout
        btn.configure(state="normal")


def _open_dashboard():
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "ui" / "app.py")],
        cwd=str(PROJECT_ROOT),
        startupinfo=startupinfo,
    )


def _clear_log(log: scrolledtext.ScrolledText):
    log.configure(state="normal")
    log.delete("1.0", tk.END)
    log.configure(state="disabled")


def main():
    root = tk.Tk()
    root.title("Trading AI Agent")
    root.geometry("860x520")
    root.resizable(True, True)

    # ── Toolbar ───────────────────────────────────────────────────────────────
    toolbar = tk.Frame(root, pady=8, padx=8)
    toolbar.pack(fill="x")

    log = scrolledtext.ScrolledText(root, state="disabled", font=("Consolas", 10))

    btn_run = tk.Button(
        toolbar,
        text="Ejecutar agente",
        width=20,
        height=2,
        command=lambda: threading.Thread(
            target=_run_pipeline, args=(log, btn_run), daemon=True
        ).start(),
    )
    btn_run.pack(side="left", padx=4)

    tk.Button(
        toolbar,
        text="Abrir dashboard",
        width=20,
        height=2,
        command=_open_dashboard,
    ).pack(side="left", padx=4)

    tk.Button(
        toolbar,
        text="Limpiar log",
        width=12,
        height=2,
        command=lambda: _clear_log(log),
    ).pack(side="right", padx=4)

    # ── Log ───────────────────────────────────────────────────────────────────
    log.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    root.mainloop()


if __name__ == "__main__":
    main()
