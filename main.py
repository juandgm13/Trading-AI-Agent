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
        redirector.write("[monitor] Señal de parada enviada...\n")
    else:
        # Arrancar monitor en un thread nuevo con su propio event loop
        # asyncio.Event debe crearse dentro del thread donde se ejecuta el loop
        btn_monitor.configure(text="Monitor OFF", bg="#ff6b6b")
        btn_analyze.configure(state="normal")
        redirector.write("[monitor] Iniciando...\n")

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
                            next_open_local = "desconocida"
                    else:
                        next_open_local = "desconocida"
                    market_label.configure(
                        text=f"⚠  Mercado cerrado — próxima apertura: {next_open_local}",
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
        redirector.write("[monitor] Análisis forzado solicitado — procesará en el próximo ciclo\n")
    else:
        redirector.write("[monitor] El monitor no está activo\n")


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
        "Confirmar reset",
        "¿Borrar TODA la base de datos y los archivos de log?\n\nEsta acción no se puede deshacer.",
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
    redirector.write("[reset] Base de datos borrada.\n")
    if cleared:
        redirector.write(f"[reset] Logs truncados: {', '.join(cleared)}\n")


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
        text="Analizar ahora",
        width=16,
        height=2,
        state="disabled",
        command=lambda: _force_analysis(log),
    )

    btn_monitor.configure(
        command=lambda: _toggle_monitor(log, btn_monitor, btn_analyze, market_label, root)
    )
    btn_monitor.pack(side="left", padx=4)
    btn_analyze.pack(side="left", padx=4)

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
