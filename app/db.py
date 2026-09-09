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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organisation_id, supplier_vat)
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
        now = utc_now()
        connection.execute(
            "INSERT OR IGNORE INTO organisations(id, name, created_at) VALUES(1, ?, ?)",
            ("My organisation", now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO profiles(organisation_id, updated_at) VALUES(1, ?)",
            (now,),
        )


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
