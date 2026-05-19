"""
TikTok Shop FBT (Fulfilled by TikTok) inventory connector.

Endpoints:
  MCF status:       GET  /fbt/202601/merchants/mcf_status
  Inventory search: POST /fbt/202408/inventory/search
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


def _get_token() -> str:
    global _access_token
    if not _access_token:
        _access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    return _access_token


def _sign(app_secret: str, path: str, params: dict, body: str = "") -> str:
    """HMAC-SHA256 signature — access_token excluded from signing, sent in header."""
    exclude = {"sign", "access_token"}
    sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()) if k not in exclude)
    to_sign = app_secret + path + sorted_params + body + app_secret
    return hmac.new(app_secret.encode(), to_sign.encode(), hashlib.sha256).hexdigest()


def _base_params(version: str) -> dict:
    return {
        "app_key": os.environ["TIKTOK_APP_KEY"],
        "timestamp": str(int(time.time())),
        "version": version,
        "shop_id": os.environ.get("TIKTOK_SHOP_ID", ""),
        "shop_cipher": os.environ.get("TIKTOK_SHOP_CYPHER", ""),
    }


def _get(path: str, version: str, extra_params: dict | None = None) -> dict:
    app_secret = os.environ["TIKTOK_APP_SECRET"]
    params = {**_base_params(version), **(extra_params or {})}
    params["sign"] = _sign(app_secret, path, params)

    resp = requests.get(
        BASE_URL + path,
        params=params,
        headers={"x-tts-access-token": _get_token()},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"TikTok GET error on {path}: {data.get('message')} (code {data.get('code')})")
    return data.get("data", {})


def _post(path: str, version: str, body: dict, extra_params: dict | None = None) -> dict:
    app_secret = os.environ["TIKTOK_APP_SECRET"]
    body_str = json.dumps(body, separators=(",", ":"))
    params = {**_base_params(version), **(extra_params or {})}
    params["sign"] = _sign(app_secret, path, params, body_str)

    resp = requests.post(
        BASE_URL + path,
        params=params,
        data=body_str,
        headers={
            "Content-Type": "application/json",
            "x-tts-access-token": _get_token(),
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(f"TikTok POST error on {path}: {data.get('message')} (code {data.get('code')})")
    return data.get("data", {})



def run(snapshot_date: date) -> int:
    # Fetch all pages and aggregate per-warehouse entries by goods.id
    aggregated: dict[str, dict] = {}  # goods_id -> accumulated totals
    page_token: str | None = None

    while True:
        extra: dict = {"page_size": 100}
        if page_token:
            extra["page_token"] = page_token

        data = _post("/fbt/202408/inventory/search", version="202408", body={}, extra_params=extra)
        items = data.get("inventory", [])

        for item in items:
            goods = item.get("goods", {})
            goods_id = str(goods.get("id", ""))
            if not goods_id:
                continue

            oh = item.get("on_hand_detail", {})
            available = int(oh.get("available_quantity", 0))
            reserved  = int(oh.get("reserved_quantity", 0))
            on_hand   = int(oh.get("total_quantity", 0))
            inbound   = int(item.get("in_transit_quantity", 0))

            if goods_id not in aggregated:
                aggregated[goods_id] = {
                    "goods_id":       goods_id,
                    "reference_code": goods.get("reference_code", ""),
                    "name":           goods.get("name", ""),
                    "available":      0,
                    "reserved":       0,
                    "on_hand":        0,
                    "inbound":        0,
                    "raw_items":      [],
                }
            agg = aggregated[goods_id]
            agg["available"] += available
            agg["reserved"]  += reserved
            agg["on_hand"]   += on_hand
            agg["inbound"]   += inbound
            agg["raw_items"].append(item)

        page_token = data.get("next_page_token")
        if not page_token or not items:
            break

    # Resolve SKUs using reference_code (matches internal SKU format e.g. SCL-0117)
    ref_codes = [v["reference_code"] for v in aggregated.values() if v["reference_code"]]
    sku_map = resolve_internal_skus(SOURCE, ref_codes)

    rows = []
    for agg in aggregated.values():
        ref = agg["reference_code"]
        rows.append({
            "snapshot_date": snapshot_date,
            "source":        SOURCE,
            "internal_sku":  sku_map.get(ref),
            "external_id":   agg["goods_id"],
            "external_sku":  ref,
            "qty_on_hand":   agg["on_hand"],
            "qty_reserved":  agg["reserved"],
            "qty_available": agg["available"],
            "qty_inbound":   agg["inbound"],
            "raw_data":      json.dumps(agg["raw_items"]),
        })

    return upsert_snapshots(rows)
