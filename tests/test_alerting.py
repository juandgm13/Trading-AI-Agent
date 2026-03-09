"""
Tests de TradeAlerter — requieren GMAIL_USER y GMAIL_APP_PASSWORD en .env.
El test de envío real manda un email de verdad a ALERT_RECIPIENT.
"""
import pytest
from unittest.mock import patch, MagicMock
from conftest import requires_gmail


# ── Tests sin credenciales (mock SMTP) ───────────────────────────────────────

def test_build_message_buy_contains_key_info():
    from src.alerting import TradeAlerter

    a = TradeAlerter("test@gmail.com", "pass")
    msg = a._build_message(
        symbol="AAPL", side="buy", qty=5, price=175.50,
        order_id="ord-001",
        reason="Strong earnings beat with positive guidance.",
        agent_analysis="Thought: AAPL dropped after earnings...",
    )

    subject = msg["Subject"]
    body = msg.get_payload(decode=False)  # MIME multipart

    assert "AAPL" in subject
    assert "BUY" in subject
    assert "🟢" in subject


def test_build_message_sell_has_red_emoji():
    from src.alerting import TradeAlerter

    a = TradeAlerter("test@gmail.com", "pass")
    msg = a._build_message(
        symbol="MSFT", side="sell", qty=3, price=320.0,
        order_id="ord-002", reason="Taking profits after 5% gain.",
        agent_analysis="",
    )

    assert "🔴" in msg["Subject"]
    assert "SELL" in msg["Subject"]


def test_send_trade_alert_returns_false_on_smtp_error():
    from src.alerting import TradeAlerter

    a = TradeAlerter("bad@gmail.com", "wrongpass")

    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value.login.side_effect = Exception("Auth failed")
        result = a.send_trade_alert(
            symbol="AAPL", side="buy", qty=5, price=175.0,
            order_id="ord-001", reason="Test reason",
        )

    assert result is False


def test_send_trade_alert_returns_true_on_success():
    from src.alerting import TradeAlerter

    a = TradeAlerter("user@gmail.com", "pass", "dest@gmail.com")

    with patch("smtplib.SMTP_SSL") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance
        result = a.send_trade_alert(
            symbol="NVDA", side="buy", qty=2, price=500.0,
            order_id="ord-003", reason="Momentum signal confirmed by news.",
            agent_analysis="Thought: NVDA surged on AI demand...",
        )

    assert result is True
    mock_instance.login.assert_called_once()
    mock_instance.sendmail.assert_called_once()


# ── Test con credenciales reales (envía email de verdad) ─────────────────────

@requires_gmail
def test_send_real_email(alerter):
    """Envía un email de prueba real. Verifica en tu bandeja de entrada."""
    result = alerter.send_trade_alert(
        symbol="AAPL",
        side="buy",
        qty=5,
        price=175.50,
        order_id="test-order-00000",
        reason="[TEST] Verificación del sistema de alertas de Trading AI Agent.",
        agent_analysis=(
            "Thought: Checking if the alert system works correctly.\n"
            "Action: send_test\n"
            "Final Answer: Test alert sent successfully."
        ),
    )
    assert result is True, "El email no se pudo enviar — verifica GMAIL_USER y GMAIL_APP_PASSWORD"
