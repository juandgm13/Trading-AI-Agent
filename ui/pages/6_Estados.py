import streamlit as st
import pandas as pd
from src.storage import StorageLayer

st.set_page_config(page_title="Estados de Símbolos", page_icon="🔄", layout="wide")
st.title("🔄 Estados de Símbolos")

storage = StorageLayer(db_path="trading_agent.db")

# ── Tabla de estados ──────────────────────────────────────────────────────────

st.subheader("Máquina de estados por símbolo")

states = storage.get_all_symbol_states()

if not states:
    st.info("No hay estados registrados. El monitor los creará cuando empiece a analizar símbolos.")
else:
    df = pd.DataFrame(states)

    # Colorear estado
    state_colors = {
        "WATCHING": "🔵",
        "OPPORTUNITY_FOUND": "🟡",
        "POSITION_OPEN": "🟢",
    }
    df["estado"] = df["state"].map(lambda s: f"{state_colors.get(s, '⚪')} {s}")

    display_cols = {
        "symbol": "Símbolo",
        "estado": "Estado",
        "last_analysis_at": "Último análisis",
        "analysis_summary": "Resumen",
        "target_price": "Target ($)",
        "stop_loss": "Stop-loss ($)",
        "position_qty": "Cantidad posición",
        "position_open_at": "Posición abierta",
        "updated_at": "Actualizado",
    }

    df_display = df[list(display_cols.keys())].rename(columns=display_cols)
    st.dataframe(df_display, use_container_width=True)

st.divider()

# ── Historial de informes de sub-agentes ──────────────────────────────────────

st.subheader("Historial de informes de sub-agentes")

all_states = storage.get_all_symbol_states()
symbols_with_reports = [s["symbol"] for s in all_states] if all_states else []

if not symbols_with_reports:
    st.info("No hay informes de agentes todavía.")
else:
    selected = st.selectbox("Seleccionar símbolo", symbols_with_reports)
    reports = storage.get_agent_reports(selected, limit=30)

    if not reports:
        st.info(f"No hay informes para {selected}.")
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
