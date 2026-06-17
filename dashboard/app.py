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
TABLE_SOURCES = ["amazon_us", "amazon_mx", "tiktok_us", "us_3pl", "mx_3pl"]
SOURCE_LABELS = {
    "shopify_us": "Shopify US",
    "shopify_mx": "Shopify MX",
    "amazon_us":  "Amazon US",
    "amazon_mx":  "Amazon MX",
    "tiktok_us":  "TikTok US",
    "us_3pl":     "US 3PL",
    "mx_3pl":     "MX 3PL (ShipHero)",
}

INBOUND_SOURCES = ["amazon_us", "amazon_mx", "tiktok_us"]
INBOUND_LABELS  = {
    "amazon_us":  "Amazon US (Inbound)",
    "amazon_mx":  "Amazon MX (Inbound)",
    "tiktok_us":  "TikTok US (Inbound)",
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
    resp = (
        get_client()
        .table("inventory_snapshots")
        .select("internal_sku,source,qty_available,qty_inbound")
        .eq("snapshot_date", snapshot_date)
        .not_.is_("internal_sku", "null")
        .limit(5000)
        .execute()
    )
    if not resp.data:
        return pd.DataFrame()

    df = pd.DataFrame(resp.data)

    avail = (
        df.pivot_table(index="internal_sku", columns="source", values="qty_available", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    for src in SOURCES:
        if src not in avail.columns:
            avail[src] = 0
    avail["total_available"] = avail[SOURCES].sum(axis=1)

    # Inbound columns for Amazon and TikTok
    inbound = (
        df[df["source"].isin(INBOUND_SOURCES)]
        .pivot_table(index="internal_sku", columns="source", values="qty_inbound", aggfunc="sum", fill_value=0)
        .reset_index()
    )
    inbound.columns = ["internal_sku"] + [f"inbound_{c}" for c in inbound.columns if c != "internal_sku"]
    for src in INBOUND_SOURCES:
        col = f"inbound_{src}"
        if col not in inbound.columns:
            inbound[col] = 0

    pivot = avail.merge(inbound, on="internal_sku", how="left").fillna(0)
    int_cols = SOURCES + [f"inbound_{s}" for s in INBOUND_SOURCES] + ["total_available"]
    pivot[int_cols] = pivot[int_cols].astype(int)

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


@st.cache_data(ttl=300)
def load_instock_rates(snapshot_date: str) -> pd.DataFrame:
    # Catalog size per source = number of mappings in sku_mappings
    maps = get_client().table("sku_mappings").select("source,internal_sku").execute()
    catalog: dict[str, set] = {}
    for r in maps.data:
        catalog.setdefault(r["source"], set()).add(r["internal_sku"])

    # In-stock SKUs per source = distinct mapped internal_skus with qty_available > 0
    snap = (
        get_client()
        .table("inventory_snapshots")
        .select("source,internal_sku,qty_available")
        .eq("snapshot_date", snapshot_date)
        .not_.is_("internal_sku", "null")
        .execute()
    )
    in_stock: dict[str, set] = {}
    for r in snap.data:
        if (r["qty_available"] or 0) > 0:
            in_stock.setdefault(r["source"], set()).add(r["internal_sku"])

    rows = []
    all_catalog: set = set()
    all_in_stock: set = set()
    for src in SOURCES:
        if src not in catalog:
            continue
        total = len(catalog[src])
        n = len(in_stock.get(src, set()))
        rows.append({
            "Channel":  SOURCE_LABELS[src],
            "In Stock": n,
            "Catalog":  total,
            "Rate":     f"{n / total:.0%}" if total else "—",
        })
        all_catalog |= catalog[src]
        all_in_stock |= in_stock.get(src, set())

    overall_total = len(all_catalog)
    overall_n = len(all_in_stock)
    rows.append({
        "Channel":  "Overall",
        "In Stock": overall_n,
        "Catalog":  overall_total,
        "Rate":     f"{overall_n / overall_total:.0%}" if overall_total else "—",
    })
    return pd.DataFrame(rows)


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
df_retail = load_retail_inventory(selected_date)

if df.empty:
    st.warning("No data for this date.")
    st.stop()

# Merge retail total into main table
if not df_retail.empty:
    df = df.merge(df_retail[["internal_sku", "Total Retail"]], on="internal_sku", how="left")
    df["Total Retail"] = df["Total Retail"].fillna(0).astype(int)
else:
    df["Total Retail"] = 0

if selected_category != "All":
    df = df[df["category"] == selected_category]

# ── Source totals ─────────────────────────────────────────────────────────────
st.subheader("Totals by source")
cols = st.columns(len(SOURCES))
for col, src in zip(cols, SOURCES):
    if src in df.columns:
        col.metric(SOURCE_LABELS[src], f"{int(df[src].sum()):,}")

st.divider()

# ── In-stock rate ─────────────────────────────────────────────────────────────
st.subheader("In-stock rate (SKUs with ≥ 1 unit available)")
rates_df = load_instock_rates(selected_date)
st.dataframe(rates_df, use_container_width=False, hide_index=True)

st.divider()

# ── Table ─────────────────────────────────────────────────────────────────────
df["table_total"] = df[TABLE_SOURCES].sum(axis=1)

col_rename = {s: SOURCE_LABELS[s] for s in TABLE_SOURCES}
col_rename.update({f"inbound_{s}": INBOUND_LABELS[s] for s in INBOUND_SOURCES})
col_rename.update({"internal_sku": "SKU", "display_name": "Name",
                   "category": "Category", "table_total": "Total",
                   "Total Retail": "Retail"})

# Column order: meta, then each table source followed by its inbound column if applicable
ordered_cols = ["internal_sku", "display_name", "category"]
for src in TABLE_SOURCES:
    ordered_cols.append(src)
    if src in INBOUND_SOURCES:
        ordered_cols.append(f"inbound_{src}")
ordered_cols += ["Total Retail", "table_total"]

df_display = df[ordered_cols].rename(columns=col_rename)

def highlight_low(row):
    color = "background-color: #fff3cd" if row["Total"] < reorder_threshold else ""
    return [color] * len(row)

st.subheader(f"Total stock available — {len(df)} SKUs")
st.dataframe(
    df_display.style.apply(highlight_low, axis=1),
    use_container_width=True,
    height=500,
)

st.divider()

# ── Total Mexico ───────────────────────────────────────────────────────────────
MX_SOURCES = ["amazon_mx", "inbound_amazon_mx", "Total Retail", "mx_3pl"]
MX_LABELS  = {
    "amazon_mx":         "Amazon MX",
    "inbound_amazon_mx": "Amazon MX (Inbound)",
    "Total Retail":      "Retail",
    "mx_3pl":            "MX 3PL (ShipHero)",
    "mx_total":          "Total MX",
}

df_mx = df[["internal_sku", "display_name", "category"] + MX_SOURCES].copy()
df_mx["mx_total"] = df_mx[MX_SOURCES].sum(axis=1)
df_mx = df_mx.sort_values("mx_total", ascending=False)
mx_display_cols = ["internal_sku", "display_name", "category"] + MX_SOURCES + ["mx_total"]
mx_col_rename = {**MX_LABELS, "internal_sku": "SKU", "display_name": "Name", "category": "Category"}

st.subheader(f"Total Mexico — {len(df_mx)} SKUs")
st.dataframe(df_mx[mx_display_cols].rename(columns=mx_col_rename), use_container_width=True, height=500)

st.divider()

# ── Total US ───────────────────────────────────────────────────────────────────
US_SOURCES = ["amazon_us", "inbound_amazon_us", "tiktok_us", "inbound_tiktok_us", "us_3pl"]
US_LABELS  = {
    "amazon_us":         "Amazon US",
    "inbound_amazon_us": "Amazon US (Inbound)",
    "tiktok_us":         "TikTok US",
    "inbound_tiktok_us": "TikTok US (Inbound)",
    "us_3pl":            "US 3PL",
    "us_total":          "Total US",
}

df_us = df[["internal_sku", "display_name", "category"] + US_SOURCES].copy()
df_us["us_total"] = df_us[US_SOURCES].sum(axis=1)
df_us = df_us.sort_values("us_total", ascending=False)
us_display_cols = ["internal_sku", "display_name", "category"] + US_SOURCES + ["us_total"]
us_col_rename = {**US_LABELS, "internal_sku": "SKU", "display_name": "Name", "category": "Category"}

st.subheader(f"Total US — {len(df_us)} SKUs")
st.dataframe(df_us[us_display_cols].rename(columns=us_col_rename), use_container_width=True, height=500)

st.divider()

# ── Retail location breakdown ──────────────────────────────────────────────────
st.subheader("Retail inventory by location (Shopify MX stores + Julius)")

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
