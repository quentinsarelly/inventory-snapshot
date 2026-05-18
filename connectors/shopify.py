"""
Shopify inventory connector — supports US and MX stores.

Fetches available inventory aggregated across all locations for every variant SKU.
Requires: SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET,
          SHOPIFY_US_SHOP_DOMAIN, SHOPIFY_MX_SHOP_DOMAIN in environment.
"""
import json
import os
import time
from datetime import date

import requests
from dotenv import load_dotenv

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-01")
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

STORE_CONFIG = {
    "us": {"domain_env": "SHOPIFY_US_SHOP_DOMAIN", "source": "shopify_us"},
    "mx": {"domain_env": "SHOPIFY_MX_SHOP_DOMAIN", "source": "shopify_mx"},
}


def _get_token(shop_domain: str) -> str:
    resp = requests.post(
        f"https://{shop_domain}.myshopify.com/admin/oauth/access_token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"X-Shopify-Access-Token": token, "Content-Type": "application/json"})
    return s


def _next_page_url(link_header: str) -> str | None:
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def _fetch_item_to_sku(session: requests.Session, base_url: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    url = f"{base_url}/products.json"
    params: dict = {"limit": 250, "fields": "variants"}
    while url:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 2)))
            continue
        resp.raise_for_status()
        for product in resp.json().get("products", []):
            for variant in product.get("variants", []):
                iid = str(variant["inventory_item_id"])
                mapping[iid] = variant.get("sku") or str(variant["id"])
        url = _next_page_url(resp.headers.get("Link", ""))
        params = {}
    return mapping


def _fetch_inventory_levels(
    session: requests.Session, base_url: str, item_ids: list[str]
) -> dict[str, int]:
    """Returns {inventory_item_id: total_available} summed across all locations.

    Filters by inventory_item_ids in batches of 250 — the only reliable approach
    since /inventory_levels.json returns 422 without a required filter param.
    """
    totals: dict[str, int] = {}
    for i in range(0, len(item_ids), 250):
        batch = ",".join(item_ids[i : i + 250])
        url: str | None = f"{base_url}/inventory_levels.json"
        params: dict = {"limit": 250, "inventory_item_ids": batch}
        while url:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(int(resp.headers.get("Retry-After", 2)))
                continue
            resp.raise_for_status()
            for level in resp.json().get("inventory_levels", []):
                iid = str(level["inventory_item_id"])
                totals[iid] = totals.get(iid, 0) + (level.get("available") or 0)
            url = _next_page_url(resp.headers.get("Link", ""))
            params = {}
    return totals


def run(snapshot_date: date, store: str = "us") -> int:
    cfg = STORE_CONFIG[store]
    shop_domain = os.environ[cfg["domain_env"]]
    source = cfg["source"]

    token = _get_token(shop_domain)
    session = _session(token)
    base_url = f"https://{shop_domain}.myshopify.com/admin/api/{API_VERSION}"

    item_to_sku = _fetch_item_to_sku(session, base_url)
    level_totals = _fetch_inventory_levels(session, base_url, list(item_to_sku.keys()))

    external_ids = list(item_to_sku.values())
    sku_map = resolve_internal_skus(source, external_ids)

    rows = []
    for iid, qty in level_totals.items():
        external_sku = item_to_sku.get(iid, iid)
        rows.append({
            "snapshot_date": snapshot_date,
            "source": source,
            "internal_sku": sku_map.get(external_sku),
            "external_id": iid,
            "external_sku": external_sku,
            "qty_on_hand": qty,
            "qty_reserved": None,
            "qty_available": qty,
            "qty_inbound": None,
            "raw_data": json.dumps({"inventory_item_id": iid, "available": qty}),
        })

    return upsert_snapshots(rows)
