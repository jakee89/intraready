from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from .config import DATABASE_PATH, ensure_directories


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS organisations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'My organisation',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    email_normalized TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    platform_role TEXT NOT NULL DEFAULT '',
    email_verified_at TEXT NOT NULL DEFAULT '',
    must_change_password INTEGER NOT NULL DEFAULT 0,
    requested_organisation TEXT NOT NULL DEFAULT '',
    last_login_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organisation_memberships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'viewer',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    UNIQUE(organisation_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    active_organisation_id INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    ip_address TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_normalized TEXT NOT NULL,
    succeeded INTEGER NOT NULL DEFAULT 0,
    ip_address TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    organisation_id INTEGER REFERENCES organisations(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entitlements TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organisation_subscriptions (
    organisation_id INTEGER PRIMARY KEY REFERENCES organisations(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL DEFAULT 'inactive',
    provider_customer_id TEXT NOT NULL DEFAULT '',
    provider_subscription_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metered_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    amount INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(organisation_id, metric, source_id)
);

CREATE TABLE IF NOT EXISTS billing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_settings (
    organisation_id INTEGER PRIMARY KEY REFERENCES organisations(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'OpenAI',
    model TEXT NOT NULL DEFAULT 'gpt-5.6-terra',
    api_key_cipher TEXT NOT NULL DEFAULT '',
    api_key_last4 TEXT NOT NULL DEFAULT '',
    monthly_budget_eur TEXT NOT NULL DEFAULT '',
    input_eur_per_million TEXT NOT NULL DEFAULT '',
    output_eur_per_million TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_eur TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed',
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    organisation_id INTEGER PRIMARY KEY REFERENCES organisations(id),
    trader_vat TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    agent_vat TEXT NOT NULL DEFAULT '',
    trading_licence TEXT NOT NULL DEFAULT '',
    signatory TEXT NOT NULL DEFAULT '',
    id_card TEXT NOT NULL DEFAULT '',
    telephone TEXT NOT NULL DEFAULT '',
    locality TEXT NOT NULL DEFAULT '',
    default_flow TEXT NOT NULL DEFAULT 'A',
    default_mot TEXT NOT NULL DEFAULT '4',
    default_not TEXT NOT NULL DEFAULT '11',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    storage_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    extracted_text TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT 'native_text',
    created_at TEXT NOT NULL,
    UNIQUE(organisation_id, sha256)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    document_id INTEGER REFERENCES documents(id),
    supplier_name TEXT NOT NULL DEFAULT '',
    supplier_vat_country TEXT NOT NULL DEFAULT '',
    supplier_vat_number TEXT NOT NULL DEFAULT '',
    invoice_number TEXT NOT NULL DEFAULT '',
    invoice_date TEXT NOT NULL DEFAULT '',
    arrival_date TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'EUR',
    total_value TEXT NOT NULL DEFAULT '',
    consignment_country TEXT NOT NULL DEFAULT '',
    mode_transport TEXT NOT NULL DEFAULT '4',
    terms_delivery TEXT NOT NULL DEFAULT '',
    nature_transaction TEXT NOT NULL DEFAULT '11',
    layout_mapping TEXT NOT NULL DEFAULT '',
    flow TEXT NOT NULL DEFAULT 'A',
    status TEXT NOT NULL DEFAULT 'needs_review',
    notes TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    sku TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    quantity TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    raw_commodity_code TEXT NOT NULL DEFAULT '',
    hs_code TEXT NOT NULL DEFAULT '',
    origin_country TEXT NOT NULL DEFAULT '',
    consignment_country TEXT NOT NULL DEFAULT '',
    invoice_value TEXT NOT NULL DEFAULT '',
    statistical_value TEXT NOT NULL DEFAULT '',
    unit_net_mass TEXT NOT NULL DEFAULT '',
    net_mass TEXT NOT NULL DEFAULT '',
    net_mass_overridden INTEGER NOT NULL DEFAULT 0,
    supp_qty TEXT NOT NULL DEFAULT '',
    supp_unit TEXT NOT NULL DEFAULT '',
    special_quantity TEXT NOT NULL DEFAULT '',
    collector_type TEXT NOT NULL DEFAULT '',
    range_value TEXT NOT NULL DEFAULT '',
    line_kind TEXT NOT NULL DEFAULT 'goods',
    linked_line_id INTEGER REFERENCES invoice_lines(id) ON DELETE SET NULL,
    reviewed INTEGER NOT NULL DEFAULT 0,
    source_page INTEGER,
    confidence TEXT NOT NULL DEFAULT 'extracted',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(invoice_id, position)
);

CREATE TABLE IF NOT EXISTS product_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    supplier_vat TEXT NOT NULL DEFAULT '',
    supplier_name TEXT NOT NULL DEFAULT '',
    sku TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    hs_code TEXT NOT NULL DEFAULT '',
    origin_country TEXT NOT NULL DEFAULT '',
    unit_net_mass TEXT NOT NULL DEFAULT '',
    supp_unit TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    effective_from TEXT NOT NULL DEFAULT '',
    verified_at TEXT NOT NULL,
    UNIQUE(organisation_id, supplier_vat, sku)
);

CREATE TABLE IF NOT EXISTS supplier_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    supplier_vat TEXT NOT NULL DEFAULT '',
    supplier_name TEXT NOT NULL DEFAULT '',
    flow TEXT NOT NULL DEFAULT 'A',
    currency TEXT NOT NULL DEFAULT 'EUR',
    consignment_country TEXT NOT NULL DEFAULT '',
    mode_transport TEXT NOT NULL DEFAULT '4',
    terms_delivery TEXT NOT NULL DEFAULT '',
    nature_transaction TEXT NOT NULL DEFAULT '11',
    layout_mapping TEXT NOT NULL DEFAULT '',
    layout_fingerprint TEXT NOT NULL DEFAULT '',
    layout_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organisation_id, supplier_vat)
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_template_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    supplier_vat TEXT NOT NULL,
    version INTEGER NOT NULL,
    layout_fingerprint TEXT NOT NULL,
    layout_mapping TEXT NOT NULL,
    source_invoice_id INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(organisation_id, supplier_vat, version)
);

CREATE TABLE IF NOT EXISTS review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    invoice_id INTEGER REFERENCES invoices(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisations(id),
    period TEXT NOT NULL,
    flow TEXT NOT NULL,
    format TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    invoice_ids TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'exported',
    declaration_reference TEXT NOT NULL DEFAULT '',
    receipt_filename TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_invoices_arrival ON invoices(organisation_id, arrival_date);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(organisation_id, status);
CREATE INDEX IF NOT EXISTS idx_lines_invoice ON invoice_lines(invoice_id, position);
CREATE INDEX IF NOT EXISTS idx_products_lookup ON product_facts(organisation_id, supplier_vat, sku);
CREATE INDEX IF NOT EXISTS idx_supplier_profiles ON supplier_profiles(organisation_id, supplier_vat);
CREATE INDEX IF NOT EXISTS idx_supplier_templates ON supplier_template_versions(organisation_id, supplier_vat, version);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token_hash, expires_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(email_normalized, created_at);
CREATE INDEX IF NOT EXISTS idx_security_events ON security_events(created_at, action);
CREATE INDEX IF NOT EXISTS idx_ai_usage_period ON ai_usage_events(organisation_id, created_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    ensure_directories()
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 15000")
    return connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with transaction() as connection:
        connection.executescript(SCHEMA)
        existing_line_columns = {item[1] for item in connection.execute("PRAGMA table_info(invoice_lines)")}
        text_columns = ("special_quantity", "collector_type", "range_value", "unit_net_mass")
        for column in text_columns:
            if column not in existing_line_columns:
                connection.execute(f"ALTER TABLE invoice_lines ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if "net_mass_overridden" not in existing_line_columns:
            connection.execute("ALTER TABLE invoice_lines ADD COLUMN net_mass_overridden INTEGER NOT NULL DEFAULT 0")
        if "consignment_country" not in existing_line_columns:
            connection.execute("ALTER TABLE invoice_lines ADD COLUMN consignment_country TEXT NOT NULL DEFAULT ''")
            connection.execute("UPDATE invoice_lines SET consignment_country=COALESCE((SELECT consignment_country FROM invoices WHERE invoices.id=invoice_lines.invoice_id),'')")
        supplier_columns = {item[1] for item in connection.execute("PRAGMA table_info(supplier_profiles)")}
        if "layout_mapping" not in supplier_columns:
            connection.execute("ALTER TABLE supplier_profiles ADD COLUMN layout_mapping TEXT NOT NULL DEFAULT ''")
        if "layout_fingerprint" not in supplier_columns:
            connection.execute("ALTER TABLE supplier_profiles ADD COLUMN layout_fingerprint TEXT NOT NULL DEFAULT ''")
        if "layout_version" not in supplier_columns:
            connection.execute("ALTER TABLE supplier_profiles ADD COLUMN layout_version INTEGER NOT NULL DEFAULT 0")
        user_columns = {item[1] for item in connection.execute("PRAGMA table_info(users)")}
        if "requested_organisation" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN requested_organisation TEXT NOT NULL DEFAULT ''")
        login_columns = {item[1] for item in connection.execute("PRAGMA table_info(login_attempts)")}
        if "ip_address" not in login_columns:
            connection.execute("ALTER TABLE login_attempts ADD COLUMN ip_address TEXT NOT NULL DEFAULT ''")
        now = utc_now()
        connection.execute(
            "INSERT OR IGNORE INTO organisations(id, name, created_at) VALUES(1, ?, ?)",
            ("My organisation", now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO profiles(organisation_id, updated_at) VALUES(1, ?)",
            (now,),
        )
        connection.execute("INSERT OR IGNORE INTO platform_settings(key,value,updated_at) VALUES('registration_mode','closed',?)", (now,))
        connection.execute("INSERT OR IGNORE INTO platform_settings(key,value,updated_at) VALUES('billing_enabled','false',?)", (now,))
        connection.execute(
            "INSERT OR IGNORE INTO plans(id,name,entitlements,active,created_at) VALUES('internal','Internal',?,1,?)",
            ('{"organisations":1,"seats":10,"invoices_per_month":10000,"ai_budget":0}', now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO organisation_subscriptions(organisation_id,plan_id,status,updated_at) VALUES(1,'internal','inactive',?)",
            (now,),
        )
        connection.execute("INSERT OR IGNORE INTO ai_settings(organisation_id,updated_at) VALUES(1,?)", (now,))
        connection.execute("INSERT OR IGNORE INTO ai_settings(organisation_id,updated_at) SELECT id,? FROM organisations", (now,))


def rows(query: str, params: tuple = ()) -> list[dict]:
    connection = connect()
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def row(query: str, params: tuple = ()) -> dict | None:
    connection = connect()
    try:
        found = connection.execute(query, params).fetchone()
        return dict(found) if found else None
    finally:
        connection.close()
