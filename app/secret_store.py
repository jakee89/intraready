from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import DATA_DIR, ensure_directories


KEY_PATH = DATA_DIR / ".intraready-secret-key"


def _fernet() -> Fernet:
    ensure_directories()
    if not KEY_PATH.exists():
        temporary = Path(str(KEY_PATH) + ".tmp")
        temporary.write_bytes(Fernet.generate_key())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        try:
            temporary.replace(KEY_PATH)
        except OSError:
            temporary.unlink(missing_ok=True)
    return Fernet(KEY_PATH.read_bytes())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii") if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise RuntimeError("The saved secret cannot be decrypted with this installation's key") from error
