"""
US 3PL inventory connector — stub.

Replace the body of run() once the 3PL provider and API docs are confirmed.
Common providers: ShipBob, ShipMonk, Whiplash — all have REST inventory endpoints.

Required env vars (fill in .env once confirmed):
    US_3PL_BASE_URL
    US_3PL_API_KEY
"""
import json
import os
from datetime import date

import requests
from dotenv import load_dotenv

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

SOURCE = "us_3pl"

def run(snapshot_date: date) -> int:
    base_url = os.environ.get("US_3PL_BASE_URL", "")
    api_key = os.environ.get("US_3PL_API_KEY", "")

    if not base_url or not api_key:
        raise RuntimeError("US_3PL_BASE_URL and US_3PL_API_KEY are not set — provider TBD")

    # ------------------------------------------------------------------
    # TODO: replace with real API call once provider is confirmed.
    # Typical pattern (e.g. ShipBob):
    #
    #   resp = requests.get(
    #       f"{base_url}/inventory",
    #       headers={"Authorization": f"Bearer {api_key}"},
    #       params={"Page": 1, "PageSize": 250},
    #   )
    #   items = resp.json()
    # ------------------------------------------------------------------

    raise NotImplementedError("US 3PL provider not yet confirmed — update this connector")
