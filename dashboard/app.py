"""Streamlit dashboard — daily inventory by SKU across all sources."""
import os
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

st.set_page_config(page_title="Sarelly Inventory", layout="wide")

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
def get_client() -> Client:
    url = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL", ""))
    key = st.secrets.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_SERVICE_KEY", ""))
    return create_client(url, key)


@st.cache_data(ttl=300)
def load_available_dates() -> list[str]:
    resp = (
        get_client()
        .table("inventory_snapshots")
        .select("snapshot_date")
        .order("snapshot_date", desc=True)
        .limit(5000)
        .execute()
    )
    return sorted(set(r["snapshot_date"] for r in resp.data), reverse=True)[:90]


@st.cache_data(ttl=300)
def load_inventory(snapshot_date: str) -> pd.DataFrame:
    # Query raw snapshots and pivot in Python — avoids dependency on the
    # materialized view being refreshed and always reflects the latest run.
    resp = (
        get_client()
        .table("inventory_snapshots")
        .select("internal_sku,source,qty_available")
        .eq("snapshot_date", snapshot_date)
        .not_.is_("internal_sku", "null")
        .limit(5000)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame()

    df = pd.DataFrame(resp.data)
    pivot = (
        df.pivot_table(index="internal_sku", columns="source", values="qty_available", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    for src in SOURCES:
        if src not in pivot.columns:
            pivot[src] = 0
    pivot["total_available"] = pivot[SOURCES].sum(axis=1)

    skus = pivot["internal_sku"].tolist()
    master = (
        get_client()
        .table("sku_master")
        .select("internal_sku,display_name,category")
        .in_("internal_sku", skus)
        .execute()
    )
    master_df = pd.DataFrame(master.data) if master.data else pd.DataFrame(columns=["internal_sku", "display_name", "category"])

    return pivot.merge(master_df, on="internal_sku", how="left").sort_values("total_available", ascending=False)


RETAIL_LOCATIONS = ["Andares", "Monterrey", "Mérida", "Perisur", "Queretaro", "Julius"]


@st.cache_data(ttl=300)
def load_retail_inventory(snapshot_date: str) -> pd.DataFrame:
    resp = (
        get_client()
        .table("inventory_location_snapshots")
        .select("internal_sku,location_name,qty_available")
        .eq("snapshot_date", snapshot_date)
        .not_.is_("internal_sku", "null")
        .limit(5000)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame()

    df = pd.DataFrame(resp.data)
    pivot = (
        df.pivot_table(index="internal_sku", columns="location_name", values="qty_available", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    for loc in RETAIL_LOCATIONS:
        if loc not in pivot.columns:
            pivot[loc] = 0
    pivot["Total Retail"] = pivot[RETAIL_LOCATIONS].sum(axis=1)

    skus = pivot["internal_sku"].tolist()
    master = (
        get_client()
        .table("sku_master")
        .select("internal_sku,display_name,category")
        .in_("internal_sku", skus)
        .execute()
    )
    master_df = pd.DataFrame(master.data) if master.data else pd.DataFrame(columns=["internal_sku", "display_name", "category"])
    return pivot.merge(master_df, on="internal_sku", how="left").sort_values("Total Retail", ascending=False)


# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("Filters")
available_dates = load_available_dates()
selected_date = st.sidebar.selectbox("Snapshot date", available_dates, index=0)
reorder_threshold = st.sidebar.number_input("Highlight below (total units)", min_value=0, value=50)
selected_category = st.sidebar.selectbox("Category", ["All"] + sorted({
    r["category"] for r in get_client().table("sku_master").select("category").execute().data
    if r.get("category")
}))

# ── Main ──────────────────────────────────────────────────────────────────────
st.title("Sarelly — Inventory Snapshot")
st.caption(f"Data as of {selected_date}")

df = load_inventory(selected_date)

if df.empty:
    st.warning("No data for this date.")
    st.stop()

if selected_category != "All":
    df = df[df["category"] == selected_category]

# ── Source totals ─────────────────────────────────────────────────────────────
st.subheader("Totals by source")
cols = st.columns(len(SOURCES))
for col, src in zip(cols, SOURCES):
    if src in df.columns:
        col.metric(SOURCE_LABELS[src], f"{int(df[src].sum()):,}")

st.divider()

# ── Table ─────────────────────────────────────────────────────────────────────
col_rename = {s: SOURCE_LABELS[s] for s in SOURCES}
col_rename.update({"internal_sku": "SKU", "display_name": "Name",
                   "category": "Category", "total_available": "Total"})
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
st.subheader("Available units by SKU (top 30)")
chart_df = df.head(30).set_index("display_name")[SOURCES].rename(columns=SOURCE_LABELS)
st.bar_chart(chart_df, height=400)

st.divider()

# ── Retail location breakdown ──────────────────────────────────────────────────
st.subheader("Retail inventory by location (Shopify MX stores + Julius)")

df_retail = load_retail_inventory(selected_date)

if df_retail.empty:
    st.info("No retail location data for this date. Run the connector to populate.")
else:
    if selected_category != "All":
        df_retail = df_retail[df_retail["category"] == selected_category]

    retail_col_rename = {"internal_sku": "SKU", "display_name": "Name", "category": "Category"}
    display_cols = ["internal_sku", "display_name", "category"] + RETAIL_LOCATIONS + ["Total Retail"]
    st.dataframe(
        df_retail[display_cols].rename(columns=retail_col_rename),
        use_container_width=True,
        height=500,
    )
