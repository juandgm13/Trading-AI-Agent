import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(
    page_title="Trading AI Agent — Summary",
    page_icon="📈",
    layout="wide",
)

trading_mode_banner()

st.title("📈 Trading AI Agent — Summary")

storage = StorageLayer(db_path="trading_agent.db")

# ── Contexto global de mercado ────────────────────────────────────────────────

st.subheader("Contexto global de mercado")

ctx_row = storage.get_latest_market_context()
if ctx_row:
    try:
        ctx = json.loads(ctx_row["report"])
    except Exception:
        ctx = {}

    sentiment = ctx.get("market_sentiment", "N/A")
    risk = ctx.get("risk_appetite", "N/A")
    confidence = ctx.get("confidence", 0)
    updated_at = ctx_row.get("created_at", "?")

    sentiment_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "🟡"}.get(sentiment, "⚪")
    risk_icon = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "NEUTRAL": "🟡"}.get(risk, "⚪")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Sentimiento", f"{sentiment_icon} {sentiment}")
    col2.metric("Apetito de riesgo", f"{risk_icon} {risk}")
    col3.metric("Confianza", f"{confidence}%")
    col4.metric("Actualizado", updated_at[:16] if updated_at else "?")

    themes = ctx.get("dominant_themes", [])
    risks = ctx.get("macro_risks", [])
    summary = ctx.get("summary", "")

    if themes or risks:
        c1, c2 = st.columns(2)
        if themes:
            c1.markdown("**Temas dominantes**")
            for t in themes:
                c1.markdown(f"- {t}")
        if risks:
            c2.markdown("**Riesgos macro**")
            for r in risks:
                c2.markdown(f"- {r}")

    if summary:
        st.caption(summary)
else:
    st.info("Aún no hay informe de contexto global. El monitor lo generará en el próximo ciclo.")

st.divider()

# ── Estado de la cartera de símbolos ─────────────────────────────────────────

st.subheader("Estado de símbolos")

states = storage.get_all_symbol_states()

watching = [s for s in states if s.get("state") == "WATCHING"]
opportunity = [s for s in states if s.get("state") == "OPPORTUNITY_FOUND"]
position = [s for s in states if s.get("state") == "POSITION_OPEN"]

col1, col2, col3 = st.columns(3)
col1.metric("🔵 WATCHING", len(watching))
col2.metric("🟡 OPPORTUNITY_FOUND", len(opportunity))
col3.metric("🟢 POSITION_OPEN", len(position))

# Posiciones abiertas
if position:
    st.markdown("**Posiciones abiertas**")
    pos_data = [
        {
            "Símbolo": s["symbol"],
            "Cantidad": s.get("position_qty"),
            "Target ($)": s.get("target_price"),
            "Stop-loss ($)": s.get("stop_loss"),
            "Abierta": (s.get("position_open_at") or "")[:16],
        }
        for s in position
    ]
    st.dataframe(pos_data, use_container_width=True, hide_index=True)

# Oportunidades en seguimiento
if opportunity:
    st.markdown("**Oportunidades en seguimiento**")
    st.write(", ".join(s["symbol"] for s in opportunity))

st.divider()

# ── Trades recientes ──────────────────────────────────────────────────────────

st.subheader("Trades recientes")

trades = storage.get_trades(limit=10)
if not trades:
    st.info("No hay trades registrados todavía.")
else:
    import pandas as pd
    df = pd.DataFrame(trades)[
        ["created_at", "symbol", "side", "qty", "price", "status", "trigger"]
    ].rename(columns={
        "created_at": "Fecha",
        "symbol": "Símbolo",
        "side": "Dirección",
        "qty": "Cantidad",
        "price": "Precio aprox.",
        "status": "Estado",
        "trigger": "Disparador",
    })
    st.dataframe(df, use_container_width=True, hide_index=True)
