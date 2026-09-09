from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL",
    "HU", "IE", "IT", "LV", "LT", "LU", "NL", "PL", "PT", "RO", "SK", "SI",
    "ES", "SE", "XI",
}


def decimal_or_none(value: str | None) -> Decimal | None:
    try:
        result = Decimal(str(value)) if value not in (None, "") else None
        return result if result is not None and result.is_finite() else None
    except InvalidOperation:
        return None


def rounded_whole(value: str) -> int:
    number = decimal_or_none(value)
    if number is None:
        raise ValueError("A valid number is required")
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def invoice_issues(invoice: dict, lines: list[dict], profile: dict | None = None) -> list[dict]:
    issues: list[dict] = []

    def add(code: str, message: str, field: str = "", line_id: int | None = None, severity: str = "blocking"):
        issues.append({"code": code, "message": message, "field": field, "line_id": line_id, "severity": severity})

    for field, label in [
        ("supplier_name", "Supplier name"), ("supplier_vat_country", "Supplier VAT country"),
        ("supplier_vat_number", "Supplier VAT number"), ("invoice_number", "Invoice number"),
        ("invoice_date", "Invoice date"), ("arrival_date", "Actual arrival date" if invoice.get("flow", "A") == "A" else "Actual dispatch date"),
        ("currency", "Invoice currency"), ("consignment_country", "Country of consignment"),
        ("mode_transport", "Mode of transport"), ("terms_delivery", "Terms of delivery"),
        ("nature_transaction", "Nature of transaction"),
    ]:
        if not str(invoice.get(field, "")).strip():
            add("missing_invoice_field", f"{label} is missing.", field)
    vat_country = str(invoice.get("supplier_vat_country", "")).upper()
    if vat_country and vat_country not in EU_COUNTRIES:
        add("vat_country_not_eu", "Supplier VAT country must be an EU member code (Greece uses EL; Northern Ireland uses XI).", "supplier_vat_country")
    coc = str(invoice.get("consignment_country", "")).upper()
    if coc and coc not in EU_COUNTRIES:
        add("consignment_not_eu", "Country of consignment must be an EU member code for Intrastat.", "consignment_country")
    if invoice.get("supplier_vat_number") and not re.fullmatch(r"[A-Z0-9]{4,14}", str(invoice["supplier_vat_number"]).upper()):
        add("supplier_vat_format", "Check the supplier VAT number; enter it without the country prefix.", "supplier_vat_number")
    if invoice.get("flow") not in {"A", "D"}:
        add("invalid_flow", "Flow must be A for arrivals or D for dispatches.", "flow")
    if invoice.get("mode_transport") and str(invoice["mode_transport"]) not in {"1", "2", "3", "4", "5", "7", "8", "9"}:
        add("invalid_transport", "Choose a valid one-digit mode of transport code.", "mode_transport")
    if invoice.get("terms_delivery") and not re.fullmatch(r"[A-Z]{3}", str(invoice["terms_delivery"]).upper()):
        add("invalid_delivery_terms", "Terms of delivery must be a three-letter Incoterm.", "terms_delivery")
    if invoice.get("nature_transaction") and not re.fullmatch(r"\d{2}", str(invoice["nature_transaction"])):
        add("invalid_transaction", "Nature of transaction must be a two-digit code.", "nature_transaction")
    if invoice.get("currency") and not re.fullmatch(r"[A-Z]{3}", str(invoice["currency"]).upper()):
        add("invalid_currency", "Invoice currency must be a three-letter code.", "currency")
    for field, label in (("invoice_date", "Invoice date"), ("arrival_date", "Movement date")):
        value = str(invoice.get(field, ""))
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                add("invalid_date", f"{label} must be a valid calendar date.", field)
    total = decimal_or_none(invoice.get("total_value"))
    accounted = Decimal("0")
    goods_count = 0
    for line in lines:
        kind = line.get("line_kind", "goods")
        amount = decimal_or_none(line.get("invoice_value"))
        if kind != "excluded" and amount is not None:
            accounted += amount
        if kind == "goods":
            goods_count += 1
            for field, label in [
                ("description", "Description"), ("quantity", "Quantity"), ("hs_code", "8-digit commodity code"),
                ("origin_country", "Country of origin"), ("invoice_value", "Invoice value"),
                ("statistical_value", "Statistical value"), ("net_mass", "Net mass"),
            ]:
                if not str(line.get(field, "")).strip():
                    add("missing_line_field", f"{label} is missing on this goods row.", field, line.get("id"))
            if line.get("hs_code") and not re.fullmatch(r"\d{8}", str(line["hs_code"])):
                add("invalid_hs_code", "Commodity code must contain exactly 8 digits.", "hs_code", line.get("id"))
            if line.get("origin_country") and not re.fullmatch(r"[A-Z]{2}", str(line["origin_country"]).upper()):
                add("invalid_origin", "Country of origin must be a 2-letter code.", "origin_country", line.get("id"))
            inv_value = decimal_or_none(line.get("invoice_value"))
            stat_value = decimal_or_none(line.get("statistical_value"))
            mass = decimal_or_none(line.get("net_mass"))
            qty = decimal_or_none(line.get("quantity"))
            if inv_value is not None and inv_value < 0:
                add("negative_goods_value", "A negative goods value needs the amendment or credit-note workflow.", "invoice_value", line.get("id"))
            if stat_value is not None and inv_value is not None and stat_value < inv_value:
                add("stat_below_invoice", "Statistical value cannot be below invoice value.", "statistical_value", line.get("id"))
            if mass is not None and mass <= 0:
                add("invalid_mass", "Net mass must be greater than zero.", "net_mass", line.get("id"))
            if qty is not None and qty <= 0:
                add("invalid_quantity", "Quantity must be greater than zero.", "quantity", line.get("id"))
            if not line.get("reviewed"):
                add("line_unreviewed", "Review this extracted goods row.", "reviewed", line.get("id"))
            special_codes = {"84151010", "84151090", "84191900", "84158100", "84158200"}
            if line.get("hs_code") in special_codes:
                if not line.get("special_quantity"):
                    add("missing_special_quantity", "This commodity code requires the special quantity field.", "special_quantity", line.get("id"))
                if not line.get("range_value"):
                    add("missing_range", "This commodity code requires a range value.", "range_value", line.get("id"))
            if line.get("hs_code") == "84191900" and not line.get("collector_type"):
                add("missing_collector_type", "Commodity 84191900 requires the collector type.", "collector_type", line.get("id"))
        elif kind.startswith("charge"):
            if not line.get("linked_line_id"):
                add("unallocated_charge", "Link this charge to a goods row or exclude it with a note.", "linked_line_id", line.get("id"))
            if kind == "charge":
                add("unresolved_charge", "Choose whether this charge belongs in invoice value, statistical value only, or should be excluded.", "line_kind", line.get("id"))
            if not line.get("reviewed"):
                add("charge_unreviewed", "Confirm how this charge should be treated.", "reviewed", line.get("id"))
    if not goods_count:
        add("no_goods", "Add at least one goods row.", "lines")
    if total is None:
        add("missing_total", "Invoice total is missing or invalid.", "total_value")
    elif abs(total - accounted) > Decimal("0.02"):
        add("reconciliation", f"Accounted rows differ from the invoice total by {total - accounted:.2f} {invoice.get('currency') or ''}.", "total_value")
    if profile:
        for field, label in [("trader_vat", "Trader VAT"), ("email", "Email"), ("signatory", "Signatory"),
                             ("id_card", "ID card"), ("telephone", "Telephone"), ("locality", "Locality")]:
            if not str(profile.get(field, "")).strip():
                add("missing_profile", f"Complete {label} in Organisation settings before export.", field)
        if profile.get("email") and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(profile["email"])):
            add("invalid_profile_email", "Enter a valid declarant email address in Organisation settings.", "email")
    return issues


def readiness(invoice: dict, lines: list[dict], profile: dict | None = None) -> dict:
    issues = invoice_issues(invoice, lines, profile)
    blocking = sum(1 for issue in issues if issue["severity"] == "blocking")
    return {"issues": issues, "blocking_count": blocking, "ready": blocking == 0}
