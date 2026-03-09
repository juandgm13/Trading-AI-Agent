import asyncio
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

from agents.pipeline import run_pipeline_sync
from agents.monitor import run_monitor_sync

load_dotenv()

# Stop event compartido para el monitor
_monitor_stop_event: asyncio.Event | None = None
_monitor_thread: threading.Thread | None = None


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


def _toggle_monitor(log: scrolledtext.ScrolledText, btn: tk.Button):
    global _monitor_stop_event, _monitor_thread

    redirector = _TextRedirector(log)

    if _monitor_thread and _monitor_thread.is_alive():
        # Detener monitor
        if _monitor_stop_event:
            _monitor_stop_event.set()
        btn.configure(text="Monitor ON", bg="SystemButtonFace")
        redirector.write("[monitor] Señal de parada enviada...\n")
    else:
        # Arrancar monitor en un thread nuevo con su propio event loop
        # asyncio.Event debe crearse dentro del thread donde se ejecuta el loop
        btn.configure(text="Monitor OFF", bg="#ff6b6b")
        redirector.write("[monitor] Iniciando...\n")

        def monitor_thread_fn():
            global _monitor_stop_event
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _monitor_stop_event = asyncio.Event()

            def on_log(msg):
                redirector.write(msg + "\n")

            try:
                loop.run_until_complete(
                    __import__("agents.monitor", fromlist=["run_monitor"]).run_monitor(
                        on_log=on_log,
                        stop_event=_monitor_stop_event,
                    )
                )
            finally:
                loop.close()
                btn.configure(text="Monitor ON", bg="SystemButtonFace")

        _monitor_thread = threading.Thread(target=monitor_thread_fn, daemon=True)
        _monitor_thread.start()


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
    root.geometry("960x540")
    root.resizable(True, True)

    # ── Toolbar ───────────────────────────────────────────────────────────────
    toolbar = tk.Frame(root, pady=8, padx=8)
    toolbar.pack(fill="x")

    log = scrolledtext.ScrolledText(root, state="disabled", font=("Consolas", 10))

    btn_run = tk.Button(
        toolbar,
        text="Ejecutar agente",
        width=18,
        height=2,
        command=lambda: threading.Thread(
            target=_run_pipeline, args=(log, btn_run), daemon=True
        ).start(),
    )
    btn_run.pack(side="left", padx=4)

    btn_monitor = tk.Button(
        toolbar,
        text="Monitor ON",
        width=14,
        height=2,
        command=lambda: _toggle_monitor(log, btn_monitor),
    )
    btn_monitor.pack(side="left", padx=4)

    tk.Button(
        toolbar,
        text="Abrir dashboard",
        width=18,
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
