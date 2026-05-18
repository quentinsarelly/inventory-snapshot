"""
MX 3PL (ShipHero) inventory connector.

Fetches warehouse inventory via ShipHero's GraphQL API.
Requires: SHIPHERO_REFRESH_TOKEN in environment.
"""
import json
import os
import time
from datetime import date

import requests
from dotenv import load_dotenv

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

SOURCE = "mx_3pl"
AUTH_URL = "https://public-api.shiphero.com/auth"
GRAPHQL_URL = "https://public-api.shiphero.com/graphql"

_cached_token: dict = {}


def _get_token() -> str:
    global _cached_token
    if _cached_token.get("expires_at", 0) > time.time() + 60:
        return _cached_token["access_token"]
    resp = requests.post(
        f"{AUTH_URL}/refresh",
        json={"refresh_token": os.environ["SHIPHERO_REFRESH_TOKEN"]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _cached_token = {
        "access_token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 28 * 24 * 3600),
    }
    return _cached_token["access_token"]


def _query(gql: str, variables: dict = None) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": gql, "variables": variables or {}},
        headers={"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"ShipHero GraphQL error: {data['errors']}")
    return data


# Pagination lives on the `data` field, not the top-level `warehouse_products` query.
_INVENTORY_QUERY = """
query WarehouseProducts($after: String) {
    warehouse_products(active: true) {
        data(first: 100, after: $after) {
            edges {
                node {
                    sku
                    on_hand
                    available
                    allocated
                }
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
}
"""


def run(snapshot_date: date) -> int:
    all_nodes = []
    cursor = None

    while True:
        result = _query(_INVENTORY_QUERY, {"after": cursor})
        page = result["data"]["warehouse_products"]["data"]
        all_nodes.extend(edge["node"] for edge in page["edges"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    external_skus = [n["sku"] for n in all_nodes if n.get("sku")]
    sku_map = resolve_internal_skus(SOURCE, external_skus)

    rows = []
    for node in all_nodes:
        sku = node.get("sku", "")
        if not sku:
            continue
        rows.append({
            "snapshot_date": snapshot_date,
            "source":        SOURCE,
            "internal_sku":  sku_map.get(sku),
            "external_id":   sku,
            "external_sku":  sku,
            "qty_on_hand":   node.get("on_hand")   or 0,
            "qty_reserved":  node.get("allocated") or 0,
            "qty_available": node.get("available") or 0,
            "qty_inbound":   None,
            "raw_data":      json.dumps(node),
        })

    return upsert_snapshots(rows)
