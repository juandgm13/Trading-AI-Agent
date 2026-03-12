import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def trading_mode_banner() -> None:
    """Muestra un banner visible indicando si se opera en paper o en real."""
    alpaca_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if "paper-api" in alpaca_url:
        st.info("🧪 **Modo paper trading** — las órdenes son simuladas, no se usa dinero real.")
    else:
        st.error("⚠️ **MODO REAL** — las órdenes se ejecutan con dinero real.")
