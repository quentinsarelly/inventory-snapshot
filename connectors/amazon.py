"""
Amazon SP-API inventory connector (FBA) — Reports API.

Requests a GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA bulk report instead of
paginating getInventorySummaries, avoiding per-call daily quota exhaustion
on large catalogs (the original cause of QuotaExceeded errors on amazon_mx).

Requires: AMAZON_LWA_APP_ID, AMAZON_LWA_CLIENT_SECRET,
          AMAZON_REFRESH_TOKEN_US / _MX,
          AMAZON_AWS_ACCESS_KEY, AMAZON_AWS_SECRET_KEY, AMAZON_ROLE_ARN
"""
import csv
import gzip
import io
import json
import os
import time
from datetime import date

import requests as http_requests
from dotenv import load_dotenv
from sp_api.api import Reports
from sp_api.base import Marketplaces, SellingApiException

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

MARKETPLACE_MAP = {
    "us": (Marketplaces.US, "AMAZON_REFRESH_TOKEN_US", "amazon_us"),
    "mx": (Marketplaces.MX, "AMAZON_REFRESH_TOKEN_MX", "amazon_mx"),
}

REPORT_TYPE    = "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA"
INITIAL_WAIT   = 15   # seconds before first status check
POLL_INTERVAL  = 15   # seconds between subsequent checks
POLL_TIMEOUT   = 1800 # 30 minutes max


def _credentials(refresh_token_env: str) -> dict:
    return {
        "lwa_app_id":        os.environ["AMAZON_LWA_APP_ID"],
        "lwa_client_secret": os.environ["AMAZON_LWA_CLIENT_SECRET"],
        "refresh_token":     os.environ[refresh_token_env],
        "aws_access_key":    os.environ["AMAZON_AWS_ACCESS_KEY"],
        "aws_secret_key":    os.environ["AMAZON_AWS_SECRET_KEY"],
        "role_arn":          os.environ["AMAZON_ROLE_ARN"],
    }


FATAL_RETRIES  = 3
FATAL_BACKOFF  = 120  # seconds to wait before retrying after FATAL


def _run_report(api: Reports, marketplace_id: str) -> str:
    """Request a report and poll until DONE. Returns reportDocumentId."""
    resp = api.create_report(
        reportType=REPORT_TYPE,
        marketplaceIds=[marketplace_id],
    )
    report_id = resp.payload["reportId"]

    deadline = time.monotonic() + POLL_TIMEOUT
    time.sleep(INITIAL_WAIT)
    while time.monotonic() < deadline:
        resp = api.get_report(report_id)
        status = resp.payload.get("processingStatus")
        if status == "DONE":
            return resp.payload["reportDocumentId"]
        if status in ("CANCELLED", "FATAL"):
            raise RuntimeError(f"Amazon report {report_id} ended with status {status}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"Amazon report {report_id} did not complete within {POLL_TIMEOUT}s")


def _request_and_download(api: Reports, marketplace_id: str) -> list[dict]:
    last_exc: Exception | None = None
    for attempt in range(1, FATAL_RETRIES + 1):
        try:
            document_id = _run_report(api, marketplace_id)
            break
        except RuntimeError as exc:
            last_exc = exc
            if attempt < FATAL_RETRIES:
                time.sleep(FATAL_BACKOFF)
    else:
        raise last_exc

    doc_resp = api.get_report_document(document_id)
    url = doc_resp.payload["url"]
    is_gzip = doc_resp.payload.get("compressionAlgorithm") == "GZIP"

    raw = http_requests.get(url, timeout=120).content
    if is_gzip:
        raw = gzip.decompress(raw)
    text = raw.decode("iso-8859-1")

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return list(reader)


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


def _int(rec: dict, key: str) -> int:
    try:
        return int(float(rec.get(key) or 0))
    except (ValueError, TypeError):
        return 0


def run(snapshot_date: date, marketplace: str = "us") -> int:
    mp, token_env, source = MARKETPLACE_MAP[marketplace]
    creds = _credentials(token_env)
    api = Reports(marketplace=mp, credentials=creds)

    try:
        records = _request_and_download(api, mp.marketplace_id)
    except SellingApiException as exc:
        raise RuntimeError(f"SP-API error ({marketplace}): {exc}") from exc

    # Deduplicate: one row per ASIN; multiple seller SKUs share the same inventory counts.
    # US: prefer the SKU ending with -USA. MX: prefer the clean base SKU.
    by_asin: dict[str, dict] = {}
    for rec in records:
        asin = rec.get("asin", "").strip()
        sku  = rec.get("sku", "").strip()
        if not asin:
            continue
        if asin not in by_asin:
            by_asin[asin] = rec
        else:
            current_sku = by_asin[asin].get("sku", "")
            if marketplace == "us":
                if sku.upper().endswith("-USA"):
                    by_asin[asin] = rec
            else:
                curr_ok = _is_canonical_mx(current_sku)
                new_ok  = _is_canonical_mx(sku)
                if new_ok and (not curr_ok or sku < current_sku):
                    by_asin[asin] = rec

    external_ids = list(by_asin.keys())
    sku_map = resolve_internal_skus(source, external_ids)

    rows = []
    for asin, rec in by_asin.items():
        inbound = (
            _int(rec, "afn-inbound-working-quantity")
            + _int(rec, "afn-inbound-shipped-quantity")
            + _int(rec, "afn-inbound-receiving-quantity")
        )
        rows.append({
            "snapshot_date": snapshot_date,
            "source":        source,
            "internal_sku":  sku_map.get(asin),
            "external_id":   asin,
            "external_sku":  rec.get("sku", "").strip(),
            "qty_on_hand":   _int(rec, "afn-warehouse-quantity"),
            "qty_reserved":  _int(rec, "reserved-qty-total"),
            "qty_available": _int(rec, "afn-fulfillable-quantity"),
            "qty_inbound":   inbound,
            "raw_data":      json.dumps(rec),
        })

    return upsert_snapshots(rows)
