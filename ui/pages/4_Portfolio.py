import asyncio
import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from ui.components import trading_mode_banner

load_dotenv()

st.set_page_config(page_title="Portfolio", page_icon="💰", layout="wide")
trading_mode_banner()
st.title("💰 Portfolio (Alpaca)")


def _load_portfolio():
    """Load account and positions from Alpaca synchronously."""
    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    trading_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not secret_key:
        return None, None, "ALPACA_API_KEY / ALPACA_SECRET_KEY credentials not configured in .env"

    from src.alpaca import AlpacaClient

    async def _fetch():
        client = AlpacaClient(api_key, secret_key, trading_url)
        account = await client.get_account()
        positions = await client.get_positions()
        return account, positions



    try:
        return *asyncio.run(_fetch()), None
    except Exception as e:
        return None, None, str(e)


if st.button("🔄 Refresh", type="primary"):
    st.rerun()

account, positions, error = _load_portfolio()

if error:
    st.error(f"Error connecting to Alpaca: {error}")
    st.stop()

# Account metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Equity", f"${account['equity']:,.2f}")
col2.metric("Available Cash", f"${account['cash']:,.2f}")
col3.metric("Total Portfolio", f"${account['portfolio_value']:,.2f}")
col4.metric("Buying Power", f"${account['buying_power']:,.2f}")

st.divider()

# Open positions
st.subheader("Open Positions")
if not positions:
    st.info("No open positions at this time.")
else:
    df = pd.DataFrame(positions)
    df["unrealized_plpc"] = df["unrealized_plpc"].map(lambda x: f"{x:.2f}%")

    # Add target_price and stop_loss from symbol_states
    from src.storage import StorageLayer as _SL
    _storage = _SL(db_path="trading_agent.db")
    states = {s["symbol"]: s for s in _storage.get_all_symbol_states()}
    df["target_price"] = df["symbol"].map(lambda s: states.get(s, {}).get("target_price"))
    df["stop_loss"] = df["symbol"].map(lambda s: states.get(s, {}).get("stop_loss"))

    st.dataframe(
        df.rename(columns={
            "symbol": "Symbol",
            "qty": "Qty",
            "avg_entry_price": "Entry Price",
            "current_price": "Current Price",
            "market_value": "Market Value",
            "unrealized_pl": "Unrealized P&L",
            "unrealized_plpc": "P&L %",
            "target_price": "Target",
            "stop_loss": "Stop-loss",
        }),
        use_container_width=True,
    )
