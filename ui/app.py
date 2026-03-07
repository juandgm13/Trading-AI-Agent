import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.storage import StorageLayer

st.set_page_config(
    page_title="Trading AI Agent",
    page_icon="📈",
    layout="wide",
)

st.title("Trading AI Agent")
st.caption("Dashboard de monitoreo de noticias financieras")

storage = StorageLayer()
articles = storage.get_all()

total = len(articles)
analyzed = sum(1 for a in articles if a["impact_level"])
high_impact = sum(1 for a in articles if a["impact_level"] == "HIGH")
alerts_sent = sum(1 for a in articles if a["alert_sent"])

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total articulos", total)
col2.metric("Analizados", analyzed)
col3.metric("Alto impacto", high_impact)
col4.metric("Alertas enviadas", alerts_sent)

st.markdown("---")
st.info("Navega a **Articulos** en el menu lateral para ver y gestionar los datos.")
