import streamlit as st
import pandas as pd
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Trade History", page_icon="💼", layout="wide")
trading_mode_banner()
st.title("💼 Trade History")

storage = StorageLayer(db_path="trading_agent.db")
trades = storage.get_trades(limit=200)

if not trades:
    st.info("No trades recorded yet. The monitor will execute the first trade when it detects a signal.")
    st.stop()

df = pd.DataFrame(trades)
df["created_at"] = pd.to_datetime(df["created_at"])

# Summary metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Trades", len(df))
col2.metric("Buys", len(df[df["side"] == "buy"]))
col3.metric("Sells", len(df[df["side"] == "sell"]))
col4.metric("Unique Symbols", df["symbol"].nunique())

st.divider()

# Filters
symbols = ["All"] + sorted(df["symbol"].unique().tolist())
selected_symbol = st.selectbox("Filter by symbol", symbols)

if selected_symbol != "All":
    df = df[df["symbol"] == selected_symbol]

# Calculate P&L per trade (matched buy→sell pairs per symbol)
df_sorted = df.sort_values("created_at")
buy_prices: dict = {}
pnl_list = []
for _, row in df_sorted.iterrows():
    sym = row["symbol"]
    if row["side"] == "buy" and row.get("price"):
        buy_prices[sym] = row["price"]
        pnl_list.append(None)
    elif row["side"] == "sell" and row.get("price") and sym in buy_prices:
        pnl = (row["price"] - buy_prices[sym]) * row["qty"]
        pnl_list.append(round(pnl, 2))
        buy_prices.pop(sym, None)
    else:
        pnl_list.append(None)
df_sorted["pnl"] = pnl_list
df = df_sorted

# Table
st.dataframe(
    df[["created_at", "symbol", "side", "qty", "price", "pnl", "status", "trigger", "reason", "order_id"]]
    .sort_values("created_at", ascending=False)
    .rename(columns={
        "created_at": "Date",
        "symbol": "Symbol",
        "side": "Direction",
        "qty": "Qty",
        "price": "Approx. Price",
        "pnl": "P&L ($)",
        "status": "Status",
        "trigger": "Trigger",
        "reason": "Reason",
        "order_id": "Order ID",
    }),
    use_container_width=True,
)
