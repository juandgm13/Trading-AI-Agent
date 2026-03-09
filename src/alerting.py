"""
Alertas por email — notifica compras/ventas ejecutadas por el agente.

Usa Gmail SMTP con App Password, igual que el resto del sistema.
"""
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class TradeAlerter:
    def __init__(self, gmail_user: str, gmail_password: str, recipient: str | None = None):
        self._user = gmail_user
        self._password = gmail_password
        self._recipient = recipient or gmail_user

    def send_trade_alert(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float | None,
        order_id: str | None,
        reason: str,
        agent_analysis: str = "",
    ) -> bool:
        """
        Envía un email con el resumen de la orden ejecutada.

        Returns:
            True si el email se envió correctamente, False si falló.
        """
        try:
            msg = self._build_message(symbol, side, qty, price, order_id, reason, agent_analysis)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self._user, self._password)
                server.sendmail(self._user, self._recipient, msg.as_string())
            return True
        except Exception:
            return False

    def _build_message(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float | None,
        order_id: str | None,
        reason: str,
        agent_analysis: str,
    ) -> MIMEMultipart:
        side_upper = side.upper()
        emoji = "🟢" if side == "buy" else "🔴"
        price_str = f"${price:.2f}" if price else "precio de mercado"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        subject = f"{emoji} [{side_upper}] {qty:.0f}x {symbol} — {reason[:60]}"

        html = f"""
<html><body style="font-family: Arial, sans-serif; color: #333; max-width: 600px;">
  <h2 style="color: {'#2e7d32' if side == 'buy' else '#c62828'};">
    {emoji} Orden ejecutada: {side_upper} {symbol}
  </h2>
  <table style="border-collapse: collapse; width: 100%;">
    <tr><td style="padding: 6px 12px; background:#f5f5f5; font-weight:bold;">Símbolo</td>
        <td style="padding: 6px 12px;">{symbol}</td></tr>
    <tr><td style="padding: 6px 12px; background:#f5f5f5; font-weight:bold;">Dirección</td>
        <td style="padding: 6px 12px;">{side_upper}</td></tr>
    <tr><td style="padding: 6px 12px; background:#f5f5f5; font-weight:bold;">Cantidad</td>
        <td style="padding: 6px 12px;">{qty:.0f} acciones</td></tr>
    <tr><td style="padding: 6px 12px; background:#f5f5f5; font-weight:bold;">Precio aprox.</td>
        <td style="padding: 6px 12px;">{price_str}</td></tr>
    <tr><td style="padding: 6px 12px; background:#f5f5f5; font-weight:bold;">Order ID</td>
        <td style="padding: 6px 12px; font-size:12px; color:#666;">{order_id or '—'}</td></tr>
    <tr><td style="padding: 6px 12px; background:#f5f5f5; font-weight:bold;">Timestamp</td>
        <td style="padding: 6px 12px;">{ts}</td></tr>
  </table>

  <h3 style="margin-top: 24px;">Razón de la decisión</h3>
  <p style="background: #fff8e1; padding: 12px; border-left: 4px solid #f9a825; margin: 0;">
    {reason}
  </p>

  {f'''
  <h3 style="margin-top: 24px;">Análisis completo del agente</h3>
  <pre style="background: #f5f5f5; padding: 12px; font-size: 12px;
              white-space: pre-wrap; word-wrap: break-word;">{agent_analysis}</pre>
  ''' if agent_analysis else ''}

  <p style="margin-top: 24px; font-size: 12px; color: #999;">
    Trading AI Agent — paper trading (simulado)
  </p>
</body></html>
"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._user
        msg["To"] = self._recipient
        msg.attach(MIMEText(html, "html"))
        return msg
