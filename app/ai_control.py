from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from .auth import current_organisation_id
from .config import OPENAI_API_KEY, OPENAI_MODEL
from .db import row, rows, transaction, utc_now
from .secret_store import decrypt_secret, encrypt_secret


def get_ai_config(include_secret: bool = False) -> dict:
    organisation_id = current_organisation_id()
    saved = row("SELECT * FROM ai_settings WHERE organisation_id=?", (organisation_id,)) or {}
    cipher = saved.get("api_key_cipher", "")
    config = {
        "provider": saved.get("provider") or "OpenAI",
        "model": saved.get("model") or OPENAI_MODEL,
        "key_source": "app" if cipher else ("environment" if OPENAI_API_KEY else "none"),
        "key_last4": saved.get("api_key_last4") or (OPENAI_API_KEY[-4:] if OPENAI_API_KEY else ""),
        "monthly_budget_eur": saved.get("monthly_budget_eur", ""),
        "input_eur_per_million": saved.get("input_eur_per_million", ""),
        "output_eur_per_million": saved.get("output_eur_per_million", ""),
    }
    if include_secret:
        config["api_key"] = decrypt_secret(cipher) if cipher else OPENAI_API_KEY
    return config


def save_ai_config(values: dict) -> dict:
    organisation_id = current_organisation_id()
    current = row("SELECT * FROM ai_settings WHERE organisation_id=?", (organisation_id,)) or {}
    model = str(values.get("model", current.get("model") or OPENAI_MODEL)).strip()
    if not model or len(model) > 100:
        raise ValueError("Enter a valid model name")
    numbers = {}
    for field in ("monthly_budget_eur", "input_eur_per_million", "output_eur_per_million"):
        raw = str(values.get(field, current.get(field, ""))).strip()
        if raw:
            try:
                number = Decimal(raw)
                if not number.is_finite() or number < 0:
                    raise InvalidOperation
                raw = format(number, "f")
            except InvalidOperation as error:
                raise ValueError(f"{field} must be a positive decimal") from error
        numbers[field] = raw
    key = str(values.get("api_key", "")).strip()
    cipher = current.get("api_key_cipher", "")
    last4 = current.get("api_key_last4", "")
    if key:
        if not key.startswith("sk-") or len(key) < 20:
            raise ValueError("The API key does not look valid")
        cipher, last4 = encrypt_secret(key), key[-4:]
    with transaction() as connection:
        connection.execute(
            """INSERT INTO ai_settings(organisation_id,provider,model,api_key_cipher,api_key_last4,monthly_budget_eur,input_eur_per_million,output_eur_per_million,updated_at)
               VALUES(?,'OpenAI',?,?,?,?,?,?,?) ON CONFLICT(organisation_id) DO UPDATE SET
               model=excluded.model,api_key_cipher=excluded.api_key_cipher,api_key_last4=excluded.api_key_last4,
               monthly_budget_eur=excluded.monthly_budget_eur,input_eur_per_million=excluded.input_eur_per_million,
               output_eur_per_million=excluded.output_eur_per_million,updated_at=excluded.updated_at""",
            (organisation_id, model, cipher, last4, numbers["monthly_budget_eur"], numbers["input_eur_per_million"], numbers["output_eur_per_million"], utc_now()),
        )
    return get_ai_config(False)


def remove_saved_key() -> dict:
    with transaction() as connection:
        connection.execute("UPDATE ai_settings SET api_key_cipher='',api_key_last4='',updated_at=? WHERE organisation_id=?", (utc_now(), current_organisation_id()))
    return get_ai_config(False)


def usage_summary() -> dict:
    organisation_id = current_organisation_id()
    period = date.today().strftime("%Y-%m")
    totals = row(
        """SELECT COALESCE(SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END),0) AS calls,
                  COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) AS failed_attempts,
                  COALESCE(SUM(input_tokens),0) AS input_tokens,
                  COALESCE(SUM(output_tokens),0) AS output_tokens,COALESCE(SUM(total_tokens),0) AS total_tokens,
                  COALESCE(SUM(CAST(NULLIF(estimated_cost_eur,'') AS REAL)),0) AS estimated_cost_eur
           FROM ai_usage_events WHERE organisation_id=? AND created_at LIKE ?""",
        (organisation_id, period + "%"),
    )
    config = get_ai_config(False)
    recent = rows("SELECT operation,model,total_tokens,estimated_cost_eur,status,error_code,created_at FROM ai_usage_events WHERE organisation_id=? ORDER BY id DESC LIMIT 20", (organisation_id,))
    return {"period": period, **totals, "budget_eur": config["monthly_budget_eur"], "recent": recent}


def budget_available() -> bool:
    summary = usage_summary()
    if not summary["budget_eur"]:
        return True
    return Decimal(str(summary["estimated_cost_eur"])) < Decimal(summary["budget_eur"])


def record_usage(operation: str, model: str, request_id: str, usage: dict, status: str = "completed", error_code: str = "") -> None:
    config = get_ai_config(False)
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    cost = ""
    if config["input_eur_per_million"] or config["output_eur_per_million"]:
        amount = Decimal(input_tokens) * Decimal(config["input_eur_per_million"] or "0") / Decimal(1_000_000)
        amount += Decimal(output_tokens) * Decimal(config["output_eur_per_million"] or "0") / Decimal(1_000_000)
        cost = format(amount.quantize(Decimal("0.000001")), "f")
    with transaction() as connection:
        connection.execute(
            """INSERT INTO ai_usage_events(organisation_id,operation,provider,model,request_id,input_tokens,output_tokens,total_tokens,estimated_cost_eur,status,error_code,created_at)
               VALUES(?,?,'OpenAI',?,?,?,?,?,?,?,?,?)""",
            (current_organisation_id(), operation, model, request_id, input_tokens, output_tokens, total_tokens, cost, status, error_code, utc_now()),
        )
