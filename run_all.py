#!/usr/bin/env python3
"""Run all inventory connectors and refresh the unified view.

Usage:
    python run_all.py            # fetch + write to database
    python run_all.py --dry-run  # fetch only, print quality report, no writes
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors import shopify, amazon, tiktok, us_3pl, mx_3pl
from db.client import RETAIL_SOURCE, refresh_unified_view, snapshot_coverage

CONNECTORS = [
    ("shopify_us", lambda d: shopify.run(d, store="us"),  shopify),
    ("shopify_mx", lambda d: shopify.run(d, store="mx"),  shopify),
    ("amazon_us",  lambda d: amazon.run(d, marketplace="us"), amazon),
    ("amazon_mx",  lambda d: amazon.run(d, marketplace="mx"), amazon),
    ("tiktok_us",  tiktok.run,   tiktok),
    ("mx_3pl",     mx_3pl.run,   mx_3pl),
    ("us_3pl",     us_3pl.run,   us_3pl),
]


def _quality_report(name: str, rows: list[dict]) -> None:
    total    = len(rows)
    mapped   = sum(1 for r in rows if r.get("internal_sku"))
    unmapped = total - mapped
    zero_qty = sum(1 for r in rows if (r.get("qty_available") or 0) == 0)

    status = "[OK]" if total > 0 else "[WARN]"
    print(f"\n{status} {name}  —  {total} rows  |  mapped {mapped}  |  unmapped {unmapped}  |  zero-qty {zero_qty}")
    for r in rows[:5]:
        sku   = (r.get("external_sku") or r.get("external_id") or "")[:30]
        isku  = r.get("internal_sku") or "—"
        qty   = r.get("qty_available")
        print(f"      {sku:<32} internal={isku:<20} qty={qty}")
    if total > 5:
        print(f"      ... and {total - 5} more")


def _coverage_report(snapshot_date, expected: list[str]) -> list[str]:
    """Print rows written and mapped per source; return the sources with a hole.

    Two distinct failures both leave the day unusable, so both are reported:

      NO DATA     the source wrote nothing. A connector can raise (caught
                  above) or quietly return zero rows.
      NONE MAPPED the source wrote rows but none resolved to an internal_sku.
                  Every dashboard table is keyed on internal_sku, so these rows
                  are invisible there and in the Excel export even though the
                  row count looks healthy. Mapped ratios differ widely by
                  source (14% to 80%), so only a total collapse to zero is
                  flagged — a percentage threshold would misfire.

    Reporting at the end of the run is what makes a hole visible while it can
    still be refilled: every connector reads *current* inventory, so once the
    date rolls over the day is lost for good.
    """
    print(f"\n{'─' * 60}")
    print(f"Coverage for {snapshot_date}:")
    try:
        coverage = snapshot_coverage(snapshot_date, expected + [RETAIL_SOURCE])
    except Exception as exc:
        print(f"  coverage check failed: {exc}")
        return []

    gaps = []
    for source in expected + [RETAIL_SOURCE]:
        written, mapped = coverage[source]
        if written == 0:
            gaps.append(source)
            note = "  <-- NO DATA"
        elif mapped == 0:
            gaps.append(source)
            note = "  <-- NONE MAPPED (invisible in dashboard and export)"
        else:
            note = ""
        print(f"  {source:<17} {written:>5} rows  {mapped:>5} mapped{note}")
    return gaps


def main():
    dry_run = "--dry-run" in sys.argv
    snapshot_date = datetime.now(ZoneInfo("America/Mexico_City")).date()
    errors = []

    if dry_run:
        # Patch both DB functions on every connector module so no network call reaches Supabase.
        import db.client as _db
        _db_orig_upsert = _db.upsert_snapshots
        _db_orig_resolve = _db.resolve_internal_skus
        _db.resolve_internal_skus = lambda source, ids: {}  # return empty map; internal_sku will be None
        print(f"DRY RUN — fetching data for {snapshot_date}, nothing will be written.\n")

    for name, fn, module in CONNECTORS:
        if dry_run:
            # Patch upsert_snapshots on the connector module so the call is intercepted.
            # Connectors use `from db.client import upsert_snapshots`, so we must patch
            # the name on the module object itself, not on db.client.
            captured: list[dict] = []

            def _capture(rows, _store=captured):
                _store.extend(rows)
                return len(rows)

            original_upsert = module.upsert_snapshots
            original_resolve = getattr(module, "resolve_internal_skus", None)
            module.upsert_snapshots = _capture
            if original_resolve is not None:
                module.resolve_internal_skus = lambda source, ids: {}
            try:
                fn(snapshot_date)
                _quality_report(name, captured)
            except Exception as exc:
                print(f"\n[ERROR] {name}: {exc}", file=sys.stderr)
                errors.append(name)
            finally:
                module.upsert_snapshots = original_upsert
                if original_resolve is not None:
                    module.resolve_internal_skus = original_resolve
        else:
            try:
                count = fn(snapshot_date)
                print(f"[OK] {name}: {count} rows")
            except Exception as exc:
                print(f"[ERROR] {name}: {exc}", file=sys.stderr)
                errors.append(name)

    gaps: list[str] = []

    if dry_run:
        print(f"\n{'─' * 60}")
        print("Dry run complete — database unchanged.")
        print("Run without --dry-run to commit to the database.")
    else:
        print("Refreshing unified view...")
        refresh_unified_view()

        gaps[:] = _coverage_report(snapshot_date, [name for name, _, _ in CONNECTORS])
        if gaps:
            print(
                f"\nINCOMPLETE DATA for {snapshot_date}: {', '.join(gaps)}\n"
                "These sources wrote no rows, or none that map to a SKU.\n"
                "Connectors read CURRENT inventory, so\n"
                f"this gap can only be refilled while it is still {snapshot_date} —\n"
                "after that the day is permanently incomplete. To refill one source:\n"
                "    python -c \"from datetime import date; from connectors import amazon; \\\n"
                "        amazon.run(date.today(), marketplace='us')\"\n"
                "then re-run the view refresh.",
                file=sys.stderr,
            )

    if errors:
        print(f"\nFailed sources: {', '.join(errors)}", file=sys.stderr)
    if errors or gaps:
        # Gaps without errors mean a connector returned no rows but did not
        # raise; that is still an incomplete day, so fail the run either way.
        sys.exit(1)


if __name__ == "__main__":
    main()
