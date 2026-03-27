import json
import streamlit as st
from pathlib import Path
from ui.components import trading_mode_banner

st.set_page_config(page_title="Prompt Logs", page_icon="📋", layout="wide")
trading_mode_banner()
st.title("📋 Prompt Logs")

PROMPTS_FILE = Path("logs/prompts.jsonl")

_AGENT_COLORS = {
    "technical": "🔵",
    "technical_entry": "🟣",
    "economics": "🟢",
    "news_classifier": "⚪",
    "trader_opportunity": "🟡",
    "trader_entry": "🔴",
}

if not PROMPTS_FILE.exists():
    st.info("No prompt logs found. Run the monitor to generate prompts.jsonl.")
    st.stop()

# ── Cargar entradas ────────────────────────────────────────────────────────────

raw_lines = PROMPTS_FILE.read_text(encoding="utf-8").splitlines()
entries = []
for line in raw_lines:
    line = line.strip()
    if not line:
        continue
    try:
        entries.append(json.loads(line))
    except json.JSONDecodeError:
        continue

if not entries:
    st.warning("El archivo prompts.jsonl existe pero no contiene entradas válidas.")
    st.stop()

# Invertir para mostrar más reciente primero
entries = list(reversed(entries))

# ── Filtros ────────────────────────────────────────────────────────────────────

agents = sorted({e.get("agent", "unknown") for e in entries})
col1, col2 = st.columns([2, 1])
with col1:
    selected_agent = st.selectbox("Filtrar por agente", ["(todos)"] + agents)
with col2:
    max_shown = st.slider("Máximo de entradas", min_value=5, max_value=200, value=50, step=5)

if selected_agent != "(todos)":
    entries = [e for e in entries if e.get("agent") == selected_agent]

entries = entries[:max_shown]

st.caption(f"Mostrando {len(entries)} entradas")

# ── Lista de entradas ──────────────────────────────────────────────────────────

for entry in entries:
    agent = entry.get("agent", "unknown")
    timestamp = entry.get("timestamp", "")
    icon = _AGENT_COLORS.get(agent, "⚫")
    label = f"{icon} `{agent}` — {timestamp}"

    with st.expander(label, expanded=False):
        system_prompt = entry.get("system_prompt", "")
        if system_prompt:
            st.markdown("**System Prompt**")
            st.text(system_prompt)
            st.divider()

        st.markdown("**User Prompt**")
        st.text(entry.get("prompt", ""))
        st.divider()

        st.markdown("**Response**")
        response = entry.get("response", "")
        try:
            parsed = json.loads(response)
            st.json(parsed)
        except (json.JSONDecodeError, TypeError):
            st.code(response, language="text")
