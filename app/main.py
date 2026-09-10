from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
import io
import time
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import pdfplumber

from .config import APP_TITLE, AUTH_COOKIE_SECURE, BASE_DIR, BOOTSTRAP_TOKEN, EXPORT_DIR, MAX_UPLOAD_BYTES, SCHEMA_DIR, UPLOAD_DIR, ensure_directories
from .auth import (
    SESSION_COOKIE, audit, authenticate, create_session, current_context, first_membership,
    current_organisation_id, hash_password, is_platform_admin, load_session,
    login_blocked, reset_context, revoke_session, set_context, users_exist, verify_password,
)
from .db import connect, init_db, row, rows, transaction, utc_now
from .exporter import csv_bytes, declaration_rows, inspect_schema, sha256, xml_bytes
from .extractors import extract_invoice, layout_fingerprint, preview_saved_mapping
from .cn_reference import cn_requirement, search_cn
from .cloud_ai import ai_status, rank_cn_candidates
from .rules import invoice_issues, readiness
from .billing import subscription_status
from .ai_control import get_ai_config, remove_saved_key, save_ai_config, usage_summary


TEMPLATE_PATH = BASE_DIR / "app" / "templates" / "index.html"
LOGIN_TEMPLATE_PATH = BASE_DIR / "app" / "templates" / "login.html"
SCHEMA_PATH = SCHEMA_DIR / "malta-intrastat.xsd"


def _org_id() -> int:
    return current_organisation_id()

INVOICE_FIELDS = {
    "supplier_name", "supplier_vat_country", "supplier_vat_number", "invoice_number",
    "invoice_date", "arrival_date", "currency", "total_value", "consignment_country",
    "mode_transport", "terms_delivery", "nature_transaction", "flow", "notes",
}
LINE_FIELDS = {
    "sku", "description", "quantity", "unit", "raw_commodity_code", "hs_code",
    "origin_country", "consignment_country", "invoice_value", "statistical_value", "unit_net_mass", "net_mass", "net_mass_overridden", "supp_qty",
    "supp_unit", "special_quantity", "collector_type", "range_value", "line_kind",
    "linked_line_id", "reviewed", "source_page", "confidence", "notes",
}
PROFILE_FIELDS = {
    "trader_vat", "email", "agent_vat", "trading_licence", "signatory", "id_card",
    "telephone", "locality", "default_flow", "default_mot", "default_not",
}
UPPER_FIELDS = {
    "supplier_vat_country", "currency", "consignment_country", "terms_delivery", "flow",
    "unit", "hs_code", "origin_country", "consignment_country", "supp_unit", "default_flow",
}
DECIMAL_FIELDS = {"total_value", "quantity", "invoice_value", "statistical_value", "unit_net_mass", "net_mass", "supp_qty", "special_quantity"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_directories()
    init_db()
    yield


app = FastAPI(title=APP_TITLE, version="0.10.0", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")


@app.get("/api/ai/status")
def cloud_ai_status():
    return ai_status(False)


@app.post("/api/ai/test")
def test_cloud_ai():
    return ai_status(True)


@app.get("/api/ai/settings")
def get_cloud_ai_settings():
    return {"config": get_ai_config(False), "usage": usage_summary(), "status": ai_status(False)}


@app.patch("/api/ai/settings")
def update_cloud_ai_settings(body: dict):
    try:
        result = save_ai_config(body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    audit("ai.settings_updated", details=f"model={result['model']};key_source={result['key_source']}")
    return result


@app.delete("/api/ai/settings/key")
def delete_cloud_ai_key():
    result = remove_saved_key()
    audit("ai.key_removed")
    return result


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    public = path.startswith("/static/") or path in {
        "/login", "/api/health", "/api/auth/status", "/api/auth/login",
        "/api/auth/setup-owner", "/api/auth/register",
    }
    raw_session = request.cookies.get(SESSION_COOKIE, "")
    auth_context = load_session(raw_session)
    context_token = set_context(auth_context)
    try:
        if auth_context and auth_context.get("must_change_password") and path not in {"/login", "/api/auth/status", "/api/auth/change-password", "/api/auth/logout"} and not path.startswith("/static/"):
            if path.startswith("/api/"):
                response = JSONResponse({"detail": "Change the temporary password before continuing", "code": "PASSWORD_CHANGE_REQUIRED"}, status_code=403)
            else:
                response = RedirectResponse("/login", status_code=303)
        elif not public and not auth_context:
            if path.startswith("/api/"):
                response = JSONResponse({"detail": "Sign in required", "code": "AUTH_REQUIRED"}, status_code=401)
            else:
                response = RedirectResponse("/login", status_code=303)
        elif request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/api/") and auth_context:
            role = auth_context.get("organisation_role", "viewer")
            if role == "viewer" and not path.startswith("/api/auth/"):
                response = JSONResponse({"detail": "Your role is read-only", "code": "PERMISSION_DENIED"}, status_code=403)
            elif (path in {"/api/profile", "/api/schema"} or path.startswith("/api/ai/settings")) and role not in {"owner", "administrator"}:
                response = JSONResponse({"detail": "Organisation administrator access required", "code": "PERMISSION_DENIED"}, status_code=403)
            else:
                supplied = request.headers.get("x-csrf-token", "")
                if not supplied or not secrets.compare_digest(supplied, auth_context["csrf_token"]):
                    response = JSONResponse({"detail": "Your session check failed. Refresh and try again.", "code": "CSRF_FAILED"}, status_code=403)
                else:
                    response = await call_next(request)
        else:
            response = await call_next(request)
    finally:
        reset_context(context_token)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-src 'self'; object-src 'self'; base-uri 'self'; form-action 'self'"
    )
    return response


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, raw_token, max_age=14 * 24 * 60 * 60, httponly=True,
        secure=AUTH_COOKIE_SECURE, samesite="lax", path="/",
    )


def _request_context(request: Request) -> tuple[str, str]:
    return request.headers.get("user-agent", ""), request.client.host if request.client else ""


def _admin_only(owner_only: bool = False) -> dict:
    context = current_context()
    permitted = context.get("platform_role") == "owner" if owner_only else is_platform_admin()
    if not permitted:
        raise HTTPException(403, "Platform administrator access required")
    return context


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return LOGIN_TEMPLATE_PATH.read_text(encoding="utf-8").replace("{{APP_TITLE}}", APP_TITLE)


@app.get("/api/auth/status")
def auth_status(request: Request):
    context = load_session(request.cookies.get(SESSION_COOKIE, ""))
    registration = row("SELECT value FROM platform_settings WHERE key='registration_mode'")
    return {
        "authenticated": bool(context), "setup_required": not users_exist(),
        "registration_mode": registration["value"] if registration else "closed",
        "user": {key: context.get(key) for key in ("email", "name", "platform_role", "organisation_role", "organisation_name", "must_change_password", "csrf_token")} if context else None,
    }


@app.post("/api/auth/setup-owner")
def setup_owner(request: Request, body: dict):
    if users_exist():
        raise HTTPException(409, "The platform owner already exists")
    if not BOOTSTRAP_TOKEN:
        raise HTTPException(503, "Set INTRASTAT_BOOTSTRAP_TOKEN in Portainer, redeploy, then create the owner account")
    if not secrets.compare_digest(str(body.get("setup_code", "")), BOOTSTRAP_TOKEN):
        raise HTTPException(403, "The setup code is incorrect")
    email = str(body.get("email", "")).strip().casefold()
    name = str(body.get("name", "")).strip()
    organisation = str(body.get("organisation", "")).strip() or "My organisation"
    if not name or "@" not in email:
        raise HTTPException(422, "Enter your name and a valid email address")
    try:
        password_hash = hash_password(str(body.get("password", "")))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    now = utc_now()
    with transaction() as connection:
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "The platform owner already exists")
        connection.execute("UPDATE organisations SET name=? WHERE id=1", (organisation,))
        cursor = connection.execute(
            """INSERT INTO users(email,email_normalized,name,password_hash,status,platform_role,email_verified_at,created_at,updated_at)
               VALUES(?,?,?,?, 'active','owner',?,?,?)""",
            (email, email, name, password_hash, now, now, now),
        )
        user_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO organisation_memberships(organisation_id,user_id,role,status,created_at) VALUES(1,?,'owner','active',?)",
            (user_id, now),
        )
    raw, _ = create_session(user_id, 1, *_request_context(request))
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, raw)
    return response


@app.post("/api/auth/login")
def login(request: Request, body: dict):
    email = str(body.get("email", "")).strip().casefold()
    _, ip_address = _request_context(request)
    if login_blocked(email, ip_address):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes and try again.")
    user = authenticate(email, str(body.get("password", "")), ip_address)
    membership = first_membership(user["id"]) if user else None
    if not user or not membership:
        raise HTTPException(401, "Email or password is incorrect")
    raw, _ = create_session(user["id"], membership["organisation_id"], *_request_context(request))
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, raw)
    return response


@app.post("/api/auth/register")
def register_account(request: Request, body: dict):
    setting = row("SELECT value FROM platform_settings WHERE key='registration_mode'")
    mode = setting["value"] if setting else "closed"
    if mode not in {"approval_required", "open"}:
        raise HTTPException(403, "Registration is currently closed")
    email = str(body.get("email", "")).strip().casefold()
    _, ip_address = _request_context(request)
    if login_blocked(email, ip_address):
        raise HTTPException(429, "Too many requests. Wait 15 minutes and try again.")
    name = str(body.get("name", "")).strip()
    organisation = str(body.get("organisation", "")).strip()
    if not name or not organisation or "@" not in email:
        raise HTTPException(422, "Enter your name, organisation and a valid email")
    try:
        password_hash = hash_password(str(body.get("password", "")))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    now = utc_now()
    with transaction() as connection:
        connection.execute("INSERT INTO login_attempts(email_normalized,succeeded,ip_address,created_at) VALUES(?,0,?,?)", (email[:320], ip_address[:80], now))
    try:
        with transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO users(email,email_normalized,name,password_hash,status,requested_organisation,created_at,updated_at)
                   VALUES(?,?,?,?, 'pending',?,?,?)""",
                (email, email, name, password_hash, organisation, now, now),
            )
            connection.execute(
                "INSERT INTO security_events(actor_user_id,action,target_type,target_id,outcome,details,created_at) VALUES(NULL,'auth.registration_requested','user',?,'success',?,?)",
                (str(cursor.lastrowid), f"organisation={organisation}"[:500], now),
            )
    except Exception as error:
        if "UNIQUE" not in str(error).upper():
            raise
    return {"ok": True, "message": "Your request was received. The platform owner must approve it before sign-in."}


@app.post("/api/auth/logout")
def logout(request: Request):
    audit("auth.logout")
    revoke_session(request.cookies.get(SESSION_COOKIE, ""))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.post("/api/auth/change-password")
def change_password(request: Request, body: dict):
    context = current_context()
    user = row("SELECT password_hash FROM users WHERE id=?", (context["user_id"],))
    if not user or not verify_password(user["password_hash"], str(body.get("current_password", ""))):
        raise HTTPException(401, "Current password is incorrect")
    try:
        replacement = hash_password(str(body.get("new_password", "")))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    now = utc_now()
    current_token_hash = hashlib.sha256(request.cookies.get(SESSION_COOKIE, "").encode()).hexdigest()
    with transaction() as connection:
        connection.execute("UPDATE users SET password_hash=?,must_change_password=0,updated_at=? WHERE id=?", (replacement, now, context["user_id"]))
        connection.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND token_hash<>? AND revoked_at=''", (now, context["user_id"], current_token_hash))
    audit("auth.password_changed")
    return {"ok": True}


@app.get("/api/admin/overview")
def admin_overview():
    _admin_only()
    settings = {item["key"]: item["value"] for item in rows("SELECT key,value FROM platform_settings")}
    accounts = rows(
        """SELECT u.id,u.email,u.name,u.status,u.platform_role,u.requested_organisation,u.email_verified_at,u.last_login_at,u.created_at,
                  COUNT(DISTINCT m.organisation_id) AS organisation_count
           FROM users u LEFT JOIN organisation_memberships m ON m.user_id=u.id
           GROUP BY u.id ORDER BY u.created_at DESC"""
    )
    organisations = rows(
        """SELECT o.id,o.name,o.created_at,COUNT(DISTINCT m.user_id) AS seats,
                  COUNT(DISTINCT i.id) AS invoices
           FROM organisations o LEFT JOIN organisation_memberships m ON m.organisation_id=o.id
           LEFT JOIN invoices i ON i.organisation_id=o.id GROUP BY o.id ORDER BY o.created_at DESC"""
    )
    events = rows("SELECT action,outcome,target_type,target_id,created_at FROM security_events ORDER BY id DESC LIMIT 50")
    return {"accounts": accounts, "organisations": organisations, "settings": settings, "events": events, "billing": subscription_status(_org_id())}


@app.get("/api/billing/status")
def get_billing_status():
    return subscription_status(_org_id())


@app.post("/api/admin/accounts")
def admin_create_account(body: dict):
    context = _admin_only(True)
    email = str(body.get("email", "")).strip().casefold()
    name = str(body.get("name", "")).strip()
    role = str(body.get("role", "viewer"))
    if role not in {"owner", "administrator", "preparer", "reviewer", "viewer"} or not name or "@" not in email:
        raise HTTPException(422, "Enter a valid name, email and role")
    try:
        password_hash = hash_password(str(body.get("temporary_password", "")))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    now = utc_now()
    try:
        with transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO users(email,email_normalized,name,password_hash,status,email_verified_at,must_change_password,created_at,updated_at)
                   VALUES(?,?,?,?, 'active',?,1,?,?)""",
                (email, email, name, password_hash, now, now, now),
            )
            user_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO organisation_memberships(organisation_id,user_id,role,status,created_at) VALUES(?,?,?,'active',?)",
                (context["organisation_id"], user_id, role, now),
            )
    except Exception as error:
        if "UNIQUE" in str(error).upper():
            raise HTTPException(409, "An account with this email already exists") from error
        raise
    audit("admin.account_created", target_type="user", target_id=str(user_id), details=f"role={role}")
    return {"id": user_id, "email": email, "status": "active"}


@app.patch("/api/admin/settings")
def admin_settings(body: dict):
    _admin_only(True)
    allowed = {"registration_mode": {"closed", "approval_required"}}
    changed = {}
    with transaction() as connection:
        for key, choices in allowed.items():
            if key in body:
                value = str(body[key])
                if value not in choices:
                    raise HTTPException(422, f"Invalid {key}")
                connection.execute("UPDATE platform_settings SET value=?,updated_at=? WHERE key=?", (value, utc_now(), key))
                changed[key] = value
    audit("admin.settings_changed", details=json.dumps(changed))
    return changed


@app.post("/api/admin/accounts/{user_id}/activate")
def admin_activate_account(user_id: int):
    context = _admin_only(True)
    now = utc_now()
    with transaction() as connection:
        user = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404, "Account not found")
        if user["status"] != "pending":
            raise HTTPException(409, "Only pending accounts can be activated")
        cursor = connection.execute("INSERT INTO organisations(name,created_at) VALUES(?,?)", (user["requested_organisation"] or f"{user['name']}'s organisation", now))
        organisation_id = cursor.lastrowid
        connection.execute("INSERT INTO profiles(organisation_id,updated_at) VALUES(?,?)", (organisation_id, now))
        connection.execute(
            "INSERT INTO organisation_memberships(organisation_id,user_id,role,status,created_at) VALUES(?,?,'owner','active',?)",
            (organisation_id, user_id, now),
        )
        connection.execute("UPDATE users SET status='active',updated_at=? WHERE id=?", (now, user_id))
        connection.execute(
            "INSERT INTO organisation_subscriptions(organisation_id,plan_id,status,updated_at) VALUES(?,'internal','inactive',?)",
            (organisation_id, now),
        )
        connection.execute("INSERT INTO ai_settings(organisation_id,updated_at) VALUES(?,?)", (organisation_id, now))
    audit("admin.account_activated", target_type="user", target_id=str(user_id), details=f"actor={context['user_id']}")
    return {"ok": True, "organisation_id": organisation_id}


@app.patch("/api/admin/accounts/{user_id}/status")
def admin_account_status(user_id: int, body: dict):
    context = _admin_only(True)
    status = str(body.get("status", ""))
    if status not in {"active", "suspended"}:
        raise HTTPException(422, "Status must be active or suspended")
    if user_id == context["user_id"]:
        raise HTTPException(409, "You cannot suspend your own platform-owner account")
    with transaction() as connection:
        found = connection.execute("SELECT id,platform_role FROM users WHERE id=?", (user_id,)).fetchone()
        if not found:
            raise HTTPException(404, "Account not found")
        if found["platform_role"] == "owner":
            raise HTTPException(409, "The platform-owner account cannot be suspended here")
        connection.execute("UPDATE users SET status=?,updated_at=? WHERE id=?", (status, utc_now(), user_id))
        if status == "suspended":
            connection.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at=''", (utc_now(), user_id))
    audit(f"admin.account_{status}", target_type="user", target_id=str(user_id))
    return {"ok": True, "status": status}


def _profile() -> dict:
    return row("SELECT * FROM profiles WHERE organisation_id = ?", (_org_id(),)) or {}


def _invoice(invoice_id: int) -> dict:
    found = row("SELECT * FROM invoices WHERE id = ? AND organisation_id = ?", (invoice_id, _org_id()))
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
    for item in lines_list:
        item["cn_reference"] = cn_requirement(item.get("hs_code", ""))
    result["lines"] = lines_list
    result["document"] = document
    result["readiness"] = readiness(invoice, lines_list, _profile())
    result["preparation"] = _preparation_summary(lines_list)
    result["extraction_runs"] = rows("SELECT method,status,message,duration_ms,created_at FROM extraction_runs WHERE invoice_id=? ORDER BY id DESC LIMIT 5", (invoice_id,))
    return result


def _preparation_summary(lines_list: list[dict]) -> dict:
    goods = [line for line in lines_list if line.get("line_kind") == "goods"]
    output = []
    for line in goods:
        invoice_value = Decimal(str(line.get("invoice_value") or "0"))
        statistical_value = Decimal(str(line.get("statistical_value") or line.get("invoice_value") or "0"))
        included = []
        for charge in lines_list:
            if charge.get("linked_line_id") != line.get("id"):
                continue
            amount = Decimal(str(charge.get("invoice_value") or "0"))
            if charge.get("line_kind") == "charge_invoice":
                invoice_value += amount
                statistical_value += amount
                included.append(charge.get("description") or "Charge")
            elif charge.get("line_kind") == "charge_stat":
                statistical_value += amount
                included.append(charge.get("description") or "Freight")
        output.append({
            "line_id": line.get("id"), "sku": line.get("sku"), "description": line.get("description"),
            "invoice_value": format(invoice_value, "f"), "statistical_value": format(statistical_value, "f"),
            "net_mass": line.get("net_mass", ""), "consignment_country": line.get("consignment_country", ""), "included": included,
        })
    return {"rows": output, "prepared": bool(output) and all(not line.get("line_kind") == "charge" for line in lines_list)}


def _clean_value(field: str, value):
    if field in {"reviewed", "net_mass_overridden"}:
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
        (_org_id(), invoice_id, event_type, json.dumps(details) if isinstance(details, dict) else details, utc_now()),
    )


def _invalidate(connection, invoice_id: int, event_type: str, details: dict):
    connection.execute(
        "UPDATE invoices SET status = 'needs_review', revision = revision + 1, updated_at = ? WHERE id = ? AND organisation_id = ?",
        (utc_now(), invoice_id, _org_id()),
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
            (_org_id(), supplier_vat, line["sku"]),
        ).fetchone()
        if not fact:
            continue
        fact = dict(fact)
        updates = {}
        for field in ("hs_code", "origin_country", "supp_unit"):
            if not line[field] and fact[field]:
                updates[field] = fact[field]
        if fact["unit_net_mass"] and not line.get("net_mass_overridden"):
            updates["unit_net_mass"] = fact["unit_net_mass"]
        if fact["unit_net_mass"] and line["quantity"] and not line.get("net_mass_overridden"):
            updates["net_mass"] = format(Decimal(fact["unit_net_mass"]) * Decimal(line["quantity"]), "f")
            updates["net_mass_overridden"] = 0
        if updates:
            updates["confidence"] = "catalogue-suggested"
            assignments = ", ".join(f"{field} = ?" for field in updates)
            connection.execute(f"UPDATE invoice_lines SET {assignments}, updated_at = ? WHERE id = ?", (*updates.values(), utc_now(), line["id"]))


def _supplier_profiles() -> list[dict]:
    return rows("SELECT * FROM supplier_profiles WHERE organisation_id=?", (_org_id(),))


def _remember_supplier(connection, invoice: dict) -> None:
    supplier_vat = re.sub(r"[^A-Z0-9]", "", (invoice.get("supplier_vat_country", "") + invoice.get("supplier_vat_number", "")).upper())
    if len(supplier_vat) < 6 or not invoice.get("supplier_name"):
        return
    now = utc_now()
    connection.execute(
        """INSERT INTO supplier_profiles(organisation_id,supplier_vat,supplier_name,flow,currency,consignment_country,mode_transport,terms_delivery,nature_transaction,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(organisation_id,supplier_vat) DO UPDATE SET
        supplier_name=excluded.supplier_name,flow=excluded.flow,currency=excluded.currency,
        consignment_country=excluded.consignment_country,mode_transport=excluded.mode_transport,
        terms_delivery=excluded.terms_delivery,nature_transaction=excluded.nature_transaction,updated_at=excluded.updated_at""",
        (_org_id(), supplier_vat, invoice["supplier_name"], invoice["flow"], invoice["currency"], invoice["consignment_country"],
         invoice["mode_transport"], invoice["terms_delivery"], invoice["nature_transaction"], now, now),
    )


def _store_supplier_template(connection, invoice: dict, mapping: list[dict], fingerprint: str, source: str) -> int | None:
    supplier_vat = re.sub(r"[^A-Z0-9]", "", (invoice.get("supplier_vat_country", "") + invoice.get("supplier_vat_number", "")).upper())
    if len(supplier_vat) < 6 or not invoice.get("supplier_name") or not mapping:
        return None
    allowed = set(INVOICE_FIELDS) | {f"line_{field}" for field in ("sku", "description", "quantity", "unit", "hs_code", "origin_country", "consignment_country", "invoice_value", "unit_net_mass")}
    clean = []
    for region in mapping[:40]:
        if region.get("field") not in allowed:
            continue
        values = {key: max(0.0, min(1.0, float(region.get(key, 0)))) for key in ("x", "y", "width", "height")}
        if values["width"] >= .005 and values["height"] >= .005:
            clean.append({"field": region["field"], "page": max(1, int(region.get("page", 1))), **values})
    if not clean:
        return None
    _remember_supplier(connection, invoice)
    current = connection.execute("SELECT COALESCE(MAX(version),0) FROM supplier_template_versions WHERE organisation_id=? AND supplier_vat=?", (_org_id(), supplier_vat)).fetchone()[0]
    version = current + 1
    connection.execute("UPDATE supplier_template_versions SET active=0 WHERE organisation_id=? AND supplier_vat=?", (_org_id(), supplier_vat))
    connection.execute("INSERT INTO supplier_template_versions(organisation_id,supplier_vat,version,layout_fingerprint,layout_mapping,source_invoice_id,source,active,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (_org_id(), supplier_vat, version, fingerprint, json.dumps(clean), invoice.get("id"), source, 1, utc_now()))
    connection.execute("UPDATE supplier_profiles SET layout_mapping=?,layout_fingerprint=?,layout_version=?,updated_at=? WHERE organisation_id=? AND supplier_vat=?",
                       (json.dumps(clean), fingerprint, version, utc_now(), _org_id(), supplier_vat))
    return version


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
        WHERE i.organisation_id=? ORDER BY i.updated_at DESC""", (_org_id(),)
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
    automation = {
        "saved_layouts": row("SELECT COUNT(*) AS total FROM supplier_profiles WHERE organisation_id=? AND layout_version>0", (_org_id(),))["total"],
        "local_layout_runs": row("SELECT COUNT(*) AS total FROM extraction_runs WHERE organisation_id=? AND method='saved-layout'", (_org_id(),))["total"],
        "ai_runs": row("SELECT COUNT(*) AS total FROM ai_usage_events WHERE organisation_id=? AND operation='invoice_layout_learning'", (_org_id(),))["total"],
    }
    return {
        "app_title": APP_TITLE, "profile": profile, "invoices": invoice_list, "stats": stats,
        "schema": inspect_schema(SCHEMA_PATH), "catalogue_count": row("SELECT COUNT(*) total FROM product_facts WHERE organisation_id=?", (_org_id(),))["total"],
        "current_period": date.today().strftime("%Y-%m"), "ai_usage": usage_summary(), "automation": automation,
        "auth": {key: current_context().get(key) for key in (
            "user_id", "email", "name", "platform_role", "organisation_id", "organisation_name", "organisation_role", "csrf_token"
        )},
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
            (_org_id(), digest),
        )
        if existing:
            results.append({"filename": filename, "invoice_id": existing["id"], "duplicate": True})
            continue
        storage_name = f"{uuid.uuid4().hex}.pdf"
        destination = UPLOAD_DIR / storage_name
        destination.write_bytes(data)
        try:
            draft, extracted_text, verified_digest = extract_invoice(destination, filename, _supplier_profiles())
            if verified_digest != digest:
                raise ValueError("Stored file hash changed during extraction")
        except Exception as exc:
            destination.unlink(missing_ok=True)
            results.append({"filename": filename, "error": f"Could not read PDF: {exc}"})
            continue
        verified_layout = []
        for region in draft.get("suggested_layout", []):
            try:
                if _mapping_region_text({"storage_name": storage_name}, region):
                    verified_layout.append(region)
            except Exception:
                continue
        draft["suggested_layout"] = verified_layout
        now = utc_now()
        with transaction() as connection:
            document_id = connection.execute(
                """INSERT INTO documents(organisation_id,sha256,filename,storage_name,content_type,size_bytes,page_count,extracted_text,extraction_method,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (_org_id(), digest, filename, storage_name, "application/pdf", len(data), draft["page_count"], extracted_text, draft["adapter"], now),
            ).lastrowid
            invoice_columns = [field for field in INVOICE_FIELDS if field in draft]
            invoice_id = connection.execute(
                f"INSERT INTO invoices(organisation_id,document_id,{','.join(invoice_columns)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in invoice_columns)},?,?)",
                (_org_id(), document_id, *(draft[field] for field in invoice_columns), now, now),
            ).lastrowid
            positions_to_ids = {}
            pending_links = []
            for position, line in enumerate(draft["lines"], 1):
                if line.get("line_kind", "goods") == "goods" and not line.get("consignment_country"):
                    line["consignment_country"] = draft.get("consignment_country", "")
                requirement = cn_requirement(line.get("hs_code", ""))
                if requirement["supp_unit"] and not line.get("supp_unit"):
                    line["supp_unit"] = requirement["supp_unit"]
                if requirement["supp_unit"] in {"p/st", "pa"} and line.get("quantity") and not line.get("supp_qty"):
                    line["supp_qty"] = line["quantity"]
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
            draft["id"] = invoice_id
            template_version = _store_supplier_template(connection, draft, draft.get("suggested_layout", []), layout_fingerprint(destination), "api-ai")
            _suggest_from_catalogue(connection, invoice_id)
            meta = draft.get("ai_meta", {})
            if meta:
                connection.execute("INSERT INTO extraction_runs(organisation_id,invoice_id,method,status,message,duration_ms,created_at) VALUES(?,?,?,?,?,?,?)",
                                   (_org_id(), invoice_id, "api-ai", "completed" if draft.get("lines") else "failed",
                                    json.dumps({"request_id": meta.get("request_id", ""), "response_id": meta.get("response_id", ""), "error_code": meta.get("error_code", ""), "error": meta.get("error", ""), "usage": meta.get("usage", {})}),
                                    meta.get("duration_ms", 0), now))
            _record_event(connection, invoice_id, "invoice_uploaded", {"filename": filename, "adapter": draft["adapter"], "template_version": template_version})
        results.append({"filename": filename, "invoice_id": invoice_id, "duplicate": False, "adapter": draft["adapter"], "template_version": template_version})
    return {"results": results}


@app.get("/api/documents/{document_id}/file")
def document_file(document_id: int):
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (document_id, _org_id()))
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


@app.get("/api/documents/{document_id}/pages/{page_number}.png")
def document_page_image(document_id: int, page_number: int):
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (document_id, _org_id()))
    if not document:
        raise HTTPException(404, "Document not found")
    with pdfplumber.open(UPLOAD_DIR / document["storage_name"]) as pdf:
        if page_number < 1 or page_number > len(pdf.pages):
            raise HTTPException(404, "Page not found")
        image = pdf.pages[page_number - 1].to_image(resolution=150).original.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
    return Response(output.getvalue(), media_type="image/png")


@app.get("/api/invoices/{invoice_id}/supplier-mapping")
def get_supplier_mapping(invoice_id: int):
    invoice = _invoice(invoice_id)
    if not invoice.get("document_id"):
        raise HTTPException(422, "This invoice has no PDF.")
    supplier_vat = re.sub(r"[^A-Z0-9]", "", (invoice["supplier_vat_country"] + invoice["supplier_vat_number"]).upper())
    profile = row("SELECT * FROM supplier_profiles WHERE organisation_id=? AND supplier_vat=?", (_org_id(), supplier_vat)) if supplier_vat else None
    return {"document_id": invoice["document_id"], "supplier": invoice["supplier_name"], "supplier_vat": supplier_vat,
            "page_count": row("SELECT page_count FROM documents WHERE id=?", (invoice["document_id"],))["page_count"],
            "regions": json.loads(profile.get("layout_mapping") or "[]") if profile else []}


def _mapping_region_text(document: dict, region: dict) -> str:
    with pdfplumber.open(UPLOAD_DIR / document["storage_name"]) as pdf:
        page_number = max(1, int(region.get("page", 1)))
        if page_number > len(pdf.pages):
            raise HTTPException(422, "The selected page does not exist.")
        page = pdf.pages[page_number - 1]
        values = [max(0.0, min(1.0, float(region.get(key, 0)))) for key in ("x", "y", "width", "height")]
        x, y, width, height = values
        if width < .005 or height < .005:
            raise HTTPException(422, "The box is too small. Drag around the complete value.")
        box = (x * page.width, y * page.height, min(page.width, (x + width) * page.width), min(page.height, (y + height) * page.height))
        return (page.crop(box).extract_text(x_tolerance=2, y_tolerance=3) or "").strip().replace("\n", " ")


@app.post("/api/invoices/{invoice_id}/supplier-mapping/preview")
async def preview_supplier_mapping(invoice_id: int, request: Request):
    invoice = _invoice(invoice_id)
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (invoice.get("document_id"), _org_id()))
    if not document:
        raise HTTPException(422, "This invoice has no PDF.")
    region = (await request.json()).get("region", {})
    return {"text": _mapping_region_text(document, region)}


@app.post("/api/invoices/{invoice_id}/supplier-mapping/preview-all")
async def preview_all_supplier_mapping(invoice_id: int, request: Request):
    invoice = _invoice(invoice_id)
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (invoice.get("document_id"), _org_id()))
    if not document:
        raise HTTPException(422, "This invoice has no PDF.")
    regions = (await request.json()).get("regions", [])[:40]
    draft = preview_saved_mapping(UPLOAD_DIR / document["storage_name"], regions)
    return {"fields": {key: draft.get(key, "") for key in ("invoice_number", "invoice_date", "total_value", "supplier_name", "supplier_vat_number")},
            "lines": draft.get("lines", [])[:50]}


@app.put("/api/invoices/{invoice_id}/supplier-mapping")
async def save_supplier_mapping(invoice_id: int, request: Request):
    invoice = _invoice(invoice_id)
    supplier_vat = re.sub(r"[^A-Z0-9]", "", (invoice["supplier_vat_country"] + invoice["supplier_vat_number"]).upper())
    if len(supplier_vat) < 6 or not invoice.get("supplier_name"):
        raise HTTPException(422, "Enter the supplier name and VAT number before saving a map.")
    allowed = set(INVOICE_FIELDS) | {f"line_{field}" for field in ("sku", "description", "quantity", "unit", "hs_code", "origin_country", "consignment_country", "invoice_value", "unit_net_mass")}
    clean = []
    for region in (await request.json()).get("regions", [])[:40]:
        field = str(region.get("field", ""))
        if field not in allowed:
            continue
        values = {key: max(0.0, min(1.0, float(region.get(key, 0)))) for key in ("x", "y", "width", "height")}
        if values["width"] < .005 or values["height"] < .005:
            continue
        clean.append({"field": field, "page": max(1, int(region.get("page", 1))), **values})
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (invoice.get("document_id"), _org_id()))
    if not document:
        raise HTTPException(422, "This invoice has no PDF.")
    unreadable = [item["field"] for item in clean if not _mapping_region_text(document, item)]
    if unreadable:
        raise HTTPException(422, "These boxes contain no readable PDF text: " + ", ".join(unreadable) + ". Redraw them around the printed value.")
    fingerprint = layout_fingerprint(UPLOAD_DIR / document["storage_name"])
    with transaction() as connection:
        version = _store_supplier_template(connection, invoice, clean, fingerprint, "manual")
        _record_event(connection, invoice_id, "supplier_mapping_saved", {"regions": len(clean), "version": version})
    return {"saved": True, "regions": clean, "fingerprint": fingerprint, "version": version}


@app.get("/api/invoices/{invoice_id}/supplier-templates")
def supplier_template_history(invoice_id: int):
    invoice = _invoice(invoice_id)
    supplier_vat = re.sub(r"[^A-Z0-9]", "", (invoice["supplier_vat_country"] + invoice["supplier_vat_number"]).upper())
    return rows("SELECT id,version,layout_fingerprint,source,active,created_at FROM supplier_template_versions WHERE organisation_id=? AND supplier_vat=? ORDER BY version DESC", (_org_id(), supplier_vat))


@app.post("/api/invoices/{invoice_id}/supplier-templates/{template_id}/activate")
def activate_supplier_template(invoice_id: int, template_id: int):
    invoice = _invoice(invoice_id)
    supplier_vat = re.sub(r"[^A-Z0-9]", "", (invoice["supplier_vat_country"] + invoice["supplier_vat_number"]).upper())
    template = row("SELECT * FROM supplier_template_versions WHERE id=? AND organisation_id=? AND supplier_vat=?", (template_id, _org_id(), supplier_vat))
    if not template:
        raise HTTPException(404, "Supplier template version not found.")
    with transaction() as connection:
        connection.execute("UPDATE supplier_template_versions SET active=0 WHERE organisation_id=? AND supplier_vat=?", (_org_id(), supplier_vat))
        connection.execute("UPDATE supplier_template_versions SET active=1 WHERE id=?", (template_id,))
        connection.execute("UPDATE supplier_profiles SET layout_mapping=?,layout_fingerprint=?,layout_version=?,updated_at=? WHERE organisation_id=? AND supplier_vat=?",
                           (template["layout_mapping"], template["layout_fingerprint"], template["version"], utc_now(), _org_id(), supplier_vat))
        _record_event(connection, invoice_id, "supplier_template_rollback", {"version": template["version"]})
    return {"activated": True, "version": template["version"]}


@app.post("/api/invoices/{invoice_id}/extract-again")
def extract_again(invoice_id: int, use_ai: bool = False):
    started = time.monotonic()
    invoice = _invoice(invoice_id)
    if invoice["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked.")
    document = row("SELECT * FROM documents WHERE id=? AND organisation_id=?", (invoice.get("document_id"), _org_id()))
    if not document:
        raise HTTPException(422, "This manual invoice has no PDF to extract.")
    draft, extracted_text, _ = extract_invoice(UPLOAD_DIR / document["storage_name"], document["filename"], _supplier_profiles(), force_ai=use_ai)
    if not draft["lines"]:
        raise HTTPException(503, draft["notes"])
    extraction_fields = ("supplier_name", "supplier_vat_country", "supplier_vat_number", "invoice_number",
                         "invoice_date", "currency", "total_value", "consignment_country", "mode_transport", "terms_delivery")
    updates = {field: draft[field] for field in extraction_fields if draft.get(field)}
    updates["notes"] = draft["notes"]
    now = utc_now()
    with transaction() as connection:
        reviewed_lines = [item for item in _lines(invoice_id) if item["reviewed"]]
        reviewed_keys = {(item["sku"].strip().upper(), item["description"].strip().upper()) for item in reviewed_lines}
        connection.execute("DELETE FROM invoice_lines WHERE invoice_id=? AND reviewed=0", (invoice_id,))
        positions_to_ids = {}
        pending_links = []
        next_position = max((item["position"] for item in reviewed_lines), default=0)
        for draft_position, line in enumerate(draft["lines"], 1):
            if (line.get("sku", "").strip().upper(), line.get("description", "").strip().upper()) in reviewed_keys:
                continue
            next_position += 1
            position = next_position
            if line.get("line_kind", "goods") == "goods" and not line.get("consignment_country"):
                line["consignment_country"] = draft.get("consignment_country", "") or invoice.get("consignment_country", "")
            requirement = cn_requirement(line.get("hs_code", ""))
            if requirement["supp_unit"] and not line.get("supp_unit"):
                line["supp_unit"] = requirement["supp_unit"]
            if requirement["supp_unit"] in {"p/st", "pa"} and line.get("quantity") and not line.get("supp_qty"):
                line["supp_qty"] = line["quantity"]
            values = {field: line.get(field, "") for field in LINE_FIELDS if field != "linked_line_id"}
            values["reviewed"] = 0
            columns = list(values)
            line_id = connection.execute(
                f"INSERT INTO invoice_lines(invoice_id,position,{','.join(columns)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in columns)},?,?)",
                (invoice_id, position, *(values[column] for column in columns), now, now),
            ).lastrowid
            positions_to_ids[draft_position] = line_id
            if line.get("linked_position"):
                pending_links.append((line_id, line["linked_position"]))
        for line_id, linked_position in pending_links:
            connection.execute("UPDATE invoice_lines SET linked_line_id=? WHERE id=?", (positions_to_ids.get(linked_position), line_id))
        assignments = ", ".join(f"{field}=?" for field in updates)
        connection.execute(f"UPDATE invoices SET {assignments},status='needs_review',revision=revision+1,updated_at=? WHERE id=?", (*updates.values(), now, invoice_id))
        connection.execute("UPDATE documents SET extracted_text=?,extraction_method=? WHERE id=?", (extracted_text, draft["adapter"], document["id"]))
        template_invoice = {**invoice, **updates, "id": invoice_id}
        template_version = _store_supplier_template(connection, template_invoice, draft.get("suggested_layout", []), layout_fingerprint(UPLOAD_DIR / document["storage_name"]), "api-ai")
        _suggest_from_catalogue(connection, invoice_id)
        _record_event(connection, invoice_id, "invoice_reextracted", {"adapter": draft["adapter"], "template_version": template_version})
        meta = draft.get("ai_meta", {})
        connection.execute("INSERT INTO extraction_runs(organisation_id,invoice_id,method,status,message,duration_ms,created_at) VALUES(?,?,?,?,?,?,?)",
                           (_org_id(), invoice_id, draft["adapter"], "completed", json.dumps({"notes": draft["notes"][-700:], "request_id": meta.get("request_id", ""), "usage": meta.get("usage", {})}), round((time.monotonic()-started)*1000), now))
    return _payload(invoice_id)


@app.get("/api/invoices/{invoice_id}/extraction-runs")
def invoice_extraction_runs(invoice_id: int):
    _invoice(invoice_id)
    return rows("SELECT method,status,message,duration_ms,created_at FROM extraction_runs WHERE invoice_id=? ORDER BY id DESC LIMIT 20", (invoice_id,))


@app.post("/api/invoices")
def create_manual_invoice():
    profile = _profile()
    now = utc_now()
    with transaction() as connection:
        invoice_id = connection.execute(
            """INSERT INTO invoices(organisation_id,mode_transport,nature_transaction,flow,notes,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (_org_id(), profile.get("default_mot", "4"), profile.get("default_not", "11"), profile.get("default_flow", "A"), "Created manually.", now, now),
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
        connection.execute(f"UPDATE invoices SET {assignments} WHERE id=? AND organisation_id=?", (*updates.values(), invoice_id, _org_id()))
        if updates.get("consignment_country"):
            connection.execute("UPDATE invoice_lines SET consignment_country=?,updated_at=? WHERE invoice_id=? AND line_kind='goods' AND consignment_country=''",
                               (updates["consignment_country"], utc_now(), invoice_id))
        _invalidate(connection, invoice_id, "invoice_updated", {"fields": list(updates)})
    return _payload(invoice_id)


@app.post("/api/invoices/{invoice_id}/lines")
async def create_line(invoice_id: int, request: Request):
    current = _invoice(invoice_id)
    if current["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked. Prepare a separate amendment draft.")
    body = await request.json()
    values = {field: _clean_value(field, value) for field, value in body.items() if field in LINE_FIELDS}
    if values.get("line_kind", "goods") == "goods" and not values.get("consignment_country"):
        values["consignment_country"] = current.get("consignment_country", "")
    requirement = cn_requirement(values.get("hs_code", ""))
    if requirement["supp_unit"] and not values.get("supp_unit"):
        values["supp_unit"] = requirement["supp_unit"]
    if requirement["supp_unit"] in {"p/st", "pa"} and values.get("quantity") and not values.get("supp_qty"):
        values["supp_qty"] = values["quantity"]
    if values.get("unit_net_mass") and values.get("quantity"):
        calculated = format(Decimal(values["unit_net_mass"]) * Decimal(values["quantity"]), "f")
        if not values.get("net_mass"):
            values["net_mass"] = calculated
        values["net_mass_overridden"] = 1 if Decimal(values["net_mass"]) != Decimal(calculated) else 0
    elif values.get("net_mass"):
        values["net_mass_overridden"] = 1
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
    if not found or found["organisation_id"] != _org_id():
        raise HTTPException(404, "Line not found")
    if _invoice(found["invoice_id"])["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked. Prepare a separate amendment draft.")
    body = await request.json()
    updates = {field: _clean_value(field, value) for field, value in body.items() if field in LINE_FIELDS}
    if "hs_code" in updates:
        requirement = cn_requirement(updates["hs_code"])
        if requirement["supp_unit"] and not updates.get("supp_unit", found.get("supp_unit")):
            updates["supp_unit"] = requirement["supp_unit"]
        if requirement["supp_unit"] in {"p/st", "pa"} and updates.get("quantity", found.get("quantity")) and not updates.get("supp_qty", found.get("supp_qty")):
            updates["supp_qty"] = updates.get("quantity", found.get("quantity"))
    if "unit_net_mass" in updates:
        quantity = updates.get("quantity", found["quantity"])
        calculated = format(Decimal(updates["unit_net_mass"]) * Decimal(quantity), "f") if updates["unit_net_mass"] and quantity else ""
        supplied_total = updates.get("net_mass")
        updates["net_mass_overridden"] = 1 if supplied_total not in (None, "") and Decimal(supplied_total) != Decimal(calculated or "0") else 0
        if not updates["net_mass_overridden"]:
            updates["net_mass"] = calculated
    elif "quantity" in updates and found.get("unit_net_mass") and not found.get("net_mass_overridden"):
        updates["net_mass"] = format(Decimal(found["unit_net_mass"]) * Decimal(updates["quantity"]), "f") if updates["quantity"] else ""
    elif "net_mass" in updates:
        updates["net_mass_overridden"] = 1
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


@app.patch("/api/invoices/{invoice_id}/review-lines")
async def review_lines(invoice_id: int, request: Request):
    invoice = _invoice(invoice_id)
    if invoice["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked.")
    changes = (await request.json()).get("reviewed", [])
    with transaction() as connection:
        valid_ids = {item[0] for item in connection.execute("SELECT id FROM invoice_lines WHERE invoice_id=?", (invoice_id,))}
        for change in changes:
            line_id = int(change.get("id", 0))
            if line_id not in valid_ids:
                raise HTTPException(422, "A reviewed row does not belong to this invoice.")
            connection.execute("UPDATE invoice_lines SET reviewed=?,updated_at=? WHERE id=?", (1 if change.get("reviewed") else 0, utc_now(), line_id))
        if changes:
            _invalidate(connection, invoice_id, "lines_reviewed", {"count": len(changes)})
    return _payload(invoice_id)


@app.get("/api/lines/{line_id}/cn-suggestions")
def cn_suggestions(line_id: int):
    found = row("""SELECT l.*,i.supplier_name,i.supplier_vat_country,i.supplier_vat_number,i.organisation_id
                 FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.id=?""", (line_id,))
    if not found or found["organisation_id"] != _org_id():
        raise HTTPException(404, "Line not found")
    product_text = " ".join(filter(None, [found.get("description"), found.get("sku"), found.get("notes")]))
    candidates = search_cn(product_text, 18)
    remembered = rows("SELECT * FROM product_facts WHERE organisation_id=? AND sku=? AND hs_code<>''", (_org_id(), found.get("sku", ""))) if found.get("sku") else []
    by_code = {item["code"]: item for item in candidates}
    for fact in remembered:
        requirement = cn_requirement(fact["hs_code"])
        by_code[fact["hs_code"]] = {"code": fact["hs_code"], "description": fact["description"],
                                     "score": 1, "supp_unit": requirement["supp_unit"], "source": "Verified product memory"}
    candidates = sorted(by_code.values(), key=lambda item: item["score"], reverse=True)[:18]
    ai_ranking = rank_cn_candidates(product_text, candidates)
    if ai_ranking:
        details = {item["code"]: item for item in candidates}
        ranked = []
        for suggestion in ai_ranking:
            item = details.get(str(suggestion.get("code", "")))
            if item:
                ranked.append({**item, "confidence": max(0, min(100, int(suggestion.get("confidence", 0)))),
                               "reason": str(suggestion.get("reason", "")), "source": "API AI + " + item["source"]})
        if ranked:
            candidates = ranked
    return {"query": product_text, "suggestions": candidates[:5], "year": 2026}


@app.delete("/api/lines/{line_id}")
def delete_line(line_id: int):
    found = row("SELECT l.invoice_id,i.organisation_id FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.id=?", (line_id,))
    if not found or found["organisation_id"] != _org_id():
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
        connection.execute("DELETE FROM invoices WHERE id=? AND organisation_id=?", (invoice_id, _org_id()))
        if invoice.get("document_id"):
            connection.execute("DELETE FROM documents WHERE id=? AND organisation_id=?", (invoice["document_id"], _org_id()))
        _record_event(connection, None, "invoice_deleted", {"invoice_number": invoice["invoice_number"]})
    if document:
        (UPLOAD_DIR / document["storage_name"]).unlink(missing_ok=True)
    return {"deleted": True}


@app.post("/api/invoices/{invoice_id}/combine-equivalent")
def combine_equivalent_lines(invoice_id: int):
    invoice = _invoice(invoice_id)
    if invoice["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked.")
    invoice_lines = _lines(invoice_id)
    groups: dict[tuple, list[dict]] = {}
    for line in invoice_lines:
        if line["line_kind"] != "goods" or not line["hs_code"] or not line["origin_country"]:
            continue
        key = (line["hs_code"], line["origin_country"], line.get("consignment_country", ""), line["unit"], line["supp_unit"],
               line["collector_type"], line["range_value"])
        groups.setdefault(key, []).append(line)
    combined_groups = 0
    with transaction() as connection:
        for group in groups.values():
            if len(group) < 2:
                continue
            primary, duplicates = group[0], group[1:]
            def total(field: str) -> Decimal:
                return sum((Decimal(item[field]) for item in group if item.get(field)), Decimal("0"))
            quantity = total("quantity")
            net_mass = total("net_mass")
            skus = {item["sku"] for item in group if item["sku"]}
            details = "; ".join(
                f"{item['sku'] or 'no SKU'} — {item['description']} (qty {item['quantity'] or '?'}, page {item['source_page'] or '?'})"
                for item in group
            )
            summed = {
                "sku": next(iter(skus)) if len(skus) == 1 else "",
                "description": primary["description"] + f" (+{len(duplicates)} equivalent variant{'s' if len(duplicates) != 1 else ''})",
                "quantity": format(quantity, "f"),
                "invoice_value": format(total("invoice_value"), "f"),
                "statistical_value": format(total("statistical_value"), "f"),
                "net_mass": format(net_mass, "f"),
                "unit_net_mass": format(net_mass / quantity, "f") if quantity > 0 and net_mass > 0 else "",
                "net_mass_overridden": 0,
                "supp_qty": format(total("supp_qty"), "f") if any(item["supp_qty"] for item in group) else "",
                "special_quantity": format(total("special_quantity"), "f") if any(item["special_quantity"] for item in group) else "",
                "reviewed": 1 if all(item["reviewed"] for item in group) else 0,
                "confidence": "combined",
                "notes": (primary["notes"] + "\n" if primary["notes"] else "") + "Combined source rows: " + details,
            }
            assignments = ", ".join(f"{field}=?" for field in summed)
            connection.execute(f"UPDATE invoice_lines SET {assignments},updated_at=? WHERE id=?", (*summed.values(), utc_now(), primary["id"]))
            duplicate_ids = [item["id"] for item in duplicates]
            placeholders = ",".join("?" for _ in duplicate_ids)
            connection.execute(f"UPDATE invoice_lines SET linked_line_id=? WHERE linked_line_id IN ({placeholders})", (primary["id"], *duplicate_ids))
            connection.execute(f"DELETE FROM invoice_lines WHERE id IN ({placeholders})", duplicate_ids)
            combined_groups += 1
        if combined_groups:
            for position, existing in enumerate(connection.execute("SELECT id FROM invoice_lines WHERE invoice_id=? ORDER BY position", (invoice_id,)), 1):
                connection.execute("UPDATE invoice_lines SET position=? WHERE id=?", (position, existing[0]))
            _invalidate(connection, invoice_id, "equivalent_lines_combined", {"groups": combined_groups})
    result = _payload(invoice_id)
    result["combined_groups"] = combined_groups
    return result


@app.post("/api/invoices/{invoice_id}/prepare")
def prepare_invoice(invoice_id: int):
    invoice = _invoice(invoice_id)
    if invoice["status"] == "submitted":
        raise HTTPException(409, "Submitted invoices are locked.")
    invoice_lines = _lines(invoice_id)
    goods = [line for line in invoice_lines if line["line_kind"] == "goods" and line.get("hs_code")]
    if not goods:
        raise HTTPException(422, "No classified goods row is available yet. Use the AI reader or add the CN code first.")
    with transaction() as connection:
        for line in invoice_lines:
            description = (line.get("description") or "").lower()
            kind = line["line_kind"]
            if re.search(r"freight|transport|shipping|carriage|delivery cost", description):
                kind = "charge_stat"
            elif re.search(r"print|engraving|logo|personalisation|decoration|setup|set-up", description) and not line.get("hs_code"):
                kind = "charge_invoice"
            if kind.startswith("charge"):
                preceding = [item for item in goods if item["position"] < line["position"]]
                target = preceding[-1] if preceding else (goods[0] if len(goods) == 1 else None)
                if target:
                    connection.execute(
                        "UPDATE invoice_lines SET line_kind=?,linked_line_id=?,updated_at=? WHERE id=?",
                        (kind, target["id"], utc_now(), line["id"]),
                    )
            elif kind == "goods":
                requirement = cn_requirement(line.get("hs_code", ""))
                updates = {}
                if requirement["supp_unit"]:
                    updates["supp_unit"] = requirement["supp_unit"]
                if requirement["supp_unit"] in {"p/st", "pa"} and line.get("quantity"):
                    updates["supp_qty"] = line["quantity"]
                if updates:
                    assignments = ",".join(f"{field}=?" for field in updates)
                    connection.execute(f"UPDATE invoice_lines SET {assignments},updated_at=? WHERE id=?", (*updates.values(), utc_now(), line["id"]))
        _invalidate(connection, invoice_id, "invoice_prepared", {"method": "ai-assisted rules"})
    return _payload(invoice_id)


@app.post("/api/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id: int):
    invoice = _invoice(invoice_id)
    check = readiness(invoice, _lines(invoice_id), _profile())
    if not check["ready"]:
        raise HTTPException(422, {"message": "Resolve blocking issues before approval.", "issues": check["issues"]})
    with transaction() as connection:
        connection.execute("UPDATE invoices SET status='approved',updated_at=? WHERE id=?", (utc_now(), invoice_id))
        _remember_supplier(connection, invoice)
        _record_event(connection, invoice_id, "invoice_approved", {"revision": invoice["revision"]})
    return _payload(invoice_id)


@app.post("/api/invoices/{invoice_id}/remember-supplier")
def remember_supplier(invoice_id: int):
    invoice = _invoice(invoice_id)
    with transaction() as connection:
        _remember_supplier(connection, invoice)
        _record_event(connection, invoice_id, "supplier_defaults_remembered", {})
    return {"saved": True, "supplier": invoice["supplier_name"]}


@app.post("/api/invoices/{invoice_id}/reopen")
def reopen_invoice(invoice_id: int):
    _invoice(invoice_id)
    with transaction() as connection:
        connection.execute("UPDATE invoices SET status='needs_review',updated_at=? WHERE id=?", (utc_now(), invoice_id))
        _record_event(connection, invoice_id, "invoice_reopened", {})
    return _payload(invoice_id)


@app.get("/api/catalogue")
def get_catalogue():
    return rows("SELECT * FROM product_facts WHERE organisation_id=? ORDER BY supplier_name,sku", (_org_id(),))


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
            (_org_id(), values["supplier_vat"], values["supplier_name"], sku, values["description"], values["hs_code"],
             values["origin_country"], values["unit_net_mass"], values["supp_unit"], values["evidence"],
             values["effective_from"], utc_now()),
        )
    return get_catalogue()


@app.post("/api/catalogue/from-line/{line_id}")
def save_catalogue_from_line(line_id: int):
    found = row("""SELECT l.*,i.supplier_name,i.supplier_vat_country,i.supplier_vat_number,i.organisation_id
                   FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id WHERE l.id=?""", (line_id,))
    if not found or found["organisation_id"] != _org_id() or not found["sku"]:
        raise HTTPException(422, "This line needs a SKU before it can be remembered")
    unit_mass = found.get("unit_net_mass", "")
    try:
        if not unit_mass and Decimal(found["quantity"]) > 0 and Decimal(found["net_mass"]) > 0:
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
            (_org_id(), supplier_vat, found["supplier_name"], found["sku"], found["description"], found["hs_code"],
             found["origin_country"], unit_mass, found["supp_unit"], f"Verified from invoice {found['invoice_id']}", "", utc_now()),
        )
        _record_event(connection, found["invoice_id"], "product_remembered", {"line_id": line_id, "sku": found["sku"]})
    return {"saved": True, "unit_net_mass": unit_mass}


@app.delete("/api/catalogue/{fact_id}")
def delete_catalogue_fact(fact_id: int):
    with transaction() as connection:
        result = connection.execute("DELETE FROM product_facts WHERE id=? AND organisation_id=?", (fact_id, _org_id()))
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
        connection.execute(f"UPDATE profiles SET {assignments},updated_at=? WHERE organisation_id=?", (*updates.values(), utc_now(), _org_id()))
        connection.execute(
            "UPDATE invoices SET status='needs_review',updated_at=? WHERE organisation_id=? AND status IN ('approved','exported')",
            (utc_now(), _org_id()),
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
    invoices = rows("SELECT * FROM invoices WHERE organisation_id=? AND arrival_date LIKE ? AND flow=? AND status IN ('approved','exported') ORDER BY id", (_org_id(), period + "%", flow))
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
            (_org_id(), period, flow, fmt, filename, digest, len(result_rows), json.dumps(invoice_ids), utc_now()),
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
    return rows("SELECT * FROM export_snapshots WHERE organisation_id=? ORDER BY created_at DESC", (_org_id(),))


@app.post("/api/exports/{export_id}/submitted")
async def mark_submitted(export_id: int, request: Request):
    body = await request.json()
    reference = str(body.get("declaration_reference", "")).strip()
    if not reference:
        raise HTTPException(422, "Enter the declaration or receipt reference")
    snapshot = row("SELECT * FROM export_snapshots WHERE id=? AND organisation_id=?", (export_id, _org_id()))
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
