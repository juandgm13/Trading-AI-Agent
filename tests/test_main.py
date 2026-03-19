"""
Tests para main.py — _TextRedirector con timestamps.
No requieren display de Tkinter (se mockea el widget).
"""
from unittest.mock import MagicMock, patch


def _make_redirector():
    """Crea un _TextRedirector con widget mockeado."""
    from main import _TextRedirector
    widget = MagicMock()
    widget.configure = MagicMock()
    widget.insert = MagicMock()
    widget.see = MagicMock()
    return _TextRedirector(widget), widget


def test_text_redirector_adds_timestamp():
    """Mensajes con contenido deben llevar prefijo [HH:MM:SS]."""
    redirector, widget = _make_redirector()

    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "12:34:56"
        redirector.write("Hello monitor\n")

    call_args = widget.insert.call_args[0]
    inserted = call_args[1]
    assert "[12:34:56]" in inserted
    assert "Hello monitor" in inserted


def test_text_redirector_no_timestamp_on_whitespace():
    """Saltos de línea solos no deben llevar timestamp."""
    redirector, widget = _make_redirector()

    redirector.write("\n")

    call_args = widget.insert.call_args[0]
    inserted = call_args[1]
    assert "[" not in inserted
    assert inserted == "\n"


def test_text_redirector_no_timestamp_on_empty():
    """Strings vacíos no deben llevar timestamp."""
    redirector, widget = _make_redirector()

    redirector.write("")

    call_args = widget.insert.call_args[0]
    inserted = call_args[1]
    assert inserted == ""


def test_text_redirector_timestamp_format():
    """El timestamp debe usar el formato HH:MM:SS."""
    redirector, widget = _make_redirector()

    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value.strftime.return_value = "09:05:03"
        redirector.write("test message\n")

    call_args = widget.insert.call_args[0]
    inserted = call_args[1]
    assert inserted.startswith("[09:05:03]")
