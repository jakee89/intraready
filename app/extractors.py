from __future__ import annotations

import hashlib
import base64
import io
import json
import re
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from .local_ai import extract_structured_invoice


COUNTRY_NAMES = {
    "Austria": "AT", "Belgium": "BE", "Bulgaria": "BG", "Croatia": "HR",
    "Cyprus": "CY", "Czechia": "CZ", "Denmark": "DK", "Estonia": "EE",
    "Finland": "FI", "France": "FR", "Germany": "DE", "Greece": "EL",
    "Hungary": "HU", "Ireland": "IE", "Italy": "IT", "Latvia": "LV",
    "Lithuania": "LT", "Luxembourg": "LU", "Netherlands": "NL", "Poland": "PL",
    "Portugal": "PT", "Romania": "RO", "Slovakia": "SK", "Slovenia": "SI",
    "Spain": "ES", "Sweden": "SE", "China": "CN", "India": "IN", "Malta": "MT",
}


def layout_fingerprint(path: Path) -> str:
    """Stable enough to detect a supplier layout while ignoring invoice values."""
    try:
        with pdfplumber.open(path) as pdf:
            parts = [f"{round(page.width)}x{round(page.height)}" for page in pdf.pages]
            text = " ".join((page.extract_text() or "")[:3000] for page in pdf.pages[:2]).upper()
        words = re.findall(r"[A-Z]{3,}", text)[:120]
        return hashlib.sha256(("|".join(parts + words)).encode()).hexdigest()[:20]
    except Exception:
        return ""


def _vision_pages(path: Path, maximum: int = 6) -> list[str]:
    images = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages[:maximum]:
                rendered = page.to_image(resolution=150).original.convert("RGB")
                buffer = io.BytesIO()
                rendered.save(buffer, format="JPEG", quality=82, optimize=True)
                images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    except Exception:
        return images
    return images


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
    invoice_number = _first(r"(?im)^invoice[ \t]+(?:number|no\.?)[ \t]*[:#]?[ \t]*([A-Z0-9][A-Z0-9\-/]{3,})", text)
    dates = re.findall(r"\b(?:[0-3]?\d[-/.][01]?\d[-/.](?:20)?\d{2}|20\d{2}-[01]\d-[0-3]\d)\b", text)
    total = _first(r"(?im)(?:total(?:\s+net\s+value[^\n]*|\s+amount)?)[^\d\n]*([\d., ]+)\s*(?:EUR)?\s*$", text)
    total_header = re.search(r"(?im)^.*\bTOTAL EURO\s*$\n([^\n]+)", text)
    if total_header:
        amounts = re.findall(r"\d+[.,]\d{2}", total_header.group(1))
        if amounts:
            total = amounts[-1]
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


def _apply_ai_draft(draft: dict, extracted: dict) -> dict:
    text_fields = ("supplier_name", "supplier_vat_country", "supplier_vat_number", "invoice_number",
                   "currency", "consignment_country", "terms_delivery")
    for field in text_fields:
        value = str(extracted.get(field, "")).strip()
        if value:
            draft[field] = value.upper() if field in {"supplier_vat_country", "currency", "consignment_country", "terms_delivery"} else value
    date_value = _iso_date(str(extracted.get("invoice_date", "")))
    if date_value:
        draft["invoice_date"] = date_value
    total = _money(str(extracted.get("total_value", "")))
    if total:
        draft["total_value"] = total
    lines = []
    for item in extracted.get("lines", []):
        if not isinstance(item, dict) or not str(item.get("description", "")).strip():
            continue
        ai_kind = item.get("line_kind")
        kind = {"freight": "charge_stat", "insurance": "charge_stat", "discount": "charge_invoice",
                "tax": "excluded", "service": "excluded", "charge": "charge"}.get(ai_kind, "goods")
        quantity = _money(str(item.get("quantity", "")))
        unit_mass = _money(str(item.get("unit_net_mass", "")))
        total_mass = _money(str(item.get("net_mass", "")))
        if not total_mass and unit_mass and quantity:
            total_mass = format(Decimal(unit_mass) * Decimal(quantity), "f")
        raw_code = re.sub(r"\D", "", str(item.get("commodity_code", "")))
        invoice_value = _money(str(item.get("invoice_value", "")))
        statistical_value = _money(str(item.get("statistical_value", "")))
        lines.append({
            "sku": str(item.get("sku", "")).strip(),
            "description": str(item.get("description", "")).strip(),
            "quantity": quantity, "unit": str(item.get("unit", "")).strip().upper(),
            "raw_commodity_code": raw_code, "hs_code": raw_code[:8],
            "origin_country": str(item.get("origin_country", "")).strip().upper(),
            "invoice_value": invoice_value,
            "statistical_value": statistical_value or (invoice_value if kind == "goods" else ""),
            "unit_net_mass": unit_mass, "net_mass": total_mass,
            "net_mass_overridden": 0, "supp_qty": "", "supp_unit": "",
            "line_kind": kind, "reviewed": False,
            "source_page": item.get("source_page") if isinstance(item.get("source_page"), int) else 1,
            "confidence": "local-ai", "notes": (f"Barcode/EAN: {item.get('barcode')}. " if item.get("barcode") else "") + "Verify against the source PDF.",
        })
    if lines:
        goods_positions = [index + 1 for index, line in enumerate(lines) if line["line_kind"] == "goods"]
        if len(goods_positions) == 1:
            for line in lines:
                if line["line_kind"] != "charge":
                    continue
                line["linked_position"] = goods_positions[0]
                if re.search(r"(?i)freight|transport|shipping|carriage|delivery cost", line["description"]):
                    line["line_kind"] = "charge_stat"
                elif re.search(r"(?i)print|engraving|logo|personalisation|decoration", line["description"]):
                    line["line_kind"] = "charge_invoice"
        draft["lines"] = lines
        draft["adapter"] = "local-ai"
        draft["notes"] += " Local AI created review suggestions; none are approved automatically."
    return draft


def _validate_and_normalise(draft: dict) -> dict:
    warnings = []
    for line in draft.get("lines", []):
        description = line.get("description", "")
        if re.search(r"(?i)\b(?:freight|transport|shipping|carriage|delivery cost)\b", description):
            line["line_kind"] = "charge_stat"
        elif re.search(r"(?i)\b(?:print|printing|engraving|logo|personalisation|decoration|setup|set-up|handling|packaging)\b", description) and line.get("line_kind") != "goods":
            line["line_kind"] = "charge_invoice"
        elif re.search(r"(?i)\b(?:vat|tax)\b", description) and line.get("line_kind") != "goods":
            line["line_kind"] = "excluded"
        quantity, unit_mass = line.get("quantity", ""), line.get("unit_net_mass", "")
        if quantity and unit_mass and not line.get("net_mass"):
            try:
                line["net_mass"] = format(Decimal(quantity) * Decimal(unit_mass), "f")
            except InvalidOperation:
                pass
        sku_digits = re.sub(r"\D", "", line.get("sku", ""))
        if len(sku_digits) in {12, 13, 14} and not re.search(r"[A-Z]", line.get("sku", ""), re.I):
            warnings.append(f"Row '{description[:35]}' may contain an EAN/barcode instead of a supplier SKU.")
        code = re.sub(r"\D", "", line.get("hs_code", ""))
        if code and len(code) != 8:
            warnings.append(f"Row '{description[:35]}' has a CN code that is not 8 digits.")
    try:
        declared = Decimal(draft.get("total_value") or "0")
        lines_total = sum((Decimal(line.get("invoice_value") or "0") for line in draft.get("lines", []) if line.get("line_kind") != "excluded"), Decimal("0"))
        if declared and lines_total and abs(declared - lines_total) > Decimal("0.05"):
            warnings.append(f"Extracted line values total {lines_total} but the invoice total is {declared}.")
    except InvalidOperation:
        warnings.append("One or more extracted amounts could not be validated.")
    if warnings:
        draft["notes"] += " Checks: " + " ".join(warnings)
    return draft


def _supplier_profile(text: str, profiles: list[dict] | None) -> dict | None:
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    normal_text = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    for profile in profiles or []:
        vat = re.sub(r"[^A-Z0-9]", "", profile.get("supplier_vat", "").upper())
        name = re.sub(r"[^A-Z0-9]+", " ", profile.get("supplier_name", "").upper()).strip()
        if (len(vat) >= 6 and vat in compact) or (len(name) >= 8 and name in normal_text):
            return profile
    return None


def _apply_supplier_defaults(draft: dict, profile: dict) -> None:
    vat = re.sub(r"[^A-Z0-9]", "", profile.get("supplier_vat", "").upper())
    country = vat[:2] if len(vat) >= 4 and vat[:2].isalpha() else ""
    draft["supplier_name"] = profile.get("supplier_name", "") or draft["supplier_name"]
    if country:
        draft["supplier_vat_country"] = country
        draft["supplier_vat_number"] = vat[2:]
    for field in ("flow", "currency", "consignment_country", "mode_transport", "terms_delivery", "nature_transaction"):
        if profile.get(field):
            draft[field] = profile[field]
    draft["notes"] += " Saved supplier defaults were applied."


def _parse_saved_mapping(path: Path, draft: dict, profile: dict) -> dict:
    try:
        regions = json.loads(profile.get("layout_mapping") or "[]")
    except (TypeError, json.JSONDecodeError):
        return draft
    if not regions:
        return draft
    columns: dict[str, list[dict]] = {}
    with pdfplumber.open(path) as pdf:
        for region in regions:
            page_number = int(region.get("page", 1))
            if page_number < 1 or page_number > len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            x, y, width, height = (float(region.get(key, 0)) for key in ("x", "y", "width", "height"))
            box = (x * page.width, y * page.height, (x + width) * page.width, (y + height) * page.height)
            crop = page.crop(box)
            field = str(region.get("field", ""))
            if field.startswith("line_"):
                grouped: list[dict] = []
                for word in crop.extract_words(x_tolerance=2, y_tolerance=3):
                    relative_y = float(word["top"]) / page.height
                    existing = next((item for item in grouped if abs(item["y"] - relative_y) < 0.006), None)
                    if existing:
                        existing["text"] += " " + word["text"]
                    else:
                        grouped.append({"y": relative_y, "text": word["text"]})
                columns[field[5:]] = grouped
            else:
                value = (crop.extract_text(x_tolerance=2, y_tolerance=2) or "").strip().replace("\n", " ")
                if not value:
                    continue
                if field in {"invoice_date", "arrival_date"}:
                    value = _iso_date(value)
                elif field == "total_value":
                    value = _money(value)
                elif field in {"supplier_vat_country", "currency", "consignment_country", "terms_delivery"}:
                    value = value.upper()
                if field in draft and value:
                    draft[field] = value
    anchors = columns.get("invoice_value") or columns.get("quantity") or columns.get("sku") or []
    parsed = []
    last_goods_position = None
    for anchor in anchors:
        values = {}
        for field, entries in columns.items():
            nearest = min(entries, key=lambda item: abs(item["y"] - anchor["y"]), default=None)
            values[field] = nearest["text"].strip() if nearest and abs(nearest["y"] - anchor["y"]) < 0.018 else ""
        description = values.get("description", "")
        amount = _money(values.get("invoice_value", ""))
        quantity = _money(values.get("quantity", ""))
        if not description or not amount:
            continue
        raw_code = re.sub(r"\D", "", values.get("hs_code", ""))
        unit_mass_text = values.get("unit_net_mass", "")
        unit_mass = _money(_first(r"([\d.,]+)", unit_mass_text))
        if unit_mass and re.search(r"(?i)\bg\b", unit_mass_text) and not re.search(r"(?i)\bkg\b", unit_mass_text):
            unit_mass = format(Decimal(unit_mass) / Decimal("1000"), "f")
        total_mass = format(Decimal(unit_mass) * Decimal(quantity), "f") if unit_mass and quantity else ""
        kind = "goods"
        if not raw_code and re.search(r"(?i)freight|transport|shipping|carriage|delivery cost", description):
            kind = "charge_stat"
        elif not raw_code and re.search(r"(?i)print|engraving|logo|personalisation|decoration|setup|set-up|handling", description):
            kind = "charge_invoice"
        parsed.append({
            "sku": values.get("sku", ""), "description": description, "quantity": quantity, "unit": values.get("unit", "").upper(),
            "raw_commodity_code": raw_code, "hs_code": raw_code[:8], "origin_country": values.get("origin_country", "").upper(),
            "invoice_value": amount, "statistical_value": amount, "unit_net_mass": unit_mass, "net_mass": total_mass,
            "net_mass_overridden": 0, "supp_qty": "", "supp_unit": "", "line_kind": kind, "reviewed": False,
            "source_page": 1, "confidence": "manual-map", "notes": "Extracted with the saved supplier map; verify this row.",
        })
        if kind == "goods":
            last_goods_position = len(parsed)
        elif last_goods_position:
            parsed[-1]["linked_position"] = last_goods_position
    if parsed:
        draft["lines"] = parsed
        draft["adapter"] = "manual-map"
        draft["notes"] += " Saved visual supplier mapping was used; AI was skipped."
    return draft


def preview_saved_mapping(path: Path, regions: list[dict]) -> dict:
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        draft = _base_draft(path.name, text, len(pdf.pages))
    return _parse_saved_mapping(path, draft, {"layout_mapping": json.dumps(regions)})


def _parse_common_tables(draft: dict, tables: list[list[list[str | None]]], text: str) -> dict:
    """Fast path for common Code/Description/Quantity/Price/Amount invoice tables."""
    for table in tables:
        if len(table) < 2:
            continue
        headers = [re.sub(r"\s+", " ", str(cell or "")).strip().lower() for cell in table[0]]
        if not {"description", "quantity", "amount"}.issubset(headers):
            continue
        indexes = {name: headers.index(name) for name in ("description", "quantity", "amount")}
        code_index = headers.index("code") if "code" in headers else None
        parsed = []
        for raw_row in table[1:]:
            description_lines = [x.strip() for x in str(raw_row[indexes["description"]] or "").splitlines() if x.strip()]
            amounts = [_money(x) for x in str(raw_row[indexes["amount"]] or "").splitlines() if _money(x)]
            quantities = [_money(x) for x in str(raw_row[indexes["quantity"]] or "").splitlines() if _money(x)]
            codes = [x.strip() for x in str(raw_row[code_index] or "").splitlines() if x.strip()] if code_index is not None else []
            if not amounts:
                continue
            metadata = re.compile(r"(?i)^(delivery note|order:|unit weight|customer order)")
            candidates = [x for x in description_lines if not metadata.search(x)]
            unit_line = next((x for x in description_lines if re.search(r"(?i)unit weight", x)), "")
            unit_match = re.search(r"(?i)unit weight\s*:?[ ]*([\d.,]+)\s*(kg|g)\b", unit_line)
            tariff = _first(r"(?i)statistical code\s*:?[ ]*(\d{8,10})", unit_line)
            # A colour/variant line often follows the metadata but has no own amount.
            if len(candidates) > len(amounts) and unit_line in description_lines:
                after = description_lines.index(unit_line) + 1
                if after < len(description_lines) and description_lines[after] in candidates:
                    candidates.remove(description_lines[after])
            if len(candidates) < len(amounts):
                continue
            candidates = candidates[:len(amounts)]
            unit_mass = ""
            if unit_match:
                unit_mass = _money(unit_match.group(1))
                if unit_match.group(2).lower() == "g" and unit_mass:
                    unit_mass = format(Decimal(unit_mass) / Decimal("1000"), "f")
            for index, (description, amount) in enumerate(zip(candidates, amounts)):
                quantity = quantities[min(index, len(quantities) - 1)] if quantities else "1"
                kind = "goods" if index == 0 and tariff else "charge_invoice"
                sku = codes[index] if index < len(codes) and len(re.sub(r"\D", "", codes[index])) < 8 else ""
                total_mass = format(Decimal(unit_mass) * Decimal(quantity), "f") if kind == "goods" and unit_mass and quantity else ""
                parsed.append({
                    "sku": sku, "description": description, "quantity": quantity, "unit": "PCE" if kind == "goods" else "",
                    "raw_commodity_code": tariff if kind == "goods" else "", "hs_code": tariff[:8] if kind == "goods" else "",
                    "origin_country": "", "invoice_value": amount, "statistical_value": amount if kind == "goods" else "",
                    "unit_net_mass": unit_mass if kind == "goods" else "", "net_mass": total_mass,
                    "net_mass_overridden": 0, "supp_qty": "", "supp_unit": "", "line_kind": kind,
                    "reviewed": False, "source_page": 1, "confidence": "saved-layout",
                    "notes": "Fast table extraction; verify against the source PDF.",
                })
                if kind == "charge_invoice":
                    parsed[-1]["linked_position"] = 1
        if parsed:
            freight = _money(_first(r"(?i)freight costs?\s*:\s*([\d.,]+)", text))
            if freight:
                parsed.append({
                    "sku": "", "description": "Freight", "quantity": "1", "unit": "",
                    "raw_commodity_code": "", "hs_code": "", "origin_country": "",
                    "invoice_value": freight, "statistical_value": "", "unit_net_mass": "", "net_mass": "",
                    "net_mass_overridden": 0, "supp_qty": "", "supp_unit": "", "line_kind": "charge_stat",
                    "linked_position": 1, "reviewed": False, "source_page": 1, "confidence": "saved-layout",
                    "notes": "Freight shown on invoice; confirm it covers transport to the Malta border.",
                })
            draft["lines"] = parsed
            draft["adapter"] = "fast-table"
            draft["notes"] += " A reusable table layout was recognised; local AI was skipped."
            return draft
    return draft


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


def extract_invoice(path: Path, display_name: str | None = None, supplier_profiles: list[dict] | None = None, force_ai: bool = False) -> tuple[dict, str, str]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    page_lines: list[list[str]] = []
    page_texts: list[str] = []
    used_ocr = False
    tables: list[list[list[str | None]]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            if len(text.strip()) < 40:
                try:
                    import pytesseract
                    ocr_text = pytesseract.image_to_string(page.to_image(resolution=200).original)
                    if len(ocr_text.strip()) > len(text.strip()):
                        text = ocr_text
                        used_ocr = True
                except Exception:
                    pass
            page_texts.append(text)
            page_lines.append(text.splitlines())
            try:
                tables.extend(page.extract_tables())
            except Exception:
                pass
    combined = "\n".join(page_texts)
    draft = _base_draft(display_name or path.name, combined, len(page_texts))
    matched_profile = _supplier_profile(combined, supplier_profiles)
    if matched_profile:
        _apply_supplier_defaults(draft, matched_profile)
    if force_ai:
        marked_text = "\n".join(f"--- PAGE {index} ---\n{text}" for index, text in enumerate(page_texts, 1))
        vision_pages = _vision_pages(path)
        ai_result, ai_message = extract_structured_invoice(marked_text, vision_pages)
        if ai_result:
            draft = _apply_ai_draft(draft, ai_result)
            if vision_pages:
                draft["adapter"] = "vision-ai"
                draft["notes"] += " Invoice page images were analysed to preserve the table layout."
            if matched_profile:
                _apply_supplier_defaults(draft, matched_profile)
        elif ai_message:
            draft["notes"] += " " + ai_message
    elif matched_profile and matched_profile.get("layout_mapping") and (
        not matched_profile.get("layout_fingerprint") or matched_profile.get("layout_fingerprint") == layout_fingerprint(path)
    ):
        draft = _parse_saved_mapping(path, draft, matched_profile)
        if not draft["lines"]:
            marked_text = "\n".join(f"--- PAGE {index} ---\n{text}" for index, text in enumerate(page_texts, 1))
            vision_pages = _vision_pages(path)
            ai_result, ai_message = extract_structured_invoice(marked_text, vision_pages)
            if ai_result:
                draft = _apply_ai_draft(draft, ai_result)
                _apply_supplier_defaults(draft, matched_profile)
                draft["adapter"] = "vision-ai" if vision_pages else "local-ai"
            elif ai_message:
                draft["notes"] += " " + ai_message
    elif "Paul Stricker" in combined:
        draft = _parse_stricker(draft, page_lines, combined)
    elif "midocean" in combined.lower() or "Mid Ocean Brands" in combined:
        draft = _parse_midocean(draft, page_lines, combined)
    else:
        draft = _parse_common_tables(draft, tables, combined)
        if not draft["lines"]:
            marked_text = "\n".join(f"--- PAGE {index} ---\n{text}" for index, text in enumerate(page_texts, 1))
            vision_pages = _vision_pages(path)
            ai_result, ai_message = extract_structured_invoice(marked_text, vision_pages)
            if ai_result:
                draft = _apply_ai_draft(draft, ai_result)
                if vision_pages:
                    draft["adapter"] = "vision-ai"
                    draft["notes"] += " Invoice page images were analysed to preserve the table layout."
                if matched_profile:
                    _apply_supplier_defaults(draft, matched_profile)
            elif ai_message:
                draft["notes"] += " " + ai_message
    if used_ocr:
        draft["notes"] += " One or more pages were read with local OCR."
        if draft["adapter"] == "general":
            draft["adapter"] = "ocr"
    draft = _validate_and_normalise(draft)
    if not draft["lines"]:
        draft["notes"] += " No reliable goods table was detected; add lines manually."
    if any(not page.strip() for page in page_texts):
        draft["notes"] += " One or more pages have no native text and need OCR or manual review."
    return draft, combined, digest
