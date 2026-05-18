import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])

def upsert_snapshots(rows: list[dict]) -> int:
    """
    Insert or update inventory_snapshots rows.
    Each dict must have: snapshot_date, source, external_id, external_sku,
    qty_on_hand, qty_reserved, qty_available, qty_inbound, raw_data.
    Returns number of rows affected.
    """
    if not rows:
        return 0

    sql = """
        INSERT INTO inventory_snapshots
            (snapshot_date, source, internal_sku, external_id, external_sku,
             qty_on_hand, qty_reserved, qty_available, qty_inbound, raw_data)
        VALUES (
            %(snapshot_date)s, %(source)s, %(internal_sku)s, %(external_id)s,
            %(external_sku)s, %(qty_on_hand)s, %(qty_reserved)s,
            %(qty_available)s, %(qty_inbound)s, %(raw_data)s)
        ON CONFLICT (snapshot_date, source, external_id)
        DO UPDATE SET
            external_sku   = EXCLUDED.external_sku,
            internal_sku   = EXCLUDED.internal_sku,
            qty_on_hand    = EXCLUDED.qty_on_hand,
            qty_reserved   = EXCLUDED.qty_reserved,
            qty_available  = EXCLUDED.qty_available,
            qty_inbound    = EXCLUDED.qty_inbound,
            raw_data       = EXCLUDED.raw_data,
            fetched_at     = NOW()
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, sql, rows)
        conn.commit()

    return len(rows)

def resolve_internal_skus(source: str, external_ids: list[str]) -> dict[str, str]:
    """Returns {external_id: internal_sku} for known mappings."""
    if not external_ids:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT external_id, internal_sku FROM sku_mappings "
                "WHERE source = %s AND external_id = ANY(%s)",
                (source, external_ids),
            )
            return {row[0]: row[1] for row in cur.fetchall()}

def refresh_unified_view():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY inventory_unified")
        conn.commit()
