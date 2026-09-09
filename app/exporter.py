from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from lxml import etree

from .rules import rounded_whole


EXPORT_FIELDS = [
    "Period", "TRADE_VAT_NO", "Email", "AGENT_VAT_NO", "TRADING_LICENCE",
    "TRADING_DOCUMENT_ID", "AIRWAY_LADING_BILL_NO", "Signatory", "ID_card_no",
    "Tel_No", "Decleration_Date", "Locality", "FLOW", "VAT_CTRY_ID",
    "Suppliers_VAT_No", "HS_Code", "COO", "COC", "MOT", "TOD", "INV_CURR",
    "NOT", "INVOICE_VALUE", "STAT_VALUE", "NET_MASS", "SUPP_QTY", "SUPP_UNIT",
    "QUANTITY", "TOC", "RANGE",
]


ALIASES = {
    "period": "Period", "tradevat": "TRADE_VAT_NO", "tradevatno": "TRADE_VAT_NO",
    "email": "Email", "agentvat": "AGENT_VAT_NO", "agentvatno": "AGENT_VAT_NO",
    "tradinglicence": "TRADING_LICENCE", "tradingdocumentid": "TRADING_DOCUMENT_ID",
    "airwayladingbillno": "AIRWAY_LADING_BILL_NO", "airwaybillingbillno": "AIRWAY_LADING_BILL_NO",
    "signatory": "Signatory", "idcard": "ID_card_no", "idcardno": "ID_card_no",
    "telno": "Tel_No", "declarationdate": "Decleration_Date", "declerationdate": "Decleration_Date",
    "locality": "Locality", "flow": "FLOW", "vatctry": "VAT_CTRY_ID", "vatctryid": "VAT_CTRY_ID",
    "suppliersvatno": "Suppliers_VAT_No", "hscode": "HS_Code", "coo": "COO", "coc": "COC",
    "mot": "MOT", "tod": "TOD", "invcurr": "INV_CURR", "not": "NOT",
    "invoicevalue": "INVOICE_VALUE", "statvalue": "STAT_VALUE", "netmass": "NET_MASS",
    "suppqty": "SUPP_QTY", "suppunit": "SUPP_UNIT", "quantity": "QUANTITY",
    "toc": "TOC", "range": "RANGE",
}


def normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def declaration_rows(invoices: list[dict], lines_by_invoice: dict[int, list[dict]], profile: dict, period: str) -> list[dict]:
    mmYYYY = period[5:7] + period[:4]
    result = []
    for invoice in invoices:
        invoice_lines = lines_by_invoice[invoice["id"]]
        charges_by_target: dict[int, dict[str, Decimal]] = {}
        for charge in invoice_lines:
            if not charge["line_kind"].startswith("charge") or not charge.get("linked_line_id"):
                continue
            bucket = charges_by_target.setdefault(
                charge["linked_line_id"],
                {"invoice": Decimal("0"), "stat": Decimal("0")},
            )
            amount = Decimal(str(charge["invoice_value"] or "0"))
            if charge["line_kind"] == "charge_invoice":
                bucket["invoice"] += amount
                bucket["stat"] += amount
            elif charge["line_kind"] == "charge_stat":
                bucket["stat"] += amount
        for line in invoice_lines:
            if line["line_kind"] != "goods":
                continue
            allocated = charges_by_target.get(
                line["id"],
                {"invoice": Decimal("0"), "stat": Decimal("0")},
            )
            result.append({
                "Period": mmYYYY,
                "TRADE_VAT_NO": re.sub(r"^MT", "", profile["trader_vat"].upper()),
                "Email": profile["email"], "AGENT_VAT_NO": profile["agent_vat"],
                "TRADING_LICENCE": profile["trading_licence"], "TRADING_DOCUMENT_ID": "",
                "AIRWAY_LADING_BILL_NO": "", "Signatory": profile["signatory"],
                "ID_card_no": profile["id_card"], "Tel_No": profile["telephone"],
                "Decleration_Date": date.today().isoformat(), "Locality": profile["locality"].upper(),
                "FLOW": invoice["flow"], "VAT_CTRY_ID": invoice["supplier_vat_country"].upper(),
                "Suppliers_VAT_No": invoice["supplier_vat_number"], "HS_Code": line["hs_code"],
                "COO": line["origin_country"].upper(), "COC": invoice["consignment_country"].upper(),
                "MOT": invoice["mode_transport"], "TOD": invoice["terms_delivery"].upper(),
                "INV_CURR": invoice["currency"].upper(), "NOT": invoice["nature_transaction"],
                "INVOICE_VALUE": str(rounded_whole(Decimal(str(line["invoice_value"] or "0")) + allocated["invoice"])),
                "STAT_VALUE": str(rounded_whole(Decimal(str(line["statistical_value"] or "0")) + allocated["stat"])),
                "NET_MASS": str(max(1, rounded_whole(line["net_mass"]))),
                "SUPP_QTY": str(rounded_whole(line["supp_qty"])) if line["supp_qty"] else "",
                "SUPP_UNIT": line["supp_unit"],
                "QUANTITY": str(rounded_whole(line["special_quantity"])) if line.get("special_quantity") else "",
                "TOC": line.get("collector_type", ""), "RANGE": line.get("range_value", ""),
                "_invoice_id": invoice["id"], "_invoice_reference": invoice["invoice_number"],
                "_supplier": invoice["supplier_name"], "_description": line["description"],
            })
    return result


def csv_bytes(rows: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    fields = EXPORT_FIELDS + ["_invoice_reference", "_supplier", "_description"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def inspect_schema(path: Path) -> dict:
    if not path.exists():
        return {"configured": False, "valid": False, "message": "Upload the official NSO XSD in Organisation settings."}
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        tree = etree.parse(str(path), parser)
        etree.XMLSchema(tree)
    except Exception as exc:
        return {"configured": True, "valid": False, "message": f"The saved schema is invalid: {exc}"}
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
    names = [name for name in tree.xpath("//xs:element/@name", namespaces=ns) if name]
    mapped = {ALIASES.get(normalise(name)): name for name in names if ALIASES.get(normalise(name))}
    missing = [field for field in EXPORT_FIELDS[:25] if field not in mapped]
    valid = len(mapped) >= 20 and not missing
    return {
        "configured": True, "valid": valid, "field_count": len(mapped), "mapped_fields": mapped,
        "missing_fields": missing,
        "message": "Official schema is ready for verified export." if valid else "Schema loaded, but required fields could not all be mapped.",
    }


def _schema_shape(path: Path) -> tuple[etree._ElementTree, str, str, dict[str, str]]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    tree = etree.parse(str(path), parser)
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}
    root_nodes = tree.xpath("/xs:schema/xs:element", namespaces=ns)
    if len(root_nodes) != 1:
        raise ValueError("Expected one global root element in the official schema")
    root_name = root_nodes[0].get("name")
    candidates = root_nodes[0].xpath(".//xs:element[@maxOccurs='unbounded']", namespaces=ns)
    if not candidates:
        raise ValueError("Could not find the repeating declaration element")
    best = max(candidates, key=lambda node: len(node.xpath(".//xs:element/@name", namespaces=ns)))
    decl_name = best.get("name")
    leaf_names = best.xpath(".//xs:element/@name", namespaces=ns)
    mapped = {ALIASES.get(normalise(name)): name for name in leaf_names if ALIASES.get(normalise(name))}
    return tree, root_name, decl_name, mapped


def xml_bytes(rows: list[dict], schema_path: Path) -> bytes:
    status = inspect_schema(schema_path)
    if not status.get("valid"):
        raise ValueError(status["message"])
    schema_tree, root_name, decl_name, mapped = _schema_shape(schema_path)
    target_ns = schema_tree.getroot().get("targetNamespace")
    root = etree.Element(etree.QName(target_ns, root_name) if target_ns else root_name,
                         nsmap={None: target_ns} if target_ns else None)
    for values in rows:
        decl = etree.SubElement(root, etree.QName(target_ns, decl_name) if target_ns else decl_name)
        for field in EXPORT_FIELDS:
            actual = mapped.get(field)
            if not actual:
                continue
            value = values.get(field, "")
            if value == "":
                continue
            child = etree.SubElement(decl, etree.QName(target_ns, actual) if target_ns else actual)
            child.text = str(value)
    result = etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    schema = etree.XMLSchema(schema_tree)
    document = etree.fromstring(result, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    if not schema.validate(document):
        raise ValueError("Generated XML failed the official XSD: " + str(schema.error_log.last_error))
    return result


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
