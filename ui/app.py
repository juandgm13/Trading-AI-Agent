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

# ── Global market context ─────────────────────────────────────────────────────

st.subheader("Global Market Context")

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
    col1.metric("Sentiment", f"{sentiment_icon} {sentiment}")
    col2.metric("Risk Appetite", f"{risk_icon} {risk}")
    col3.metric("Confidence", f"{confidence}%")
    col4.metric("Updated", updated_at[:16] if updated_at else "?")

    themes = ctx.get("dominant_themes", [])
    risks = ctx.get("macro_risks", [])
    summary = ctx.get("summary", "")

    if themes or risks:
        c1, c2 = st.columns(2)
        if themes:
            c1.markdown("**Dominant Themes**")
            for t in themes:
                c1.markdown(f"- {t}")
        if risks:
            c2.markdown("**Macro Risks**")
            for r in risks:
                c2.markdown(f"- {r}")

    if summary:
        st.caption(summary)
else:
    st.info("No global context report yet. The monitor will generate it in the next cycle.")

st.divider()

# ── Symbol status ─────────────────────────────────────────────────────────────

st.subheader("Symbol Status")

states = storage.get_all_symbol_states()

watching = [s for s in states if s.get("state") == "WATCHING"]
opportunity = [s for s in states if s.get("state") == "OPPORTUNITY_FOUND"]
position = [s for s in states if s.get("state") == "POSITION_OPEN"]

col1, col2, col3 = st.columns(3)
col1.metric("🔵 WATCHING", len(watching))
col2.metric("🟡 OPPORTUNITY_FOUND", len(opportunity))
col3.metric("🟢 POSITION_OPEN", len(position))

# Open positions
if position:
    st.markdown("**Open Positions**")
    pos_data = [
        {
            "Symbol": s["symbol"],
            "Qty": s.get("position_qty"),
            "Target ($)": s.get("target_price"),
            "Stop-loss ($)": s.get("stop_loss"),
            "Opened": (s.get("position_open_at") or "")[:16],
        }
        for s in position
    ]
    st.dataframe(pos_data, use_container_width=True, hide_index=True)

# Opportunities in tracking
if opportunity:
    st.markdown("**Opportunities in Tracking**")
    st.write(", ".join(s["symbol"] for s in opportunity))

st.divider()

# ── Recent trades ─────────────────────────────────────────────────────────────

st.subheader("Recent Trades")

trades = storage.get_trades(limit=10)
if not trades:
    st.info("No trades recorded yet.")
else:
    import pandas as pd
    df = pd.DataFrame(trades)[
        ["created_at", "symbol", "side", "qty", "price", "status", "trigger"]
    ].rename(columns={
        "created_at": "Date",
        "symbol": "Symbol",
        "side": "Direction",
        "qty": "Qty",
        "price": "Approx. Price",
        "status": "Status",
        "trigger": "Trigger",
    })
    st.dataframe(df, use_container_width=True, hide_index=True)
