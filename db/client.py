import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _client


def upsert_snapshots(rows: list[dict]) -> int:
    """
    Insert or update inventory_snapshots rows.
    Each dict must have: snapshot_date, source, external_id, external_sku,
    qty_on_hand, qty_reserved, qty_available, qty_inbound, raw_data.
    Returns number of rows affected.
    """
    if not rows:
        return 0

    # PostgREST expects date as ISO string
    payload = [
        {**r, "snapshot_date": str(r["snapshot_date"])}
        for r in rows
    ]

    get_client().table("inventory_snapshots").upsert(
        payload,
        on_conflict="snapshot_date,source,external_id",
    ).execute()

    return len(rows)


def resolve_internal_skus(source: str, external_ids: list[str]) -> dict[str, str]:
    """Returns {external_id: internal_sku} for known mappings."""
    if not external_ids:
        return {}

    resp = (
        get_client()
        .table("sku_mappings")
        .select("external_id,internal_sku")
        .eq("source", source)
        .in_("external_id", external_ids)
        .execute()
    )
    return {row["external_id"]: row["internal_sku"] for row in resp.data}


def refresh_unified_view():
    get_client().rpc("refresh_inventory_unified").execute()
