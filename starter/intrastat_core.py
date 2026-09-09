"""Supplier-independent preparation helpers. Not an XML exporter or tax rules engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path


def document_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_decimal(text: str, *, decimal_separator: str) -> Decimal:
    """Parse an explicitly selected locale. Currency symbols must be separate fields.

    Examples: ('1.805,96', ',') and ('1,805.96', '.') both mean 1805.96.
    Reject malformed grouping rather than silently changing the value.
    """
    if decimal_separator not in {",", "."}:
        raise ValueError("Choose ',' or '.' as the decimal separator")
    value = text.strip().replace("\u00a0", " ").replace("\u202f", " ")
    sign = ""
    if value.startswith(("-", "+")):
        sign, value = value[0], value[1:]
    pieces = value.split(decimal_separator)
    if len(pieces) > 2:
        raise ValueError("Multiple decimal separators")
    whole = pieces[0]
    fraction = pieces[1] if len(pieces) == 2 else None
    grouping = "." if decimal_separator == "," else ","
    if " " in whole and grouping in whole:
        raise ValueError("Mixed grouping separators")
    separator = " " if " " in whole else grouping
    if separator in whole:
        if not re.fullmatch(r"[0-9]{1,3}(?:" + re.escape(separator) + r"[0-9]{3})+", whole):
            raise ValueError("Invalid thousands grouping")
        whole = whole.replace(separator, "")
    if not re.fullmatch(r"[0-9]+", whole):
        raise ValueError("Invalid integer part")
    if fraction is not None and not re.fullmatch(r"[0-9]+", fraction):
        raise ValueError("Invalid fractional part")
    return Decimal(sign + whole + ("." + fraction if fraction is not None else ""))


def cn_candidate(raw: str) -> str:
    """Return an 8-digit candidate, NOT proof of valid classification."""
    digits = re.sub(r"\s", "", raw)
    if not re.fullmatch(r"(?:[0-9]{8}|[0-9]{10})", digits):
        raise ValueError("Expected a printed 8- or 10-digit code")
    return digits[:8]


def _decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, (bool, float)):
        raise ValueError("Use Decimal, a decimal string or an integer; floats are unsafe")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("Value must be finite")
    return result


def allocate_amount(total: Decimal | str | int,
                    weights: list[Decimal | str | int]) -> list[Decimal]:
    """Largest-remainder allocation in cents, stable by input order for ties.

    The caller must approve what these weights mean. Never infer net weights
    from prices. Negative charges/discounts are supported as arithmetic only.
    """
    amount = _decimal(total)
    parts = [_decimal(w) for w in weights]
    if not parts or any(w < 0 for w in parts) or sum(parts) <= 0:
        raise ValueError("Need nonnegative weights with a positive sum")
    cents = abs(amount) * 100
    if cents != cents.to_integral_value():
        raise ValueError("Total must have no fractional cent")
    exact = [cents * w / sum(parts) for w in parts]
    integers = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
    remainder = int(cents) - sum(integers)
    order = sorted(range(len(parts)), key=lambda i: (-(exact[i] - integers[i]), i))
    for i in order[:remainder]:
        integers[i] += 1
    sign = -1 if amount < 0 else 1
    return [Decimal(i * sign) / 100 for i in integers]


def reconciliation_difference(expected: Decimal | str | int,
                              amounts: list[Decimal | str | int]) -> Decimal:
    """Expected minus accounted amounts; caller must ensure the same currency."""
    return _decimal(expected) - sum((_decimal(x) for x in amounts), Decimal(0))


def extract_pdf_text(path: str | Path) -> dict:
    """Extract native text for any supplier; no invoice-field inference or OCR.

    Empty text is one OCR signal only: non-empty garbled or partial extraction
    still needs review. Production ingestion also needs upload/resource limits.
    """
    import pdfplumber

    source = Path(path)
    pages = []
    with pdfplumber.open(source) as pdf:
        for number, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            pages.append({"page": number, "text": text,
                          "empty_text_requires_ocr_or_manual_review": not bool(text.strip())})
    return {"filename": source.name, "sha256": document_hash(source.read_bytes()),
            "status": "unreviewed_text_only", "pages": pages}


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PDF text to an unreviewed JSON file")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = extract_pdf_text(args.pdf)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Avoid accidentally replacing an invoice, existing draft or prior output.
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
