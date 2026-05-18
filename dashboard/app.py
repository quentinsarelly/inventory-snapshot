"""Streamlit dashboard — daily inventory by SKU across all sources."""
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Inventory Snapshot", layout="wide")

import psycopg2
import psycopg2.extras

SOURCES = ["shopify_us", "shopify_mx", "amazon_us", "amazon_mx", "tiktok_us", "us_3pl", "mx_3pl"]
SOURCE_LABELS = {
    "shopify_us": "Shopify US",
    "shopify_mx": "Shopify MX",
    "amazon_us":  "Amazon US",
    "amazon_mx":  "Amazon MX",
    "tiktok_us":  "TikTok US",
    "us_3pl":     "US 3PL",
    "mx_3pl":     "MX 3PL (ShipHero)",
}

@st.cache_resource
def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])

def load_inventory(snapshot_date: date) -> pd.DataFrame:
    sql = """
        SELECT internal_sku, display_name, category,
               shopify_us, shopify_mx, amazon_us, amazon_mx, tiktok_us, us_3pl, mx_3pl,
               total_available
        FROM inventory_unified
        WHERE snapshot_date = %s
        ORDER BY total_available DESC
    """
    with get_conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, (snapshot_date,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()

def load_available_dates() -> list[date]:
    sql = "SELECT DISTINCT snapshot_date FROM inventory_unified ORDER BY snapshot_date DESC LIMIT 90"
    with get_conn().cursor() as cur:
        cur.execute(sql)
        return [row[0] for row in cur.fetchall()]

# ── Sidebar ──────────────────────────────────────────────────────────────────
st.sidebar.title("Filters")
available_dates = load_available_dates()
default_date = available_dates[0] if available_dates else date.today()
selected_date = st.sidebar.selectbox("Snapshot date", available_dates, index=0, format_func=str)
reorder_threshold = st.sidebar.number_input("Highlight below (total units)", min_value=0, value=50)

# ── Main ─────────────────────────────────────────────────────────────────────
st.title("Inventory Snapshot")
st.caption(f"Data as of {selected_date}")

df = load_inventory(selected_date)

if df.empty:
    st.warning("No data for this date. Run `python run_all.py` to fetch inventory.")
    st.stop()

# Rename columns for display
col_rename = {s: SOURCE_LABELS[s] for s in SOURCES}
col_rename["internal_sku"] = "SKU"
col_rename["display_name"] = "Name"
col_rename["category"] = "Category"
col_rename["total_available"] = "Total"
df_display = df.rename(columns=col_rename)

def highlight_low(row):
    color = "background-color: #fff3cd" if row["Total"] < reorder_threshold else ""
    return [color] * len(row)

st.subheader(f"{len(df)} SKUs")
st.dataframe(
    df_display.style.apply(highlight_low, axis=1),
    use_container_width=True,
    height=500,
)

# ── Bar chart ─────────────────────────────────────────────────────────────────
st.subheader("Available units by SKU")
chart_df = df.set_index("display_name")[SOURCES].rename(columns=SOURCE_LABELS)
st.bar_chart(chart_df, height=400)

# ── Source totals ─────────────────────────────────────────────────────────────
st.subheader("Totals by source")
source_totals = {SOURCE_LABELS[s]: int(df[s].sum()) for s in SOURCES if s in df.columns}
cols = st.columns(len(source_totals))
for col, (label, total) in zip(cols, source_totals.items()):
    col.metric(label, f"{total:,}")
