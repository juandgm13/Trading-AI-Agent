"""
Fixtures compartidas para todos los tests.

Los tests que requieren credenciales reales se saltan automáticamente
si las variables de entorno no están configuradas.
"""
import os
import pytest
import urllib.request
from dotenv import load_dotenv

load_dotenv()


# ── Helpers de skip ───────────────────────────────────────────────────────────

def _has_alpaca() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_SECRET_KEY"))

def _has_gnews() -> bool:
    # GNews reemplazado por Alpaca News + RSS. Siempre False para saltar estos tests.
    return False

def _has_gmail() -> bool:
    return bool(os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD"))

def _has_ollama() -> bool:
    """Comprueba si Ollama está corriendo en la URL configurada."""
    url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        urllib.request.urlopen(f"{url}/api/tags", timeout=2)
        return True
    except Exception:
        return False

def _has_network() -> bool:
    """Comprueba si hay acceso a internet (acepta cualquier respuesta HTTP, incluido 4xx)."""
    import urllib.error
    try:
        urllib.request.urlopen("https://www.google.com", timeout=5)
        return True
    except urllib.error.HTTPError:
        return True   # respuesta HTTP = hay red
    except Exception:
        return False


# ── Markers ───────────────────────────────────────────────────────────────────

requires_alpaca  = pytest.mark.skipif(not _has_alpaca(),  reason="ALPACA_API_KEY / ALPACA_SECRET_KEY no configuradas")
requires_network = pytest.mark.skipif(not _has_network(), reason="Sin acceso a internet")
requires_gnews  = pytest.mark.skipif(not _has_gnews(),  reason="GNEWS_API_KEY no configurada")
requires_gmail  = pytest.mark.skipif(not _has_gmail(),  reason="GMAIL_USER / GMAIL_APP_PASSWORD no configuradas")
requires_ollama = pytest.mark.skipif(not _has_ollama(), reason="Ollama no está corriendo")
requires_all    = pytest.mark.skipif(
    not _has_alpaca(),
    reason="Requiere ALPACA_API_KEY / ALPACA_SECRET_KEY configuradas",
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
