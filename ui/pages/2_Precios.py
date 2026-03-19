import streamlit as st
import pandas as pd
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Price History", page_icon="📊", layout="wide")
trading_mode_banner()
st.title("📊 Price History")

storage = StorageLayer(db_path="trading_agent.db")
watchlist = storage.get_watchlist()

if not watchlist:
    st.info("Watchlist is empty. Add symbols in the Watchlist page.")
    st.stop()

symbols = [w["symbol"] for w in watchlist]
selected = st.selectbox("Symbol", symbols)

if selected:
    rows = storage.get_price_history(selected, limit=200)
    if not rows:
        st.warning(f"No price data for {selected} yet.")
    else:
        df = pd.DataFrame(rows)
        df["recorded_at"] = pd.to_datetime(df["recorded_at"])
        df = df.sort_values("recorded_at")

        col1, col2, col3 = st.columns(3)
        col1.metric("Last Price", f"${df['close'].iloc[-1]:.2f}")
        col2.metric("Max", f"${df['close'].max():.2f}")
        col3.metric("Min", f"${df['close'].min():.2f}")

        st.line_chart(df.set_index("recorded_at")["close"], height=300)

        with st.expander("View full table"):
            st.dataframe(
                df[["recorded_at", "close", "volume", "bar_time"]].sort_values(
                    "recorded_at", ascending=False
                ),
                use_container_width=True,
            )
