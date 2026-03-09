"""
Fixtures compartidas para todos los tests.

Los tests que requieren credenciales reales se saltan automáticamente
si las variables de entorno no están configuradas.
"""
import os
import tempfile
import pytest
from dotenv import load_dotenv

load_dotenv()


# ── Helpers de skip ───────────────────────────────────────────────────────────

def _has_alpaca() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"))

def _has_gnews() -> bool:
    return bool(os.environ.get("GNEWS_API_KEY"))

def _has_gmail() -> bool:
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))


# ── Markers ───────────────────────────────────────────────────────────────────

requires_alpaca = pytest.mark.skipif(not _has_alpaca(), reason="ALPACA_API_KEY / ALPACA_SECRET_KEY no configuradas")
requires_gnews  = pytest.mark.skipif(not _has_gnews(),  reason="GNEWS_API_KEY no configurada")
requires_gmail  = pytest.mark.skipif(not _has_gmail(),  reason="GMAIL_USER / GMAIL_APP_PASSWORD no configuradas")
requires_all    = pytest.mark.skipif(
    not (_has_alpaca() and _has_gnews()),
    reason="Requiere ALPACA y GNEWS configuradas",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """StorageLayer usando una base de datos temporal (se borra al acabar el test)."""
    from src.storage import StorageLayer
    db_file = str(tmp_path / "test.db")
    return StorageLayer(db_path=db_file)


@pytest.fixture
def alpaca():
    """AlpacaClient con las credenciales del .env."""
    from src.alpaca import AlpacaClient
    return AlpacaClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
        trading_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    )


@pytest.fixture
def alerter():
    """TradeAlerter con las credenciales del .env."""
    from src.alerting import TradeAlerter
    return TradeAlerter(
        gmail_user=os.environ["GMAIL_USER"],
        gmail_password=os.environ["GMAIL_APP_PASSWORD"],
        recipient=os.environ.get("ALERT_RECIPIENT") or os.environ["GMAIL_USER"],
    )
