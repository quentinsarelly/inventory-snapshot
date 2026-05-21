"""
Amazon SP-API inventory connector (FBA).

Fetches FBA inventory summaries for a given marketplace.
Requires: AMAZON_LWA_APP_ID, AMAZON_LWA_CLIENT_SECRET,
          AMAZON_REFRESH_TOKEN_US / _MX,
          AMAZON_AWS_ACCESS_KEY, AMAZON_AWS_SECRET_KEY, AMAZON_ROLE_ARN
"""
import json
import os
from datetime import date

from dotenv import load_dotenv
from sp_api.api import Inventories
from sp_api.base import Marketplaces, SellingApiException as SellerApiException

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

MARKETPLACE_MAP = {
    "us": (Marketplaces.US, "AMAZON_REFRESH_TOKEN_US", "amazon_us"),
    "mx": (Marketplaces.MX, "AMAZON_REFRESH_TOKEN_MX", "amazon_mx"),
}

def _credentials(refresh_token_env: str) -> dict:
    return {
        "lwa_app_id":       os.environ["AMAZON_LWA_APP_ID"],
        "lwa_client_secret": os.environ["AMAZON_LWA_CLIENT_SECRET"],
        "refresh_token":    os.environ[refresh_token_env],
        "aws_access_key":   os.environ["AMAZON_AWS_ACCESS_KEY"],
        "aws_secret_key":   os.environ["AMAZON_AWS_SECRET_KEY"],
        "role_arn":         os.environ["AMAZON_ROLE_ARN"],
    }

def run(snapshot_date: date, marketplace: str = "us") -> int:
    mp, token_env, source = MARKETPLACE_MAP[marketplace]
    creds = _credentials(token_env)

    api = Inventories(marketplace=mp, credentials=creds)

    all_items = []
    next_token = None

    while True:
        kwargs = {"details": True, "granularityType": "Marketplace", "granularityId": mp.marketplace_id}
        if next_token:
            kwargs["nextToken"] = next_token

        try:
            resp = api.get_inventory_summary_marketplace(**kwargs)
        except SellerApiException as exc:
            raise RuntimeError(f"SP-API error ({marketplace}): {exc}") from exc

        summaries = (resp.payload or {}).get("inventorySummaries", [])
        all_items.extend(summaries)

        next_token = (getattr(resp, "pagination", None) or {}).get("nextToken")
        if not next_token:
            break

    # Deduplicate: one row per ASIN. Multiple seller SKUs share identical inventory counts.
    # US: prefer the SKU ending with "-USA".
    # MX: prefer the clean base SKU (no -USA suffix, no spaces, no auto-generated strings).
    def _is_canonical_mx(sku: str) -> bool:
        s = sku.upper()
        if s.endswith("-USA") or "-USA-" in s:
            return False
        if sku.startswith("Stickered.") or sku.startswith("Uncommingled."):
            return False
        if " " in sku:
            return False
        if not (s.startswith("SAR-") or s.startswith("SCL-")):
            return False
        return True

    by_asin: dict[str, dict] = {}
    for item in all_items:
        asin = item["asin"]
        sku = item.get("sellerSku", "")
        if asin not in by_asin:
            by_asin[asin] = item
        else:
            current_sku = by_asin[asin].get("sellerSku", "")
            if marketplace == "us":
                if sku.upper().endswith("-USA"):
                    by_asin[asin] = item
            else:
                curr_ok = _is_canonical_mx(current_sku)
                new_ok = _is_canonical_mx(sku)
                if new_ok and (not curr_ok or sku < current_sku):
                    by_asin[asin] = item
    all_items = list(by_asin.values())

    external_ids = [item["asin"] for item in all_items]
    sku_map = resolve_internal_skus(source, external_ids)

    rows = []
    for item in all_items:
        asin = item["asin"]
        seller_sku = item.get("sellerSku", "")
        inv_details = item.get("inventoryDetails", {})
        fulfillable = inv_details.get("fulfillableQuantity", 0) or 0
        inbound = (
            (inv_details.get("inboundWorkingQuantity") or 0)
            + (inv_details.get("inboundShippedQuantity") or 0)
            + (inv_details.get("inboundReceivingQuantity") or 0)
        )
        reserved = (inv_details.get("reservedQuantity", {}) or {}).get("totalReservedQuantity", 0) or 0
        total = item.get("totalQuantity", 0) or 0

        rows.append({
            "snapshot_date": snapshot_date,
            "source": source,
            "internal_sku": sku_map.get(asin),
            "external_id": asin,
            "external_sku": seller_sku,
            "qty_on_hand": total,
            "qty_reserved": reserved,
            "qty_available": fulfillable,
            "qty_inbound": inbound,
            "raw_data": json.dumps(item),
        })

    return upsert_snapshots(rows)
