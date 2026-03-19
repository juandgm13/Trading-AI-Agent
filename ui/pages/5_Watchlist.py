import os
import asyncio
import streamlit as st
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Watchlist", page_icon="👁️", layout="wide")
trading_mode_banner()
st.title("👁️ Watchlist — Símbolos monitorizados")

storage = StorageLayer(db_path="trading_agent.db")

# ── Añadir símbolo ────────────────────────────────────────────────────────────
st.subheader("Añadir símbolo")
col1, col2, col3 = st.columns([2, 4, 1])
new_symbol = col1.text_input("Ticker", placeholder="AAPL").upper().strip()
new_company = col2.text_input("Empresa (opcional)", placeholder="Apple Inc.")

if col3.button("Añadir", type="primary"):
    if new_symbol:
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")
        trading_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

        if not api_key or not secret_key:
            st.warning("Alpaca credentials not configured — adding without validation.")
            storage.upsert_watchlist(new_symbol, new_company)
            st.success(f"{new_symbol} added to watchlist (unvalidated).")
            st.rerun()
        else:
            from src.alpaca import AlpacaClient

            async def _validate():
                client = AlpacaClient(api_key, secret_key, trading_url)
                return await client.get_asset(new_symbol)

            try:
                asset = asyncio.run(_validate())
            except Exception as e:
                st.error(f"Error connecting to Alpaca: {e}")
                st.stop()

            if asset is None:
                st.error(f"Symbol '{new_symbol}' not found in Alpaca or not tradable.")
            else:
                storage.upsert_watchlist(
                    new_symbol,
                    asset.get("name") or new_company,
                    exchange=asset.get("exchange", ""),
                    marginable=asset.get("marginable", False),
                    shortable=asset.get("shortable", False),
                )
                st.success(f"{new_symbol} validated and added ({asset.get('exchange', '')})")
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
        col1, col2, col3, col4, col5, col6 = st.columns([2, 4, 1, 1, 1, 1])
        col1.write(f"**{item['symbol']}**")
        col2.write(item.get("company") or "—")
        col3.write(item.get("exchange") or "—")
        col4.write("✓" if item.get("marginable") else "✗")
        col5.write("✓" if item.get("shortable") else "✗")
        if col6.button("Eliminar", key=f"del_{item['symbol']}"):
            storage.deactivate_watchlist(item["symbol"])
            st.rerun()

    st.caption("Columnas: Ticker | Empresa | Exchange | Marginable | Shortable | Acción")

st.divider()
