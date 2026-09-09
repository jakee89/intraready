from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("INTRASTAT_DATA_DIR", BASE_DIR / "data")).resolve()
DATABASE_PATH = DATA_DIR / "intrastat.sqlite3"
UPLOAD_DIR = DATA_DIR / "documents"
EXPORT_DIR = DATA_DIR / "exports"
SCHEMA_DIR = DATA_DIR / "schemas"

MAX_UPLOAD_BYTES = int(os.getenv("INTRASTAT_MAX_UPLOAD_MB", "20")) * 1024 * 1024
APP_PASSWORD = os.getenv("INTRASTAT_APP_PASSWORD", "")
APP_TITLE = os.getenv("INTRASTAT_APP_TITLE", "IntraReady")


def ensure_directories() -> None:
    for directory in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR, SCHEMA_DIR):
        directory.mkdir(parents=True, exist_ok=True)
