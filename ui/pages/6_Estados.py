import streamlit as st
import pandas as pd
from pathlib import Path
from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(page_title="Symbol States", page_icon="🔄", layout="wide")
trading_mode_banner()
st.title("🔄 Symbol States")

storage = StorageLayer(db_path="trading_agent.db")

# ── States table ──────────────────────────────────────────────────────────────

st.subheader("State machine per symbol")

states = storage.get_all_symbol_states()

if not states:
    st.info("No states recorded. The monitor will create them when it starts analysing symbols.")
else:
    df = pd.DataFrame(states)

    # Colorize state
    state_colors = {
        "WATCHING": "🔵",
        "OPPORTUNITY_FOUND": "🟡",
        "POSITION_OPEN": "🟢",
    }
    df["estado"] = df["state"].map(lambda s: f"{state_colors.get(s, '⚪')} {s}")

    display_cols = {
        "symbol": "Symbol",
        "estado": "State",
        "last_analysis_at": "Last Analysis",
        "analysis_summary": "Summary",
        "target_price": "Target ($)",
        "stop_loss": "Stop-loss ($)",
        "position_qty": "Position Qty",
        "position_open_at": "Position Opened",
        "updated_at": "Updated",
    }

    df_display = df[list(display_cols.keys())].rename(columns=display_cols)
    st.dataframe(df_display, use_container_width=True)

    # ── Evidence charts ───────────────────────────────────────────────────────
    symbols_with_charts = [
        s for s in states
        if s.get("chart_path") and Path(s["chart_path"]).exists()
    ]
    if symbols_with_charts:
        st.divider()
        st.subheader("Evidence Charts")
        for s in symbols_with_charts:
            chart_path = s["chart_path"]
            symbol = s["symbol"]
            state = s["state"]
            with st.expander(f"{symbol} — {state_colors.get(state, '⚪')} {state}"):
                st.image(chart_path, use_container_width=True)
                st.caption(f"Path: {chart_path}")

st.divider()

# ── Sub-agent report history ──────────────────────────────────────────────────

st.subheader("Sub-agent Report History")

all_states = storage.get_all_symbol_states()
symbols_with_reports = [s["symbol"] for s in all_states] if all_states else []

if not symbols_with_reports:
    st.info("No agent reports yet.")
else:
    selected = st.selectbox("Select symbol", symbols_with_reports)
    reports = storage.get_agent_reports(selected, limit=30)

    if not reports:
        st.info(f"No reports for {selected}.")
    else:
        for report in reports:
            agent_name = report.get("agent_name", "?")
            created_at = report.get("created_at", "?")
            report_text = report.get("report", "{}")

            with st.expander(f"{agent_name} — {created_at}"):
                try:
                    import json
                    parsed = json.loads(report_text)
                    st.json(parsed)
                except Exception:
                    st.text(report_text)
