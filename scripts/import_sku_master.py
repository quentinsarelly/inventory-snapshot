#!/usr/bin/env python3
"""
Convert a SKU master CSV into SQL INSERT statements for sku_master.

Usage:
    python scripts/import_sku_master.py path/to/your_skus.csv

Output is printed to stdout — review it, then paste into Supabase SQL Editor.
"""
import csv
import sys
from pathlib import Path

REQUIRED_COLUMNS = {"internal_sku", "display_name"}
OPTIONAL_COLUMNS = {"category", "is_bundle"}

def parse_bool(value: str) -> str:
    return "TRUE" if value.strip().lower() in ("true", "1", "yes") else "FALSE"

def escape(value: str) -> str:
    return value.replace("'", "''")

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/import_sku_master.py <csv_file>", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    rows = []
    encoding = "utf-8-sig"
    try:
        with open(csv_path, newline="", encoding=encoding) as f:
            f.read()
    except UnicodeDecodeError:
        encoding = "iso-8859-1"

    with open(csv_path, newline="", encoding=encoding) as f:
        reader = csv.DictReader(f)
        headers = set(reader.fieldnames or [])

        missing = REQUIRED_COLUMNS - headers
        if missing:
            print(f"Missing required columns: {missing}", file=sys.stderr)
            sys.exit(1)

        for i, row in enumerate(reader, start=2):
            internal_sku = row["internal_sku"].strip()
            display_name = row["display_name"].strip()

            if not internal_sku or not display_name:
                print(f"Skipping row {i}: internal_sku and display_name are required", file=sys.stderr)
                continue

            category = escape(row.get("category", "").strip()) or "Uncategorized"
            is_bundle = parse_bool(row.get("is_bundle", "false"))

            rows.append(
                f"  ('{escape(internal_sku)}', '{escape(display_name)}', '{category}', {is_bundle})"
            )

    if not rows:
        print("No valid rows found.", file=sys.stderr)
        sys.exit(1)

    sql = (
        "INSERT INTO sku_master (internal_sku, display_name, category, is_bundle)\nVALUES\n"
        + ",\n".join(rows)
        + "\nON CONFLICT (internal_sku) DO UPDATE SET\n"
        + "  display_name = EXCLUDED.display_name,\n"
        + "  category     = EXCLUDED.category,\n"
        + "  is_bundle    = EXCLUDED.is_bundle;"
    )

    print(sql)
    print(f"\n-- {len(rows)} SKUs", file=sys.stderr)

if __name__ == "__main__":
    main()
