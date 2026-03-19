import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from src.storage import StorageLayer
from ui.components import trading_mode_banner

st.set_page_config(
    page_title="Articles · Trading AI Agent",
    page_icon="📰",
    layout="wide",
)
trading_mode_banner()

st.title("Articles")

storage = StorageLayer()

# ── Filters ───────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)
with col1:
    filter_level = st.multiselect("Impact level", ["LOW", "MEDIUM", "HIGH"])
with col2:
    filter_type = st.multiselect("Impact type", ["POSITIVE", "NEGATIVE", "NEUTRAL"])

# ── Data ──────────────────────────────────────────────────────────────────────
articles = storage.get_all()
df = pd.DataFrame(articles)

if df.empty:
    st.info("No articles in the database.")
    st.stop()

if filter_level:
    df = df[df["impact_level"].isin(filter_level)]
if filter_type:
    df = df[df["impact_type"].isin(filter_type)]

# ── Table ─────────────────────────────────────────────────────────────────────
display_cols = [
    "title", "source", "company", "ticker",
    "impact_level", "impact_type", "confidence",
    "alert_sent", "published_at",
]
st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
st.caption(f"{len(df)} articles shown")

# ── Delete individual ─────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Delete article")

titles_to_url = dict(zip(df["title"], df["url"]))
selected_title = st.selectbox("Select article", options=list(titles_to_url.keys()))

if st.button("Delete selected article", type="secondary"):
    storage.delete_by_url(titles_to_url[selected_title])
    st.success(f"Deleted: {selected_title}")
    st.rerun()

# ── Full reset ────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Full Reset")
st.warning("This will delete all articles from the database.")

if st.button("Reset database", type="primary"):
    storage.reset()
    st.success("Database reset.")
    st.rerun()
