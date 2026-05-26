"""
Shopify inventory connector — supports US and MX stores.

Fetches available inventory for active (non-archived) products only.
Archived products with remaining stock are reported separately for logistics review.
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
    "us": {"domain_env": "SHOPIFY_US_SHOP_DOMAIN", "token_env": "SHOPIFY_US_ACCESS_TOKEN", "source": "shopify_us"},
    "mx": {"domain_env": "SHOPIFY_MX_SHOP_DOMAIN", "token_env": "SHOPIFY_MX_ACCESS_TOKEN", "source": "shopify_mx"},
}


def _get_token(shop_domain: str, token_env: str) -> str:
    # Use pre-fetched token if available (required for CI where client_credentials is blocked).
    # client_credentials tokens don't expire, so this is safe to store as a secret.
    if token := os.getenv(token_env):
        return token
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


def _fetch_products(session: requests.Session, base_url: str) -> tuple[dict[str, str], dict[str, str]]:
    """
    Returns (active, archived) where each is {inventory_item_id: sku}.
    active:   status != 'archived' — included in daily snapshot.
    archived: status == 'archived' — excluded from snapshot; reported if stock > 0.
    """
    active: dict[str, str] = {}
    archived: dict[str, str] = {}

    url = f"{base_url}/products.json"
    params: dict = {"limit": 250, "fields": "status,variants"}
    while url:
        resp = session.get(url, params=params, timeout=30)
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 2)))
            continue
        resp.raise_for_status()
        for product in resp.json().get("products", []):
            is_archived = product.get("status") == "archived"
            for variant in product.get("variants", []):
                iid = str(variant["inventory_item_id"])
                sku = variant.get("sku") or str(variant["id"])
                if is_archived:
                    archived[iid] = sku
                else:
                    active[iid] = sku
        url = _next_page_url(resp.headers.get("Link", ""))
        params = {}
    return active, archived


def _fetch_inventory_levels(
    session: requests.Session, base_url: str, item_ids: list[str]
) -> dict[str, int]:
    """Returns {inventory_item_id: total_available} summed across all locations."""
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

    token = _get_token(shop_domain, cfg["token_env"])
    session = _session(token)
    base_url = f"https://{shop_domain}.myshopify.com/admin/api/{API_VERSION}"

    active, archived = _fetch_products(session, base_url)

    # Fetch inventory for all products (active + archived) in one pass
    all_ids = list(set(active) | set(archived))
    level_totals = _fetch_inventory_levels(session, base_url, all_ids)

    # Report archived products that still have stock — for logistics review
    archived_with_stock = [
        (archived[iid], level_totals[iid])
        for iid in archived
        if level_totals.get(iid, 0) > 0
    ]
    if archived_with_stock:
        print(f"  [LOGISTICS] {source}: {len(archived_with_stock)} archived products with stock remaining:")
        for sku, qty in sorted(archived_with_stock, key=lambda x: -x[1]):
            print(f"    {sku:<30} qty={qty}")

    # Only snapshot active products
    external_ids = list(active.values())
    sku_map = resolve_internal_skus(source, external_ids)

    rows = []
    for iid, qty in level_totals.items():
        if iid not in active:
            continue
        external_sku = active[iid]
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
