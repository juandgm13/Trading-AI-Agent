"""
Chart generator — genera gráficos de velas como evidencia de decisiones de trading.
"""
from datetime import datetime, timezone
from pathlib import Path

import mplfinance as mpf
import pandas as pd


def generate_candlestick_chart(
    symbol: str,
    bars: list[dict],
    timeframe: str,
    event_label: str,
    charts_dir: str = "charts",
) -> str:
    """
    Genera un gráfico de velas para el símbolo con los datos OHLCV proporcionados.

    Args:
        symbol: Ticker del activo (e.g. "AAPL")
        bars: Lista de dicts con keys: timestamp, open, high, low, close, volume
        timeframe: Descripción del timeframe (e.g. "1Day", "15Min")
        event_label: Etiqueta del evento (e.g. "opportunity", "entry")
        charts_dir: Directorio donde guardar los gráficos

    Returns:
        Ruta absoluta al PNG generado.
    """

    # Crear directorio si no existe
    Path(charts_dir).mkdir(parents=True, exist_ok=True)

    # Preparar DataFrame
    if not bars:
        raise ValueError(f"No hay barras para generar gráfico de {symbol}")

    df = pd.DataFrame(bars)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp")
    df = df.set_index("timestamp")
    df = df.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()

    if df.empty:
        raise ValueError(f"DataFrame vacío tras limpiar barras para {symbol}")

    # Nombre de archivo
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{symbol}_{event_label}_{ts}.png"
    filepath = str(Path(charts_dir).resolve() / filename)

    # Generar gráfico
    title = f"{symbol} — {event_label.replace('_', ' ').title()} ({timeframe})"
    mpf.plot(
        df,
        type="candle",
        style="charles",
        title=title,
        volume=True,
        savefig=filepath,
        figsize=(12, 6),
    )

    return filepath
