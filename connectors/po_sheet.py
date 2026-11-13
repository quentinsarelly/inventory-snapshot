"""
Purchase order connector: the supply team's PO tracking Google Sheet.

Copies the "TRANSITO" (open POs) and "TRANSITO RECIBIDO " (received POs) tabs
into public.po_lines_raw, one full copy per day.

Unlike the inventory connectors, this source keeps its own history, so a
missed day is not lost data: the next run copies the sheet as it is then.

Design choices:
  * Cells are read as FORMATTED_VALUE, the text people see. Parsing dates and
    numbers happens in SQL (agent.po_lines), where bad values get flagged
    instead of guessed. Reading raw values would turn dates into serial
    numbers or UTC timestamps, the source of SYNC_TRANSITO's one-day shift.
  * Columns are matched by header name, not position, so the supply team can
    insert or reorder columns without breaking the load. A missing required
    header fails the run loudly instead of loading shifted data.
  * The payment status column has no header today. It is taken from the blank
    header right after STATUS, or from a header named "Payment status" once
    the team adds one.

Authentication uses OAuth *user* credentials, not a service account: the
organization does not allow service accounts. GOOGLE_TOKEN_JSON holds the
contents of a token.json produced by scripts/get_google_token.py, including the
refresh_token, client_id and client_secret. Each run exchanges the refresh
token for a fresh access token; nothing is written back, so the refreshed
access token is simply discarded at the end of the run.

Required env vars:
    GOOGLE_TOKEN_JSON   contents of token.json for a Google account with at
                        least read access to the sheet. Must contain
                        refresh_token, client_id and client_secret, and must
                        have been granted the scope in SCOPES below.
    PO_SHEET_ID         spreadsheet ID from the sheet's URL
"""
import json
import os
import unicodedata
from datetime import date
from urllib.parse import quote

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

from db.client import replace_po_lines

load_dotenv()

TABS = ["TRANSITO", "TRANSITO RECIBIDO "]   # note the trailing space in the tab name
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# normalized header -> column in po_lines_raw
HEADERS = {
    "po#":                       "po_number",
    "po date":                   "po_date",
    "sku id":                    "sku",
    "descripcion del producto":  "description",
    "proveedor":                 "supplier",
    "po qty":                    "po_qty",
    "qty confirmada":            "qty_confirmed",
    "status":                    "status",
    "payment status":            "payment_status",
    "estatus de pago":           "payment_status",
    "prepayment date":           "prepayment_date",
    "pickup ready date":         "pickup_ready_date",
    "eta":                       "eta",
    "final payment date":        "final_payment_date",
    "final wh":                  "final_wh",
    "comment":                   "comment",
}
REQUIRED = {"po_number", "sku", "supplier", "po_qty", "status", "eta"}


class SheetStructureError(Exception):
    pass


def _norm(header: str) -> str:
    """Lowercase, trim, strip accents: 'Descripción del Producto ' -> 'descripcion del producto'."""
    text = unicodedata.normalize("NFKD", header or "").encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def _column_map(header_row: list[str], tab: str) -> dict[int, str]:
    """Return {column index: po_lines_raw column} for one tab's header row."""
    mapping: dict[int, str] = {}
    for i, raw in enumerate(header_row):
        name = _norm(raw)
        if name in HEADERS:
            mapping[i] = HEADERS[name]
        elif name == "" and i > 0 and _norm(header_row[i - 1]) == "status":
            mapping[i] = "payment_status"

    found = set(mapping.values())
    missing = REQUIRED - found
    if missing:
        raise SheetStructureError(
            f"tab {tab!r}: required columns not found: {sorted(missing)}. "
            f"Headers seen: {header_row}"
        )
    return mapping


def _fetch_tab(session: AuthorizedSession, sheet_id: str, tab: str) -> list[list[str]]:
    rng = quote(f"'{tab}'!A:Z", safe="")
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}"
        "?valueRenderOption=FORMATTED_VALUE&majorDimension=ROWS"
    )
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json().get("values", [])


def _session() -> AuthorizedSession:
    """Authorized session from the OAuth user token in GOOGLE_TOKEN_JSON."""
    raw = os.environ["GOOGLE_TOKEN_JSON"]
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_TOKEN_JSON is not valid JSON. It must hold the full contents "
            "of token.json (see scripts/get_google_token.py)."
        ) from exc

    missing = [k for k in ("refresh_token", "client_id", "client_secret") if not info.get(k)]
    if missing:
        raise RuntimeError(
            f"GOOGLE_TOKEN_JSON is missing {missing}. Regenerate it with "
            "scripts/get_google_token.py — a token.json saved without these "
            "cannot be refreshed unattended."
        )

    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        # Stored access tokens are short-lived and GitHub Actions keeps nothing
        # between runs, so this refresh is the normal path, not an edge case.
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise RuntimeError(
                f"Refreshing the Google token failed: {exc}. Common causes: the "
                "OAuth consent screen is in Testing mode, where refresh tokens "
                "expire after 7 days (use an Internal app or publish it); the "
                "user revoked access; or the token was granted different scopes "
                f"than {SCOPES}."
            ) from exc
    return AuthorizedSession(creds)


def rows_from_values(values: list[list[str]], tab: str, snapshot_date: date) -> list[dict]:
    """Turn one tab's cell values into po_lines_raw rows. Pure function, easy to test."""
    if not values:
        raise SheetStructureError(f"tab {tab!r} is empty")

    mapping = _column_map(values[0], tab)
    rows = []
    for offset, cells in enumerate(values[1:], start=2):   # sheet row numbers, header = 1
        record = {col: None for col in HEADERS.values()}
        for i, col in mapping.items():
            value = cells[i].strip() if i < len(cells) and cells[i] is not None else ""
            record[col] = value or None
        # skip fully blank rows
        if not any(record.values()):
            continue
        record.update({"snapshot_date": str(snapshot_date), "tab": tab, "row_number": offset})
        rows.append(record)
    return rows


def run(snapshot_date: date) -> int:
    sheet_id = os.environ["PO_SHEET_ID"]
    session = _session()

    total = 0
    for tab in TABS:
        values = _fetch_tab(session, sheet_id, tab)
        rows = rows_from_values(values, tab, snapshot_date)
        total += replace_po_lines(snapshot_date, tab, rows)
    return total
