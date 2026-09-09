from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import APP_PASSWORD, APP_TITLE, BASE_DIR, EXPORT_DIR, MAX_UPLOAD_BYTES, SCHEMA_DIR, UPLOAD_DIR, ensure_directories
from .db import connect, init_db, row, rows, transaction, utc_now
from .exporter import csv_bytes, declaration_rows, inspect_schema, sha256, xml_bytes
from .extractors import extract_invoice
from .rules import invoice_issues, readiness


ORG_ID = 1
TEMPLATE_PATH = BASE_DIR / "app" / "templates" / "index.html"
SCHEMA_PATH = SCHEMA_DIR / "malta-intrastat.xsd"

INVOICE_FIELDS = {
    "supplier_name", "supplier_vat_country", "supplier_vat_number", "invoice_number",
    "invoice_date", "arrival_date", "currency", "total_value", "consignment_country",
    "mode_transport", "terms_delivery", "nature_transaction", "flow", "notes",
}
LINE_FIELDS = {
    "sku", "description", "quantity", "unit", "raw_commodity_code", "hs_code",
    "origin_country", "invoice_value", "statistical_value", "net_mass", "supp_qty",
    "supp_unit", "special_quantity", "collector_type", "range_value", "line_kind",
    "linked_line_id", "reviewed", "source_page", "confidence", "notes",
}
PROFILE_FIELDS = {
    "trader_vat", "email", "agent_vat", "trading_licence", "signatory", "id_card",
    "telephone", "locality", "default_flow", "default_mot", "default_not",
}
UPPER_FIELDS = {
    "supplier_vat_country", "currency", "consignment_country", "terms_delivery", "flow",
    "unit", "hs_code", "origin_country", "supp_unit", "default_flow",
}
DECIMAL_FIELDS = {"total_value", "quantity", "invoice_value", "statistical_value", "net_mass", "supp_qty", "special_quantity", "unit_net_mass"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_directories()
    init_db()
    yield


app = FastAPI(title=APP_TITLE, version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if APP_PASSWORD and request.url.path != "/api/health":
        supplied = request.headers.get("authorization", "")
        expected = "Basic " + base64.b64encode(f"intrastat:{APP_PASSWORD}".encode()).decode()
        if not hmac.compare_digest(supplied, expected):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="IntraReady"'})
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
        if request.headers.get("x-intraready-request") != "1":
            return JSONResponse({"detail": "Missing same-origin request header"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-src 'self'; object-src 'self'; base-uri 'self'; form-action 'self'"
    )
    return response


def _profile() -> dict:
    return row("SELECT * FROM profiles WHERE organisation_id = ?", (ORG_ID,)) or {}


def _invoice(invoice_id: int) -> dict:
    found = row("SELECT * FROM invoices WHERE id = ? AND organisation_id = ?", (invoice_id, ORG_ID))
    if not found:
        raise HTTPException(404, "Invoice not found")
    return found


def _lines(invoice_id: int) -> list[dict]:
    return rows("SELECT * FROM invoice_lines WHERE invoice_id = ? ORDER BY position", (invoice_id,))


def _payload(invoice_id: int) -> dict:
    invoice = _invoice(invoice_id)
    lines_list = _lines(invoice_id)
    document = row("SELECT id, filename, page_count, extraction_method FROM documents WHERE id = ?", (invoice.get("document_id"),)) if invoice.get("document_id") else None
    result = dict(invoice)
    result["lines"] = lines_list
    result["document"] = document
    result["readiness"] = readiness(invoice, lines_list, _profile())
    return result


def _clean_value(field: str, value):
    if field == "reviewed":
        return 1 if value else 0
    if field == "linked_line_id" and value in ("", None):
        return None
    if value is None:
        return ""
    value = str(value).strip()
    if field in UPPER_FIELDS:
        value = value.upper()
    if field in DECIMAL_FIELDS and value:
        try:
            number = Decimal(value)
            if not number.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            raise HTTPException(422, f"{field} must be a plain decimal number using a dot")
        value = format(number, "f")
    if field == "hs_code" and value:
        value = re.sub(r"\s", "", value)
    return value


def _record_event(connection, invoice_id: int | None, event_type: str, details: dict | str = ""):
    connection.execute(
        "INSERT INTO review_events(organisation_id, invoice_id, event_type, details, created_at) VALUES(?,?,?,?,?)",
        (ORG_ID, invoice_id, event_type, json.dumps(details) if isinstance(details, dict) else details, utc_now()),
    )


def _invalidate(connection, invoice_id: int, event_type: str, details: dict):
    connection.execute(
        "UPDATE invoices SET status = 'needs_review', revision = revision + 1, updated_at = ? WHERE id = ? AND organisation_id = ?",
        (utc_now(), invoice_id, ORG_ID),
    )
    _record_event(connection, invoice_id, event_type, details)


def _suggest_from_catalogue(connection, invoice_id: int):
    invoice = dict(connection.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone())
    supplier_vat = invoice["supplier_vat_country"] + invoice["supplier_vat_number"]
    for line_row in connection.execute("SELECT * FROM invoice_lines WHERE invoice_id = ? AND line_kind = 'goods'", (invoice_id,)).fetchall():
        line = dict(line_row)
        if not line["sku"]:
            continue
        fact = connection.execute(
            "SELECT * FROM product_facts WHERE organisation_id = ? AND supplier_vat = ? AND sku = ?",
            (ORG_ID, supplier_vat, line["sku"]),
        ).fetchone()
        if not fact:
            continue
        fact = dict(fact)
        updates = {}
        for field in ("hs_code", "origin_country", "supp_unit"):
            if not line[field] and fact[field]:
                updates[field] = fact[field]
        if not line["net_mass"] and fact["unit_net_mass"] and line["quantity"]:
            updates["net_mass"] = format(Decimal(fact["unit_net_mass"]) * Decimal(line["quantity"]), "f")
        if updates:
            updates["confidence"] = "catalogue-suggested"
            assignments = ", ".join(f"{field} = ?" for field in updates)
            connection.execute(f"UPDATE invoice_lines SET {assignments}, updated_at = ? WHERE id = ?", (*updates.values(), utc_now(), line["id"]))


@app.get("/", response_class=HTMLResponse)
def index():
    return TEMPLATE_PATH.read_text(encoding="utf-8").replace("{{APP_TITLE}}", APP_TITLE)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/bootstrap")
def bootstrap():
    invoice_list = rows(
        """SELECT i.*, d.filename,
        (SELECT COUNT(*) FROM invoice_lines l WHERE l.invoice_id=i.id AND l.line_kind='goods') goods_count
        FROM invoices i LEFT JOIN documents d ON d.id=i.document_id
        WHERE i.organisation_id=? ORDER BY i.updated_at DESC""", (ORG_ID,)
    )
    profile = _profile()
    for invoice in invoice_list:
        ready = readiness(invoice, _lines(invoice["id"]), profile)
        invoice["blocking_count"] = ready["blocking_count"]
        invoice["ready"] = ready["ready"]
    stats = {
        "total": len(invoice_list),
        "needs_review": sum(1 for item in invoice_list if item["status"] in {"needs_review", "draft"}),
        "approved": sum(1 for item in invoice_list if item["status"] == "approved"),
        "exported": sum(1 for item in invoice_list if item["status"] in {"exported", "submitted"}),
    }
    return {
        "app_title": APP_TITLE, "profile": profile, "invoices": invoice_list, "stats": stats,
        "schema": inspect_schema(SCHEMA_PATH), "catalogue_count": row("SELECT COUNT(*) total FROM product_facts WHERE organisation_id=?", (ORG_ID,))["total"],
        "current_period": date.today().strftime("%Y-%m"),
    }


@app.post("/api/upload")
async def upload_invoices(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(422, "Choose at least one PDF")
    results = []
    for upload in files:
        filename = Path(upload.filename or "invoice.pdf").name
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            results.append({"filename": filename, "error": "File exceeds the upload limit"})
            continue
        if not data.startswith(b"%PDF-"):
            results.append({"filename": filename, "error": "Only genuine PDF files are accepted"})
            continue
        digest = hashlib.sha256(data).hexdigest()
        existing = row(
            "SELECT i.id FROM documents d JOIN invoices i ON i.document_id=d.id WHERE d.organisation_id=? AND d.sha256=?",
            (ORG_ID, digest),
        )
        if existing:
            results.append({"filename": filename, "invoice_id": existing["id"], "duplicate": True})
            continue
        storage_name = f"{uuid.uuid4().hex}.pdf"
        destination = UPLOAD_DIR / storage_name
        destination.write_bytes(data)
        try:
            draft, extracted_text, verified_digest = extract_invoice(destination, filename)
            if verified_digest != digest:
                raise ValueError("Stored file hash changed during extraction")
        except Exception as exc:
            destination.unlink(missing_ok=True)
            results.append({"filename": filename, "error": f"Could not read PDF: {exc}"})
            continue
        now = utc_now()
        with transaction() as connection:
            document_id = connection.execute(
                """INSERT INTO documents(organisation_id,sha256,filename,storage_name,content_type,size_bytes,page_count,extracted_text,extraction_method,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (ORG_ID, digest, filename, storage_name, "application/pdf", len(data), draft["page_count"], extracted_text, draft["adapter"], now),
            ).lastrowid
            invoice_columns = [field for field in INVOICE_FIELDS if field in draft]
            invoice_id = connection.execute(
                f"INSERT INTO invoices(organisation_id,document_id,{','.join(invoice_columns)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in invoice_columns)},?,?)",
                (ORG_ID, document_id, *(draft[field] for field in invoice_columns), now, now),
            ).lastrowid
            positions_to_ids = {}
            pending_links = []
            for position, line in enumerate(draft["lines"], 1):
                values = {field: line.get(field, "") for field in LINE_FIELDS if field not in {"linked_line_id"}}
                values["reviewed"] = 1 if values.get("reviewed") else 0
                columns = list(values)
                line_id = connection.execute(
                    f"INSERT INTO invoice_lines(invoice_id,position,{','.join(columns)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in columns)},?,?)",
                    (invoice_id, position, *(values[column] for column in columns), now, now),
                ).lastrowid
                positions_to_ids[position] = line_id
                if line.get("linked_position"):
                    pending_links.append((line_id, line["linked_position"]))
            for line_id, linked_position in pending_links:
                connection.execute("UPDATE invoice_lines SET linked_line_id=? WHERE id=?", (positions_to_ids.get(linked_position), line_id))
            _suggest_from_catalogue(connection, invoice_id)
            _record_event(connection, invoice_id, "invoice_uploaded", {"filename": filename, "adapter": draft["adapter"]})
        results.append({"filename": filename, "invoice_id": invoice_id, "duplicate": False, "adapter": draft["adapter"]})
    return {"results": results}


@app.get("/api/documents/{document_id}/file")
def document_file(document_id: int):
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (document_id, ORG_ID))
    if not document:
        raise HTTPException(404, "Document not found")
    path = UPLOAD_DIR / document["storage_name"]
    if not path.is_file():
        raise HTTPException(404, "Stored PDF is missing")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=Path(document["filename"]).name,
        content_disposition_type="inline",
    )


@app.post("/api/invoices")
def create_manual_invoice():
    profile = _profile()
    now = utc_now()
    with transaction() as connection:
        invoice_id = connection.execute(
            """INSERT INTO invoices(organisation_id,mode_transport,nature_transaction,flow,notes,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (ORG_ID, profile.get("default_mot", "4"), profile.get("default_not", "11"), profile.get("default_flow", "A"), "Created manually.", now, now),
        ).lastrowid
        _record_event(connection, invoice_id, "invoice_created", {})
    return _payload(invoice_id)


@app.get("/api/invoices/{invoice_id}")
def get_invoice(invoice_id: int):
    return _payload(invoice_id)


@app.patch("/api/invoices/{invoice_id}")
async def update_invoice(invoice_id: int, request: Request):
    current = _invoice(invoice_id)
    if current["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked. Prepare a separate amendment draft so the original record remains intact.")
    body = await request.json()
    expected_revision = body.pop("revision", None)
    if expected_revision is not None and int(expected_revision) != current["revision"]:
        raise HTTPException(409, "This invoice changed in another tab. Reload before saving.")
    updates = {field: _clean_value(field, value) for field, value in body.items() if field in INVOICE_FIELDS}
    if not updates:
        raise HTTPException(422, "No supported fields supplied")
    with transaction() as connection:
        assignments = ", ".join(f"{field}=?" for field in updates)
        connection.execute(f"UPDATE invoices SET {assignments} WHERE id=? AND organisation_id=?", (*updates.values(), invoice_id, ORG_ID))
        _invalidate(connection, invoice_id, "invoice_updated", {"fields": list(updates)})
    return _payload(invoice_id)


@app.post("/api/invoices/{invoice_id}/lines")
async def create_line(invoice_id: int, request: Request):
    current = _invoice(invoice_id)
    if current["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked. Prepare a separate amendment draft.")
    body = await request.json()
    values = {field: _clean_value(field, value) for field, value in body.items() if field in LINE_FIELDS}
    values.setdefault("line_kind", "goods")
    values.setdefault("reviewed", 0)
    now = utc_now()
    with transaction() as connection:
        position = connection.execute("SELECT COALESCE(MAX(position),0)+1 FROM invoice_lines WHERE invoice_id=?", (invoice_id,)).fetchone()[0]
        columns = list(values)
        line_id = connection.execute(
            f"INSERT INTO invoice_lines(invoice_id,position,{','.join(columns)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in columns)},?,?)",
            (invoice_id, position, *(values[column] for column in columns), now, now),
        ).lastrowid
        _invalidate(connection, invoice_id, "line_created", {"line_id": line_id})
    return _payload(invoice_id)


@app.patch("/api/lines/{line_id}")
async def update_line(line_id: int, request: Request):
    found = row("SELECT l.*,i.organisation_id FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.id=?", (line_id,))
    if not found or found["organisation_id"] != ORG_ID:
        raise HTTPException(404, "Line not found")
    if _invoice(found["invoice_id"])["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked. Prepare a separate amendment draft.")
    body = await request.json()
    updates = {field: _clean_value(field, value) for field, value in body.items() if field in LINE_FIELDS}
    if "line_kind" in updates and updates["line_kind"] not in {"goods", "charge", "charge_invoice", "charge_stat", "excluded"}:
        raise HTTPException(422, "Unsupported row type")
    if "linked_line_id" in updates and updates["linked_line_id"] is not None:
        target = row("SELECT id FROM invoice_lines WHERE id=? AND invoice_id=? AND line_kind='goods'", (int(updates["linked_line_id"]), found["invoice_id"]))
        if not target:
            raise HTTPException(422, "Charges can only link to a goods row on the same invoice")
        updates["linked_line_id"] = int(updates["linked_line_id"])
    if not updates:
        raise HTTPException(422, "No supported fields supplied")
    with transaction() as connection:
        assignments = ", ".join(f"{field}=?" for field in updates)
        connection.execute(f"UPDATE invoice_lines SET {assignments},updated_at=? WHERE id=?", (*updates.values(), utc_now(), line_id))
        _invalidate(connection, found["invoice_id"], "line_updated", {"line_id": line_id, "fields": list(updates)})
    return _payload(found["invoice_id"])


@app.delete("/api/lines/{line_id}")
def delete_line(line_id: int):
    found = row("SELECT l.invoice_id,i.organisation_id FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.id=?", (line_id,))
    if not found or found["organisation_id"] != ORG_ID:
        raise HTTPException(404, "Line not found")
    if _invoice(found["invoice_id"])["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked. Prepare a separate amendment draft.")
    with transaction() as connection:
        connection.execute("DELETE FROM invoice_lines WHERE id=?", (line_id,))
        # Keep simple stable display positions after deletion.
        for position, existing in enumerate(connection.execute("SELECT id FROM invoice_lines WHERE invoice_id=? ORDER BY position", (found["invoice_id"],)), 1):
            connection.execute("UPDATE invoice_lines SET position=? WHERE id=?", (position, existing[0]))
        _invalidate(connection, found["invoice_id"], "line_deleted", {"line_id": line_id})
    return _payload(found["invoice_id"])


@app.delete("/api/invoices/{invoice_id}")
def delete_invoice(invoice_id: int):
    invoice = _invoice(invoice_id)
    if invoice["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked and cannot be deleted.")
    document = row("SELECT storage_name FROM documents WHERE id=?", (invoice.get("document_id"),)) if invoice.get("document_id") else None
    with transaction() as connection:
        connection.execute("DELETE FROM invoices WHERE id=? AND organisation_id=?", (invoice_id, ORG_ID))
        if invoice.get("document_id"):
            connection.execute("DELETE FROM documents WHERE id=? AND organisation_id=?", (invoice["document_id"], ORG_ID))
        _record_event(connection, None, "invoice_deleted", {"invoice_number": invoice["invoice_number"]})
    if document:
        (UPLOAD_DIR / document["storage_name"]).unlink(missing_ok=True)
    return {"deleted": True}


@app.post("/api/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id: int):
    invoice = _invoice(invoice_id)
    check = readiness(invoice, _lines(invoice_id), _profile())
    if not check["ready"]:
        raise HTTPException(422, {"message": "Resolve blocking issues before approval.", "issues": check["issues"]})
    with transaction() as connection:
        connection.execute("UPDATE invoices SET status='approved',updated_at=? WHERE id=?", (utc_now(), invoice_id))
        _record_event(connection, invoice_id, "invoice_approved", {"revision": invoice["revision"]})
    return _payload(invoice_id)


@app.post("/api/invoices/{invoice_id}/reopen")
def reopen_invoice(invoice_id: int):
    _invoice(invoice_id)
    with transaction() as connection:
        connection.execute("UPDATE invoices SET status='needs_review',updated_at=? WHERE id=?", (utc_now(), invoice_id))
        _record_event(connection, invoice_id, "invoice_reopened", {})
    return _payload(invoice_id)


@app.get("/api/catalogue")
def get_catalogue():
    return rows("SELECT * FROM product_facts WHERE organisation_id=? ORDER BY supplier_name,sku", (ORG_ID,))


@app.post("/api/catalogue")
async def save_catalogue_fact(request: Request):
    body = await request.json()
    sku = str(body.get("sku", "")).strip()
    if not sku:
        raise HTTPException(422, "SKU is required")
    fields = ["supplier_vat", "supplier_name", "description", "hs_code", "origin_country", "unit_net_mass", "supp_unit", "evidence", "effective_from"]
    values = {field: _clean_value(field, body.get(field, "")) for field in fields}
    values["sku"] = sku
    with transaction() as connection:
        connection.execute(
            """INSERT INTO product_facts(organisation_id,supplier_vat,supplier_name,sku,description,hs_code,origin_country,unit_net_mass,supp_unit,evidence,effective_from,verified_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(organisation_id,supplier_vat,sku) DO UPDATE SET supplier_name=excluded.supplier_name,
            description=excluded.description,hs_code=excluded.hs_code,origin_country=excluded.origin_country,
            unit_net_mass=excluded.unit_net_mass,supp_unit=excluded.supp_unit,evidence=excluded.evidence,
            effective_from=excluded.effective_from,verified_at=excluded.verified_at""",
            (ORG_ID, values["supplier_vat"], values["supplier_name"], sku, values["description"], values["hs_code"],
             values["origin_country"], values["unit_net_mass"], values["supp_unit"], values["evidence"],
             values["effective_from"], utc_now()),
        )
    return get_catalogue()


@app.post("/api/catalogue/from-line/{line_id}")
def save_catalogue_from_line(line_id: int):
    found = row("""SELECT l.*,i.supplier_name,i.supplier_vat_country,i.supplier_vat_number,i.organisation_id
                   FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.id=?""", (line_id,))
    if not found or found["organisation_id"] != ORG_ID or not found["sku"]:
        raise HTTPException(422, "This line needs a SKU before it can be remembered")
    unit_mass = ""
    try:
        if Decimal(found["quantity"]) > 0 and Decimal(found["net_mass"]) > 0:
            unit_mass = format(Decimal(found["net_mass"]) / Decimal(found["quantity"]), "f")
    except (InvalidOperation, TypeError):
        pass
    supplier_vat = found["supplier_vat_country"] + found["supplier_vat_number"]
    with transaction() as connection:
        connection.execute(
            """INSERT INTO product_facts(organisation_id,supplier_vat,supplier_name,sku,description,hs_code,origin_country,unit_net_mass,supp_unit,evidence,effective_from,verified_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(organisation_id,supplier_vat,sku) DO UPDATE SET
            supplier_name=excluded.supplier_name,description=excluded.description,hs_code=excluded.hs_code,
            origin_country=excluded.origin_country,unit_net_mass=excluded.unit_net_mass,
            supp_unit=excluded.supp_unit,evidence=excluded.evidence,verified_at=excluded.verified_at""",
            (ORG_ID, supplier_vat, found["supplier_name"], found["sku"], found["description"], found["hs_code"],
             found["origin_country"], unit_mass, found["supp_unit"], f"Verified from invoice {found['invoice_id']}", "", utc_now()),
        )
        _record_event(connection, found["invoice_id"], "product_remembered", {"line_id": line_id, "sku": found["sku"]})
    return {"saved": True, "unit_net_mass": unit_mass}


@app.delete("/api/catalogue/{fact_id}")
def delete_catalogue_fact(fact_id: int):
    with transaction() as connection:
        result = connection.execute("DELETE FROM product_facts WHERE id=? AND organisation_id=?", (fact_id, ORG_ID))
        if result.rowcount == 0:
            raise HTTPException(404, "Catalogue item not found")
    return {"deleted": True}


@app.get("/api/profile")
def get_profile():
    return _profile()


@app.patch("/api/profile")
async def update_profile(request: Request):
    body = await request.json()
    updates = {field: _clean_value(field, value) for field, value in body.items() if field in PROFILE_FIELDS}
    if not updates:
        raise HTTPException(422, "No supported fields supplied")
    with transaction() as connection:
        assignments = ", ".join(f"{field}=?" for field in updates)
        connection.execute(f"UPDATE profiles SET {assignments},updated_at=? WHERE organisation_id=?", (*updates.values(), utc_now(), ORG_ID))
        connection.execute(
            "UPDATE invoices SET status='needs_review',updated_at=? WHERE organisation_id=? AND status IN ('approved','exported')",
            (utc_now(), ORG_ID),
        )
        _record_event(connection, None, "profile_updated", {"fields": list(updates)})
    return _profile()


@app.post("/api/schema")
async def upload_schema(file: UploadFile = File(...)):
    data = await file.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise HTTPException(413, "XSD is unexpectedly large")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise HTTPException(422, "DTD and entity declarations are not allowed")
    temporary = SCHEMA_DIR / f".{uuid.uuid4().hex}.xsd"
    temporary.write_bytes(data)
    status = inspect_schema(temporary)
    if not status.get("valid"):
        temporary.unlink(missing_ok=True)
        raise HTTPException(422, status)
    temporary.replace(SCHEMA_PATH)
    return inspect_schema(SCHEMA_PATH)


def _approved_for_period(period: str, flow: str) -> tuple[list[dict], dict[int, list[dict]], dict]:
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
        raise HTTPException(422, "Period must be YYYY-MM")
    if flow not in {"A", "D"}:
        raise HTTPException(422, "Flow must be A for arrivals or D for dispatches")
    invoices = rows("SELECT * FROM invoices WHERE organisation_id=? AND arrival_date LIKE ? AND flow=? AND status IN ('approved','exported') ORDER BY id", (ORG_ID, period + "%", flow))
    lines_by_invoice = {invoice["id"]: _lines(invoice["id"]) for invoice in invoices}
    profile = _profile()
    return invoices, lines_by_invoice, profile


@app.get("/api/declarations/{period}")
def declaration_preview(period: str, flow: str = "A"):
    invoices, lines_by_invoice, profile = _approved_for_period(period, flow)
    result_rows = declaration_rows(invoices, lines_by_invoice, profile, period)
    return {
        "period": period, "flow": flow, "rows": result_rows, "invoice_count": len(invoices),
        "row_count": len(result_rows), "schema": inspect_schema(SCHEMA_PATH),
        "totals": {
            "invoice_value": sum(int(item["INVOICE_VALUE"]) for item in result_rows),
            "statistical_value": sum(int(item["STAT_VALUE"]) for item in result_rows),
            "net_mass": sum(int(item["NET_MASS"]) for item in result_rows),
        },
    }


def _save_export(period: str, flow: str, fmt: str, data: bytes, extension: str, result_rows: list[dict]) -> tuple[Path, str]:
    digest = sha256(data)
    direction = "arrivals" if flow == "A" else "dispatches"
    filename = f"intrastat-{period}-{direction}-{digest[:8]}.{extension}"
    path = EXPORT_DIR / filename
    if not path.exists():
        path.write_bytes(data)
    invoice_ids = sorted({item["_invoice_id"] for item in result_rows})
    with transaction() as connection:
        connection.execute(
            """INSERT INTO export_snapshots(organisation_id,period,flow,format,filename,sha256,row_count,invoice_ids,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (ORG_ID, period, flow, fmt, filename, digest, len(result_rows), json.dumps(invoice_ids), utc_now()),
        )
        if invoice_ids:
            placeholders = ",".join("?" for _ in invoice_ids)
            connection.execute(f"UPDATE invoices SET status='exported',updated_at=? WHERE id IN ({placeholders})", (utc_now(), *invoice_ids))
        _record_event(connection, None, "declaration_exported", {"period": period, "format": fmt, "sha256": digest})
    return path, filename


@app.post("/api/declarations/{period}/csv")
def export_csv(period: str, flow: str = "A"):
    invoices, lines_by_invoice, profile = _approved_for_period(period, flow)
    result_rows = declaration_rows(invoices, lines_by_invoice, profile, period)
    if not result_rows:
        raise HTTPException(422, "There are no approved goods rows in this period")
    data = csv_bytes(result_rows)
    path, filename = _save_export(period, flow, "review_csv", data, "csv", result_rows)
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.post("/api/declarations/{period}/xml")
def export_xml(period: str, flow: str = "A"):
    status = inspect_schema(SCHEMA_PATH)
    if not status.get("valid"):
        raise HTTPException(409, status)
    invoices, lines_by_invoice, profile = _approved_for_period(period, flow)
    result_rows = declaration_rows(invoices, lines_by_invoice, profile, period)
    if not result_rows:
        raise HTTPException(422, "There are no approved goods rows in this period")
    try:
        data = xml_bytes(result_rows, SCHEMA_PATH)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    path, filename = _save_export(period, flow, "xml", data, "xml", result_rows)
    return FileResponse(path, media_type="application/xml", filename=filename)


@app.get("/api/exports")
def get_exports():
    return rows("SELECT * FROM export_snapshots WHERE organisation_id=? ORDER BY created_at DESC", (ORG_ID,))


@app.post("/api/exports/{export_id}/submitted")
async def mark_submitted(export_id: int, request: Request):
    body = await request.json()
    reference = str(body.get("declaration_reference", "")).strip()
    if not reference:
        raise HTTPException(422, "Enter the declaration or receipt reference")
    snapshot = row("SELECT * FROM export_snapshots WHERE id=? AND organisation_id=?", (export_id, ORG_ID))
    if not snapshot:
        raise HTTPException(404, "Export not found")
    invoice_ids = json.loads(snapshot["invoice_ids"])
    with transaction() as connection:
        connection.execute("UPDATE export_snapshots SET status='submitted',declaration_reference=? WHERE id=?", (reference, export_id))
        if invoice_ids:
            placeholders = ",".join("?" for _ in invoice_ids)
            connection.execute(f"UPDATE invoices SET status='submitted',updated_at=? WHERE id IN ({placeholders})", (utc_now(), *invoice_ids))
        _record_event(connection, None, "submission_recorded", {"export_id": export_id, "reference": reference})
    return {"saved": True}
