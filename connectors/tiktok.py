"""
TikTok Shop FBT (Fulfilled by TikTok) inventory connector.

Fetches warehouse inventory from TikTok Shop Open Platform.
Requires: TIKTOK_APP_KEY, TIKTOK_APP_SECRET, TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN
"""
import hashlib
import hmac
import json
import os
import time
from datetime import date

import requests
from dotenv import load_dotenv

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

SOURCE = "tiktok_us"
BASE_URL = "https://open-api.tiktokglobalshop.com"

_access_token: str = ""


def _refresh_token() -> str:
    """Exchange the refresh token for a new access token and update the module-level cache."""
    global _access_token
    app_key = os.environ["TIKTOK_APP_KEY"]
    app_secret = os.environ["TIKTOK_APP_SECRET"]
    refresh_token = os.environ["TIKTOK_REFRESH_TOKEN"]
    resp = requests.get(
        f"{BASE_URL}/api/token/refreshToken",
        params={
            "app_key": app_key,
            "app_secret": app_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"TikTok token refresh failed: {data.get('message')} (code {data.get('code')})")
    _access_token = data["data"]["access_token"]
    return _access_token


def _get_token() -> str:
    global _access_token
    if not _access_token:
        _access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    return _access_token


def _sign(app_secret: str, path: str, params: dict, body: str = "") -> str:
    """HMAC-SHA256 signature — access_token is excluded from signing (sent in header instead)."""
    exclude = {"sign", "access_token"}
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()) if k not in exclude)
    to_sign = app_secret + path + sorted_params + body + app_secret
    return hmac.new(app_secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()


def _get(path: str, extra_params: dict | None = None, _retried: bool = False) -> dict:
    app_key = os.environ["TIKTOK_APP_KEY"]
    app_secret = os.environ["TIKTOK_APP_SECRET"]
    shop_id = os.environ.get("TIKTOK_SHOP_ID", "")
    shop_cipher = os.environ.get("TIKTOK_SHOP_CYPHER", "")

    params: dict = {
        "app_key": app_key,
        "timestamp": str(int(time.time())),
        "version": "202309",
        **({"shop_id": shop_id} if shop_id else {}),
        **({"shop_cipher": shop_cipher} if shop_cipher else {}),
        **(extra_params or {}),
    }
    params["sign"] = _sign(app_secret, path, params)

    resp = requests.get(
        BASE_URL + path,
        params=params,
        headers={"Content-Type": "application/json", "x-tts-access-token": _get_token()},
        timeout=30,
    )

    # On 401/403, try refreshing the token once
    if resp.status_code in (401, 403) and not _retried:
        _refresh_token()
        return _get(path, extra_params, _retried=True)

    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        # TikTok uses code=4 for auth errors — retry with fresh token
        if data.get("code") in (4, 40001, 40002) and not _retried:
            _refresh_token()
            return _get(path, extra_params, _retried=True)
        raise RuntimeError(f"TikTok API error: {data.get('message')} (code {data.get('code')})")
    return data.get("data", {})

def run(snapshot_date: date) -> int:
    all_items = []
    page = 1
    page_size = 100

    while True:
        data = _get(
            "/api/fulfillment/warehouse/inventory/list",
            {"page_number": str(page), "page_size": str(page_size)},
        )
        items = data.get("inventory_list") or data.get("list", [])
        all_items.extend(items)

        total = data.get("total", 0)
        if len(all_items) >= total or not items:
            break
        page += 1

    external_ids = [item.get("seller_sku", "") for item in all_items if item.get("seller_sku")]
    sku_map = resolve_internal_skus(SOURCE, external_ids)

    rows = []
    for item in all_items:
        seller_sku = item.get("seller_sku", "")
        product_id = str(item.get("product_id", seller_sku))
        available = item.get("available_quantity", 0) or 0
        reserved = item.get("reserved_quantity", 0) or 0
        on_hand = available + reserved

        rows.append({
            "snapshot_date": snapshot_date,
            "source": SOURCE,
            "internal_sku": sku_map.get(seller_sku),
            "external_id": product_id,
            "external_sku": seller_sku,
            "qty_on_hand": on_hand,
            "qty_reserved": reserved,
            "qty_available": available,
            "qty_inbound": None,
            "raw_data": json.dumps(item),
        })

    return upsert_snapshots(rows)
