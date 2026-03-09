import sys
import threading
import subprocess
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

from agents.pipeline import run_pipeline_sync

load_dotenv()

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
    redirector = _TextRedirector(log)

    try:
        run_pipeline_sync(on_log=lambda msg: redirector.write(msg + "\n"))
    finally:
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
