import streamlit as st
import pandas as pd
from src.storage import StorageLayer

st.set_page_config(page_title="Historial de Trades", page_icon="💼", layout="wide")
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

# Tabla
st.dataframe(
    df[["created_at", "symbol", "side", "qty", "price", "status", "trigger", "reason", "order_id"]]
    .sort_values("created_at", ascending=False)
    .rename(columns={
        "created_at": "Fecha",
        "symbol": "Símbolo",
        "side": "Dirección",
        "qty": "Cantidad",
        "price": "Precio aprox.",
        "status": "Estado",
        "trigger": "Disparador",
        "reason": "Razón",
        "order_id": "Order ID",
    }),
    use_container_width=True,
)
