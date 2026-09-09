from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber


COUNTRY_NAMES = {
    "Austria": "AT", "Belgium": "BE", "Bulgaria": "BG", "Croatia": "HR",
    "Cyprus": "CY", "Czechia": "CZ", "Denmark": "DK", "Estonia": "EE",
    "Finland": "FI", "France": "FR", "Germany": "DE", "Greece": "EL",
    "Hungary": "HU", "Ireland": "IE", "Italy": "IT", "Latvia": "LV",
    "Lithuania": "LT", "Luxembourg": "LU", "Netherlands": "NL", "Poland": "PL",
    "Portugal": "PT", "Romania": "RO", "Slovakia": "SK", "Slovenia": "SI",
    "Spain": "ES", "Sweden": "SE", "China": "CN", "India": "IN", "Malta": "MT",
}


def _money(value: str) -> str:
    value = value.strip().replace(" ", "").replace("\u00a0", "")
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return format(Decimal(value), "f")
    except InvalidOperation:
        return ""


def _iso_date(value: str) -> str:
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _first(pattern: str, text: str, flags: int = 0) -> str:
    found = re.search(pattern, text, flags)
    return found.group(1).strip() if found else ""


def _base_draft(filename: str, text: str, pages: int) -> dict:
    invoice_number = _first(r"(?im)\binvoice(?:\s+(?:no\.?|number))?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", text)
    dates = re.findall(r"\b(?:[0-3]?\d[-/.][01]?\d[-/.](?:20)?\d{2}|20\d{2}-[01]\d-[0-3]\d)\b", text)
    total = _first(r"(?im)(?:total(?:\s+net\s+value[^\n]*|\s+amount)?)[^\d\n]*([\d., ]+)\s*(?:EUR)?\s*$", text)
    incoterm = _first(r"(?im)\bincoterm\s*:?\s*([A-Z]{3})\b", text)
    currency = "EUR" if re.search(r"\bEUR\b|€", text) else ""
    return {
        "supplier_name": "",
        "supplier_vat_country": "",
        "supplier_vat_number": "",
        "invoice_number": invoice_number,
        "invoice_date": _iso_date(dates[0]) if dates else "",
        "arrival_date": "",
        "currency": currency or "EUR",
        "total_value": _money(total) if total else "",
        "consignment_country": "",
        "mode_transport": "4" if re.search(r"(?i)\b(?:DHL|TNT|UPS|FedEx|courier)\b", text) else "",
        "terms_delivery": incoterm,
        "nature_transaction": "11",
        "flow": "A",
        "notes": f"Extracted from {filename}; verify every suggested value.",
        "page_count": pages,
        "adapter": "general",
        "lines": [],
    }


def _parse_stricker(draft: dict, page_lines: list[list[str]], text: str) -> dict:
    draft.update({
        "supplier_name": "Paul Stricker, SA",
        "supplier_vat_country": "PT",
        "supplier_vat_number": _first(r"NIF-\s*PT([A-Z0-9]+)", text),
        "invoice_number": _first(r"Fatura\s+FCI\s+(FCI-[A-Z0-9/\-]+)", text),
        "invoice_date": _iso_date(_first(r"(?m)^(\d{2}-\d{2}-\d{4})\s+\d{2}-\d{2}-\d{4}", text)),
        "arrival_date": "",
        "total_value": _money(_first(r"TOTAL\s+([\d., ]+)\s+EUR", text)),
        "terms_delivery": _first(r"Incoterm:\s*([A-Z]{3})", text),
        "mode_transport": "4" if "TNTECON" in text else "",
        "consignment_country": "PT",
        "adapter": "stricker",
    })
    grouped: OrderedDict[tuple, dict] = OrderedDict()
    line_pattern = re.compile(
        r"^(\S+)\s+(P[DS]-\d+|CS-\d+)\s+(.+?)\s+(\d+(?:[.,]\d+)?)UN\s+([\d.,]+)(?:\s+([\d.,]+))?(?:\s+[\d.,]+)?$"
    )
    for page_number, lines in enumerate(page_lines, 1):
        for index, raw in enumerate(lines):
            match = line_pattern.match(raw)
            if not match:
                continue
            product_code, sku, description, quantity, unit_price, value = match.groups()
            tariff = origin = ""
            if index + 1 < len(lines):
                detail = re.search(r"TARIC Code:\s*(\d{8,10})\s+Product Origin:\s*([^\n]+)", lines[index + 1])
                if detail:
                    tariff = detail.group(1)
                    origin = COUNTRY_NAMES.get(detail.group(2).strip(), "")
            kind = "charge" if sku.startswith("CS-") else "goods"
            key = (sku, description, tariff, origin, kind)
            amount = _money(value or "0")
            qty = _money(quantity)
            if key not in grouped:
                grouped[key] = {
                    "sku": sku, "description": description, "quantity": qty, "unit": "PCE",
                    "raw_commodity_code": tariff, "hs_code": tariff[:8], "origin_country": origin,
                    "invoice_value": amount, "statistical_value": amount if kind == "goods" else "",
                    "net_mass": "", "supp_qty": "", "supp_unit": "", "line_kind": kind,
                    "reviewed": False, "source_page": page_number, "confidence": "layout-adapter",
                    "notes": f"Supplier product code {product_code}",
                }
            else:
                grouped[key]["quantity"] = format(Decimal(grouped[key]["quantity"]) + Decimal(qty), "f")
                grouped[key]["invoice_value"] = format(Decimal(grouped[key]["invoice_value"] or "0") + Decimal(amount or "0"), "f")
                if kind == "goods":
                    grouped[key]["statistical_value"] = grouped[key]["invoice_value"]
    draft["lines"] = list(grouped.values())
    # Link Stricker charge rows to the sole goods SKU on invoices where unambiguous.
    goods = [i for i, line in enumerate(draft["lines"]) if line["line_kind"] == "goods"]
    if len(goods) == 1:
        for line in draft["lines"]:
            if line["line_kind"] == "charge":
                line["linked_position"] = goods[0] + 1
    return draft


def _parse_midocean(draft: dict, page_lines: list[list[str]], text: str) -> dict:
    draft.update({
        "supplier_name": "Solo midocean / Mid Ocean Brands B.V.",
        "supplier_vat_country": _first(r"VAT no\. MOB\s+([A-Z]{2})", text),
        "supplier_vat_number": _first(r"VAT no\. MOB\s+[A-Z]{2}([A-Z0-9]+)", text),
        "invoice_number": _first(r"(?m)^Jake Borg Invoice\s+(\S+)", text),
        "invoice_date": _iso_date(_first(r"(?im)\bDate\s+(\d{2}\.\d{2}\.\d{4})", text)),
        "arrival_date": "",
        "total_value": _money(_first(r"Total net value excl\. VAT:\s*EUR\s*([\d.,]+)", text)),
        "terms_delivery": _first(r"Incoterm\s+([A-Z]{3})", text),
        "mode_transport": "4" if "TNT Express" in text else "",
        "consignment_country": "PL" if "VAT no. MOB PL" in text else "",
        "adapter": "midocean",
    })
    goods_pattern = re.compile(
        r"^(.+?)\s+((?:\d{4}\s+){2}\d{2})\s+(\d+(?:[.,]\d+)?)\s+(PC|PCS|EA|UN)\s+([\d.,]+)\s+\w+\s+EUR\s+([\d.,]+)$",
        re.I,
    )
    all_lines = [(p, line) for p, lines in enumerate(page_lines, 1) for line in lines]
    parsed: list[dict] = []
    last_goods_position: int | None = None
    for flat_index, (page_number, raw) in enumerate(all_lines):
        match = goods_pattern.match(raw)
        if match:
            description, raw_code, quantity, unit, unit_price, amount = match.groups()
            code = re.sub(r"\s", "", raw_code)
            sku = ""
            origin = ""
            if flat_index + 1 < len(all_lines):
                detail = all_lines[flat_index + 1][1]
                sku_match = re.match(r"(\S+)\s+/\S+\s+([A-Z]{2})$", detail)
                if sku_match:
                    sku, origin = sku_match.groups()
            parsed.append({
                "sku": sku, "description": description, "quantity": _money(quantity), "unit": "PCE",
                "raw_commodity_code": code, "hs_code": code[:8], "origin_country": origin,
                "invoice_value": _money(amount), "statistical_value": _money(amount), "net_mass": "",
                "supp_qty": "", "supp_unit": "", "line_kind": "goods", "reviewed": False,
                "source_page": page_number, "confidence": "layout-adapter",
                "notes": f"Invoice unit price {_money(unit_price)} EUR",
            })
            last_goods_position = len(parsed)
            continue
        charge = re.match(r"^(Setting-up costs|Printing costs|Handling charges)\s+([\d.,]+)$", raw, re.I)
        if charge:
            parsed.append({
                "sku": "", "description": charge.group(1), "quantity": "1", "unit": "",
                "raw_commodity_code": "", "hs_code": "", "origin_country": "",
                "invoice_value": _money(charge.group(2)), "statistical_value": "", "net_mass": "",
                "supp_qty": "", "supp_unit": "", "line_kind": "charge", "reviewed": False,
                "source_page": page_number, "confidence": "layout-adapter",
                "linked_position": last_goods_position, "notes": "Confirm that this charge belongs with the linked goods.",
            })
    draft["lines"] = parsed
    return draft


def extract_invoice(path: Path, display_name: str | None = None) -> tuple[dict, str, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    page_lines: list[list[str]] = []
    page_texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            page_texts.append(text)
            page_lines.append(text.splitlines())
    combined = "\n".join(page_texts)
    draft = _base_draft(display_name or path.name, combined, len(page_texts))
    if "Paul Stricker" in combined:
        draft = _parse_stricker(draft, page_lines, combined)
    elif "midocean" in combined.lower() or "Mid Ocean Brands" in combined:
        draft = _parse_midocean(draft, page_lines, combined)
    if not draft["lines"]:
        draft["notes"] += " No reliable goods table was detected; add lines manually."
    if any(not page.strip() for page in page_texts):
        draft["notes"] += " One or more pages have no native text and need OCR or manual review."
    return draft, combined, digest
