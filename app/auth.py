from __future__ import annotations

import hashlib
import secrets
from contextvars import ContextVar, Token
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .db import row, transaction, utc_now


SESSION_COOKIE = "intraready_session"
SESSION_DAYS = 14
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_dummy_hash = _hasher.hash(secrets.token_urlsafe(32))
_context: ContextVar[dict | None] = ContextVar("auth_context", default=None)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Use a password between 12 and 128 characters")
    return _hasher.hash(password)


def verify_password(stored: str, supplied: str) -> bool:
    try:
        return _hasher.verify(stored, supplied)
    except (VerifyMismatchError, InvalidHashError):
        return False


def set_context(value: dict | None) -> Token:
    return _context.set(value)


def reset_context(token: Token) -> None:
    _context.reset(token)


def current_context() -> dict:
    value = _context.get()
    if not value:
        raise RuntimeError("Authentication context is unavailable")
    return value


def current_organisation_id() -> int:
    return int(current_context()["organisation_id"])


def current_user_id() -> int:
    return int(current_context()["user_id"])


def users_exist() -> bool:
    found = row("SELECT COUNT(*) AS total FROM users")
    return bool(found and found["total"])


def load_session(raw_token: str) -> dict | None:
    if not raw_token:
        return None
    now = utc_now()
    found = row(
        """SELECT s.id AS session_id,s.user_id,s.active_organisation_id AS organisation_id,
                  s.csrf_token,s.expires_at,s.last_seen_at,u.email,u.name,u.platform_role,u.status,u.must_change_password,
                  m.role AS organisation_role,o.name AS organisation_name
           FROM user_sessions s
           JOIN users u ON u.id=s.user_id
           JOIN organisations o ON o.id=s.active_organisation_id
           JOIN organisation_memberships m ON m.user_id=u.id AND m.organisation_id=o.id
           WHERE s.token_hash=? AND s.revoked_at='' AND s.expires_at>? AND u.status='active'""",
        (token_hash(raw_token), now),
    )
    if not found:
        return None
    last_seen = datetime.fromisoformat(found["last_seen_at"])
    if datetime.now(timezone.utc) - last_seen > timedelta(minutes=5):
        with transaction() as connection:
            connection.execute("UPDATE user_sessions SET last_seen_at=? WHERE id=?", (now, found["session_id"]))
    return found


def create_session(user_id: int, organisation_id: int, user_agent: str = "", ip_address: str = "") -> tuple[str, dict]:
    raw = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = (now + timedelta(days=SESSION_DAYS)).isoformat()
    with transaction() as connection:
        connection.execute(
            """INSERT INTO user_sessions(user_id,active_organisation_id,token_hash,csrf_token,user_agent,ip_address,
               created_at,last_seen_at,expires_at,revoked_at) VALUES(?,?,?,?,?,?,?,?,?,'')""",
            (user_id, organisation_id, token_hash(raw), csrf, user_agent[:300], ip_address[:80], now.isoformat(), now.isoformat(), expires),
        )
        connection.execute("UPDATE users SET last_login_at=? WHERE id=?", (now.isoformat(), user_id))
    context = load_session(raw)
    if not context:
        raise RuntimeError("Could not create session")
    return raw, context


def revoke_session(raw_token: str) -> None:
    if not raw_token:
        return
    with transaction() as connection:
        connection.execute("UPDATE user_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at=''", (utc_now(), token_hash(raw_token)))


def authenticate(email: str, password: str, ip_address: str = "") -> dict | None:
    email = email.strip().casefold()
    found = row("SELECT * FROM users WHERE email_normalized=? AND status='active'", (email,))
    valid = verify_password(found["password_hash"] if found else _dummy_hash, password)
    with transaction() as connection:
        connection.execute(
            "INSERT INTO login_attempts(email_normalized,succeeded,ip_address,created_at) VALUES(?,?,?,?)",
            (email[:320], 1 if valid and found else 0, ip_address[:80], utc_now()),
        )
    return found if valid and found else None


def login_blocked(email: str, ip_address: str = "") -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).replace(microsecond=0).isoformat()
    found = row("""SELECT COUNT(*) AS total FROM login_attempts
                   WHERE succeeded=0 AND created_at>=? AND (email_normalized=? OR (ip_address<>'' AND ip_address=?))""",
                (cutoff, email.strip().casefold(), ip_address[:80]))
    return bool(found and found["total"] >= 8)


def first_membership(user_id: int) -> dict | None:
    return row(
        """SELECT m.organisation_id,m.role,o.name FROM organisation_memberships m
           JOIN organisations o ON o.id=m.organisation_id
           WHERE m.user_id=? AND m.status='active' ORDER BY m.created_at LIMIT 1""",
        (user_id,),
    )


def audit(action: str, outcome: str = "success", target_type: str = "", target_id: str = "", details: str = "") -> None:
    context = _context.get() or {}
    with transaction() as connection:
        connection.execute(
            """INSERT INTO security_events(actor_user_id,organisation_id,action,target_type,target_id,outcome,details,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (context.get("user_id"), context.get("organisation_id"), action, target_type, str(target_id), outcome, details[:500], utc_now()),
        )


def is_platform_admin() -> bool:
    return current_context().get("platform_role") in {"owner", "support", "auditor"}


def require_platform_owner() -> None:
    if current_context().get("platform_role") != "owner":
        raise PermissionError("Platform owner access required")
