"""
US 3PL (Camelot / Excalibur) inventory connector.

Uses SOAP/Excalibur web service (Dynamics NAV).
Interface object: XMLPort 37005331 PW Item Inventory Export.

Required env vars:
    CAMELOT_SOAP_URL
    CAMELOT_USERNAME
    CAMELOT_PASSWORD
    CAMELOT_INTERFACE_PROFILE
    CAMELOT_CLIENT
    CAMELOT_TRADING_PARTNER
"""
import json
import os
from datetime import date
from xml.etree import ElementTree as ET

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

from db.client import resolve_internal_skus, upsert_snapshots

load_dotenv()

SOURCE = "us_3pl"

_NS = "urn:microsoft-dynamics-schemas/codeunit/TPLWebServiceInt"
_NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"

_ENVELOPE = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <{action} xmlns="{ns}">
      {params}
    </{action}>
  </soap:Body>
</soap:Envelope>"""


class CamelotError(Exception):
    pass


def _call(action: str, params: dict) -> ET.Element:
    param_xml = "\n      ".join(f"<{k}>{v}</{k}>" for k, v in params.items())
    envelope = _ENVELOPE.format(action=action, ns=_NS, params=param_xml)
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"{_NS}"',
    }
    resp = requests.get(
        os.environ["CAMELOT_SOAP_URL"],
        data=envelope.encode("utf-8"),
        headers=headers,
        auth=HTTPBasicAuth(os.environ["CAMELOT_USERNAME"], os.environ["CAMELOT_PASSWORD"]),
        timeout=60,
    )
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        resp.raise_for_status()
        raise
    body = root.find(f"{{{_NS_SOAP}}}Body")
    fault = body.find(f"{{{_NS_SOAP}}}Fault")
    if fault is not None:
        msg = fault.findtext("faultstring") or ET.tostring(fault, encoding="unicode")
        raise CamelotError(msg)
    if not resp.ok:
        resp.raise_for_status()
    return list(body)[0]


def _text(el: ET.Element, tag: str) -> str:
    return (
        el.findtext(f".//{{{_NS}}}{tag}")
        or el.findtext(f".//{tag}")
        or ""
    )


def _int(el: ET.Element, *tags: str) -> int:
    for tag in tags:
        val = el.findtext(tag)
        if val is not None:
            try:
                return int(float(val))
            except ValueError:
                pass
    return 0


def _get_inventory() -> ET.Element | None:
    params = {
        "pInterfaceProfile": os.environ.get("CAMELOT_INTERFACE_PROFILE", ""),
        "pClient":           os.environ.get("CAMELOT_CLIENT", ""),
        "pTradingPartner":   os.environ.get("CAMELOT_TRADING_PARTNER", ""),
        "pXMLDoc":           "",
        "pClientFilter":     os.environ.get("CAMELOT_CLIENT", ""),
        "pItem":             "",
    }
    resp = _call("GetAvailableInventory", params)
    xml_str = _text(resp, "pXMLDoc")
    if not xml_str:
        return None
    try:
        return ET.fromstring(xml_str)
    except ET.ParseError:
        return None


_INV_NS = "urn:microsoft-dynamics-nav/xmlports/x50009"


def run(snapshot_date: date) -> int:
    doc = _get_inventory()
    if doc is None:
        return 0

    # Response: <Transaction xmlns="urn:microsoft-dynamics-nav/xmlports/x50009">
    #   <Inventory><ItemNumber>...</ItemNumber><QtyOnHand>...</QtyOnHand>...</Inventory>
    ns = f"{{{_INV_NS}}}"
    items = doc.findall(f"{ns}Inventory")

    external_skus = []
    for item in items:
        sku = (item.findtext(f"{ns}ItemNumber") or "").strip()
        if sku:
            external_skus.append(sku)

    sku_map = resolve_internal_skus(SOURCE, external_skus)

    rows = []
    for item in items:
        sku = (item.findtext(f"{ns}ItemNumber") or "").strip()
        if not sku:
            continue
        rows.append({
            "snapshot_date": snapshot_date,
            "source":        SOURCE,
            "internal_sku":  sku_map.get(sku),
            "external_id":   sku,
            "external_sku":  sku,
            "qty_on_hand":   _int(item, f"{ns}QtyOnHand"),
            "qty_reserved":  _int(item, f"{ns}QtyReserved"),
            "qty_available": _int(item, f"{ns}QtyAvailable"),
            "qty_inbound":   None,
            "raw_data":      json.dumps({c.tag.split("}")[-1]: c.text for c in item}),
        })

    return upsert_snapshots(rows)
