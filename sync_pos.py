#!/usr/bin/env python3
"""Copy the supply team's PO tracking sheet into po_lines_raw.

Runs separately from run_all.py on purpose: a PO sheet problem must not mark
the inventory day as incomplete, and an inventory outage must not stop the PO
copy. The two are independent sources.

Usage:
    python sync_pos.py            # fetch + write to database
    python sync_pos.py --dry-run  # fetch only, print what would be written
"""
import sys
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from connectors import po_sheet


def main():
    snapshot_date = datetime.now(ZoneInfo("America/Mexico_City")).date()

    if "--dry-run" in sys.argv:
        session = po_sheet._session()
        import os
        for tab in po_sheet.TABS:
            values = po_sheet._fetch_tab(session, os.environ["PO_SHEET_ID"], tab)
            rows = po_sheet.rows_from_values(values, tab, snapshot_date)
            statuses = Counter(r["status"] for r in rows)
            print(f"[DRY RUN] {tab!r}: {len(rows)} rows  {dict(statuses)}")
            for r in rows[:3]:
                print(f"    row {r['row_number']}: {r['po_number']} {r['sku']} qty={r['po_qty']} eta={r['eta']}")
        print("Dry run complete, database unchanged.")
        return

    try:
        count = po_sheet.run(snapshot_date)
        print(f"[OK] po_sheet: {count} rows for {snapshot_date}")
    except Exception as exc:
        print(f"[ERROR] po_sheet: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
