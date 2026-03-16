import streamlit as st
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Watchlist", page_icon="👁️", layout="wide")
trading_mode_banner()
st.title("👁️ Watchlist — Símbolos monitorizados")

storage = StorageLayer(db_path="trading_agent.db")

# ── Añadir símbolo ────────────────────────────────────────────────────────────
st.subheader("Añadir símbolo")
col1, col2, col3, col4 = st.columns([2, 3, 2, 1])
new_symbol = col1.text_input("Ticker", placeholder="AAPL").upper().strip()
new_company = col2.text_input("Empresa (opcional)", placeholder="Apple Inc.")
new_threshold = col3.number_input("Threshold % cambio", min_value=0.1, max_value=20.0, value=2.0, step=0.1)

if col4.button("Añadir", type="primary"):
    if new_symbol:
        storage.upsert_watchlist(new_symbol, new_company, new_threshold)
        st.success(f"{new_symbol} añadido a la watchlist")
        st.rerun()
    else:
        st.warning("Introduce un ticker válido")

st.divider()

# ── Watchlist actual ──────────────────────────────────────────────────────────
st.subheader("Símbolos activos")
watchlist = storage.get_watchlist()

if not watchlist:
    st.info("La watchlist está vacía. Añade símbolos arriba.")
else:
    for item in watchlist:
        col1, col2, col3, col4, col5, col6, col7 = st.columns([2, 3, 2, 1, 1, 1, 1])
        col1.write(f"**{item['symbol']}**")
        col2.write(item.get("company") or "—")
        col3.write(f"Threshold: {item['threshold_pct']}%")
        col4.write(item.get("exchange") or "—")
        col5.write("✓" if item.get("marginable") else "✗")
        col6.write("✓" if item.get("shortable") else "✗")
        if col7.button("Eliminar", key=f"del_{item['symbol']}"):
            storage.deactivate_watchlist(item["symbol"])
            st.rerun()

    st.caption("Columnas: Ticker | Empresa | Threshold | Exchange | Marginable | Shortable | Acción")

st.divider()
st.caption(
    "El threshold define el % de cambio de precio que dispara el análisis del agente. "
    "Un valor más bajo dispara el agente más frecuentemente."
)
