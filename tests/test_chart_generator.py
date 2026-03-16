"""
Tests del módulo src/chart_generator.py.

Verifican la generación de gráficos de velas OHLCV.
mplfinance se importa al nivel del módulo, por lo que se parchea
con patch("src.chart_generator.mpf").
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _sample_bars(n=5):
    """Helper: genera n barras OHLCV mínimas con timestamps diarios válidos."""
    bars = []
    for i in range(n):
        bars.append({
            "timestamp": f"2025-01-{i+2:02d}T00:00:00Z",
            "open": 170.0 + i,
            "high": 175.0 + i,
            "low": 169.0 + i,
            "close": 173.0 + i,
            "volume": 1_000_000 + i * 100_000,
        })
    return bars


# ── Tests con mplfinance mockeado ─────────────────────────────────────────────

def test_generate_chart_calls_mpf_plot(tmp_path):
    """generate_candlestick_chart llama a mplfinance.plot con type=candle."""
    from src.chart_generator import generate_candlestick_chart

    bars = _sample_bars(3)

    with patch("src.chart_generator.mpf") as mock_mpf:
        generate_candlestick_chart("AAPL", bars, "1Day", "opportunity", charts_dir=str(tmp_path))

    mock_mpf.plot.assert_called_once()
    call_kwargs = mock_mpf.plot.call_args[1]
    assert call_kwargs["type"] == "candle"
    assert call_kwargs["volume"] is True


def test_generate_chart_creates_charts_dir(tmp_path):
    """generate_candlestick_chart crea el directorio charts/ si no existe."""
    from src.chart_generator import generate_candlestick_chart

    charts_dir = str(tmp_path / "subdir" / "charts")
    bars = _sample_bars(3)

    with patch("src.chart_generator.mpf"):
        generate_candlestick_chart("AAPL", bars, "1Day", "opportunity", charts_dir=charts_dir)

    assert Path(charts_dir).exists()


def test_generate_chart_filename_contains_symbol_and_label(tmp_path):
    """El nombre del archivo incluye símbolo y event_label."""
    from src.chart_generator import generate_candlestick_chart

    bars = _sample_bars(3)

    with patch("src.chart_generator.mpf"):
        path = generate_candlestick_chart("AAPL", bars, "1Day", "entry", charts_dir=str(tmp_path))

    filename = Path(path).name
    assert "AAPL" in filename
    assert "entry" in filename
    assert filename.endswith(".png")


def test_generate_chart_raises_on_empty_bars(tmp_path):
    """generate_candlestick_chart lanza ValueError si no hay barras."""
    from src.chart_generator import generate_candlestick_chart

    with patch("src.chart_generator.mpf"):
        with pytest.raises(ValueError, match="No hay barras"):
            generate_candlestick_chart("AAPL", [], "1Day", "opportunity", charts_dir=str(tmp_path))


def test_generate_chart_raises_on_bars_with_no_valid_data(tmp_path):
    """generate_candlestick_chart lanza ValueError si todas las barras tienen NaN."""
    from src.chart_generator import generate_candlestick_chart

    bars = [{"timestamp": "2025-01-02T00:00:00Z", "open": None, "high": None, "low": None, "close": None, "volume": None}]

    with patch("src.chart_generator.mpf"):
        with pytest.raises(ValueError, match="vacío"):
            generate_candlestick_chart("AAPL", bars, "1Day", "opportunity", charts_dir=str(tmp_path))


def test_generate_chart_returns_absolute_path(tmp_path):
    """La ruta devuelta por generate_candlestick_chart es absoluta."""
    from src.chart_generator import generate_candlestick_chart

    bars = _sample_bars(3)

    with patch("src.chart_generator.mpf"):
        path = generate_candlestick_chart("MSFT", bars, "15Min", "entry", charts_dir=str(tmp_path))

    assert Path(path).is_absolute()
    assert "MSFT" in path


def test_generate_chart_title_includes_symbol_and_label(tmp_path):
    """El título del gráfico incluye el símbolo y el event_label."""
    from src.chart_generator import generate_candlestick_chart

    bars = _sample_bars(3)
    captured_kwargs = {}

    with patch("src.chart_generator.mpf") as mock_mpf:
        mock_mpf.plot.side_effect = lambda df, **kw: captured_kwargs.update(kw)
        generate_candlestick_chart("NVDA", bars, "1Day", "opportunity", charts_dir=str(tmp_path))

    title = captured_kwargs.get("title", "")
    assert "NVDA" in title
    assert "Opportunity" in title or "opportunity" in title.lower()


def test_generate_chart_passes_savefig_path(tmp_path):
    """El argumento savefig apunta a la ruta esperada dentro de charts_dir."""
    from src.chart_generator import generate_candlestick_chart

    bars = _sample_bars(3)
    captured_kwargs = {}

    with patch("src.chart_generator.mpf") as mock_mpf:
        mock_mpf.plot.side_effect = lambda df, **kw: captured_kwargs.update(kw)
        path = generate_candlestick_chart("AAPL", bars, "1Day", "opportunity", charts_dir=str(tmp_path))

    assert captured_kwargs.get("savefig") == path
    assert str(tmp_path) in path


# ── Test de integración real (requiere mplfinance instalado) ──────────────────

def test_generate_chart_real_png_created(tmp_path):
    """Genera un PNG real con mplfinance y verifica que el archivo existe."""
    pytest.importorskip("mplfinance")
    from src.chart_generator import generate_candlestick_chart

    bars = _sample_bars(10)
    path = generate_candlestick_chart("AAPL", bars, "1Day", "test", charts_dir=str(tmp_path))

    assert Path(path).exists()
    assert Path(path).stat().st_size > 0
