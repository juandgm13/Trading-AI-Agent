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
col1, col2, col3 = st.columns(3)
with col1:
    filter_scope = st.multiselect(
        "Scope", ["company", "market", "exchange", "irrelevant"]
    )
with col2:
    filter_impact = st.multiselect("Impact", ["HIGH", "MEDIUM", "LOW"])
with col3:
    filter_ticker = st.text_input("Ticker (e.g. AAPL)")

# ── Data ──────────────────────────────────────────────────────────────────────
articles = storage.get_all()
df = pd.DataFrame(articles)

if df.empty:
    st.info("No articles in the database.")
    st.stop()

# Ensure new columns exist (graceful for old rows)
for col in ("scope", "feed_group", "exchange_affected", "impact", "reasoning", "expires_at", "description", "url", "created_at"):
    if col not in df.columns:
        df[col] = None

if filter_scope:
    df = df[df["scope"].isin(filter_scope)]
if filter_impact:
    df = df[df["impact"].isin(filter_impact)]
if filter_ticker:
    df = df[df["ticker"].str.upper() == filter_ticker.strip().upper()]

# ── Article detail modal ───────────────────────────────────────────────────────
@st.dialog("Article details", width="large")
def _show_article(row: pd.Series) -> None:
    st.markdown(f"### {row.get('title', '')}")

    description = row.get("description") or ""
    if description:
        st.markdown(description)
        st.markdown("---")

    c1, c2 = st.columns(2)
    with c1:
        source = row.get("source") or ""
        feed = row.get("feed_group") or ""
        st.markdown(f"**Source:** {source}  \n**Feed:** {feed}")
        published = row.get("published_at") or ""
        st.markdown(f"**Published:** {published}")
    with c2:
        scope = row.get("scope") or ""
        ticker = row.get("ticker") or ""
        exchange = row.get("exchange_affected") or ""
        scope_line = f"**Scope:** {scope}"
        if ticker:
            scope_line += f"  \n**Ticker:** {ticker}"
        if exchange:
            scope_line += f"  \n**Exchange:** {exchange}"
        st.markdown(scope_line)

        impact = row.get("impact") or ""
        if impact:
            st.markdown(f"**Impact:** {impact}")

    reasoning = row.get("reasoning") or ""
    if reasoning:
        st.markdown("**Reasoning:**")
        st.markdown(reasoning)

    url = row.get("url") or ""
    if url:
        st.markdown(f"**URL:** [{url}]({url})")

    st.markdown("---")
    expires = row.get("expires_at") or ""
    created = row.get("created_at") or ""
    if expires or created:
        meta_parts = []
        if expires:
            meta_parts.append(f"Expires: {expires}")
        if created:
            meta_parts.append(f"Saved: {created}")
        st.caption("  ·  ".join(meta_parts))


# ── Table ─────────────────────────────────────────────────────────────────────
display_cols = [
    "title", "source", "feed_group",
    "scope", "ticker", "exchange_affected",
    "impact", "reasoning",
    "expires_at", "published_at",
]
# Only include columns that actually exist
display_cols = [c for c in display_cols if c in df.columns]

event = st.dataframe(
    df[display_cols],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "title": st.column_config.TextColumn("Title", width="large"),
        "source": st.column_config.TextColumn("Source", width="small"),
        "feed_group": st.column_config.TextColumn("Feed", width="small"),
        "scope": st.column_config.TextColumn("Scope", width="small"),
        "ticker": st.column_config.TextColumn("Ticker", width="small"),
        "exchange_affected": st.column_config.TextColumn("Exchange", width="small"),
        "impact": st.column_config.TextColumn("Impact", width="small"),
        "reasoning": st.column_config.TextColumn("Reasoning", width="medium"),
        "expires_at": st.column_config.DatetimeColumn("Expires", format="YYYY-MM-DD HH:mm"),
        "published_at": st.column_config.DatetimeColumn("Published", format="YYYY-MM-DD HH:mm"),
    },
)
st.caption(f"{len(df)} articles shown · Click a row to view full article details")

selected_rows = event.selection.rows
if selected_rows:
    _show_article(df.iloc[selected_rows[0]])

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
