import streamlit as st
import pandas as pd
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Historial de Trades", page_icon="💼", layout="wide")
trading_mode_banner()
st.title("💼 Historial de Trades")

storage = StorageLayer(db_path="trading_agent.db")
trades = storage.get_trades(limit=200)

if not trades:
    st.info("No hay trades registrados todavía. El monitor ejecutará el primer trade cuando detecte una señal.")
    st.stop()

df = pd.DataFrame(trades)
df["created_at"] = pd.to_datetime(df["created_at"])

# Métricas resumen
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total trades", len(df))
col2.metric("Compras", len(df[df["side"] == "buy"]))
col3.metric("Ventas", len(df[df["side"] == "sell"]))
col4.metric("Símbolos distintos", df["symbol"].nunique())

st.divider()

# Filtros
symbols = ["Todos"] + sorted(df["symbol"].unique().tolist())
selected_symbol = st.selectbox("Filtrar por símbolo", symbols)

if selected_symbol != "Todos":
    df = df[df["symbol"] == selected_symbol]

# Calcular P&L por trade (solo para pares buy→sell del mismo símbolo)
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

# Tabla
st.dataframe(
    df[["created_at", "symbol", "side", "qty", "price", "pnl", "status", "trigger", "reason", "order_id"]]
    .sort_values("created_at", ascending=False)
    .rename(columns={
        "created_at": "Fecha",
        "symbol": "Símbolo",
        "side": "Dirección",
        "qty": "Cantidad",
        "price": "Precio aprox.",
        "pnl": "P&L ($)",
        "status": "Estado",
        "trigger": "Disparador",
        "reason": "Razón",
        "order_id": "Order ID",
    }),
    use_container_width=True,
)
