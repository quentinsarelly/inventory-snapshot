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


def upsert_location_snapshots(rows: list[dict]) -> int:
    """Insert or update inventory_location_snapshots rows (retail store breakdown)."""
    if not rows:
        return 0
    payload = [{**r, "snapshot_date": str(r["snapshot_date"])} for r in rows]
    get_client().table("inventory_location_snapshots").upsert(
        payload, on_conflict="snapshot_date,location_id,external_sku"
    ).execute()
    return len(rows)


# Synthetic source name under which snapshot_coverage reports the retail
# location table, which has no `source` column of its own.
RETAIL_SOURCE = "retail_locations"


def count_snapshot_rows(snapshot_date, source: str, mapped_only: bool = False) -> int:
    """Rows written for one source on one snapshot date.

    Used to detect a source that completed without raising but wrote nothing —
    a silent gap, which matters because every connector reads *current*
    inventory and so a missing day can only be refilled on the day itself.
    """
    table = "inventory_location_snapshots" if source == RETAIL_SOURCE else "inventory_snapshots"
    q = (
        get_client()
        .table(table)
        .select("id", count="exact")
        .eq("snapshot_date", str(snapshot_date))
    )
    if source != RETAIL_SOURCE:
        q = q.eq("source", source)
    if mapped_only:
        q = q.not_.is_("internal_sku", "null")
    return q.limit(1).execute().count or 0


def snapshot_coverage(snapshot_date, sources: list[str]) -> dict[str, tuple[int, int]]:
    """{source: (rows_written, rows_mapped)} for one snapshot date.

    Reads the snapshot_coverage view in a single query. Falls back to per-source
    count queries if the view has not been created yet (see schema.sql), which
    costs one round trip per source but keeps callers working either way.

    Sources absent from the result wrote nothing at all and are reported as
    (0, 0), so a caller can distinguish "no rows" from "rows but none mapped".
    """
    out: dict[str, tuple[int, int]] = {s: (0, 0) for s in sources}
    try:
        rows = (
            get_client()
            .table("snapshot_coverage")
            .select("source,rows_written,rows_mapped")
            .eq("snapshot_date", str(snapshot_date))
            .execute()
            .data
        )
        for r in rows:
            if r["source"] in out:
                out[r["source"]] = (r["rows_written"] or 0, r["rows_mapped"] or 0)
        return out
    except Exception:
        for s in sources:
            out[s] = (
                count_snapshot_rows(snapshot_date, s),
                count_snapshot_rows(snapshot_date, s, mapped_only=True),
            )
        return out


def refresh_unified_view():
    get_client().rpc("refresh_inventory_unified").execute()
