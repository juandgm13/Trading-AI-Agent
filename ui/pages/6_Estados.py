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

st.divider()

# ── Sub-agent report history ──────────────────────────────────────────────────

st.subheader("Sub-agent Report History")

all_states = storage.get_all_symbol_states()
symbols_with_reports = [s["symbol"] for s in all_states] if all_states else []

if not symbols_with_reports:
    st.info("No agent reports yet.")
else:
    selected = st.selectbox("Select symbol", symbols_with_reports)

    sym_state = storage.get_symbol_state(selected)
    chart_path = sym_state.get("chart_path")
    if chart_path and Path(chart_path).exists():
        with st.expander(f"Evidence chart — {selected}", expanded=True):
            st.image(chart_path, use_container_width=True)
            st.caption(chart_path)

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
