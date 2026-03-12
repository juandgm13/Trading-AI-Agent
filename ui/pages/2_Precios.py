import streamlit as st
import pandas as pd
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Historial de Precios", page_icon="📊", layout="wide")
trading_mode_banner()
st.title("📊 Historial de Precios")

storage = StorageLayer(db_path="trading_agent.db")
watchlist = storage.get_watchlist()

if not watchlist:
    st.info("La watchlist está vacía. Añade símbolos en la página Watchlist.")
    st.stop()

symbols = [w["symbol"] for w in watchlist]
selected = st.selectbox("Símbolo", symbols)

if selected:
    rows = storage.get_price_history(selected, limit=200)
    if not rows:
        st.warning(f"No hay datos de precios para {selected} todavía.")
    else:
        df = pd.DataFrame(rows)
        df["recorded_at"] = pd.to_datetime(df["recorded_at"])
        df = df.sort_values("recorded_at")

        col1, col2, col3 = st.columns(3)
        col1.metric("Último precio", f"${df['close'].iloc[-1]:.2f}")
        col2.metric("Máximo", f"${df['close'].max():.2f}")
        col3.metric("Mínimo", f"${df['close'].min():.2f}")

        st.line_chart(df.set_index("recorded_at")["close"], height=300)

        with st.expander("Ver tabla completa"):
            st.dataframe(
                df[["recorded_at", "close", "volume", "bar_time"]].sort_values(
                    "recorded_at", ascending=False
                ),
                use_container_width=True,
            )
