from __future__ import annotations

import json

from .db import row, transaction, utc_now


class BillingDisabledError(RuntimeError):
    code = "BILLING_DISABLED"


def billing_enabled() -> bool:
    setting = row("SELECT value FROM platform_settings WHERE key='billing_enabled'")
    return bool(setting and setting["value"].lower() == "true")


def subscription_status(organisation_id: int) -> dict:
    subscription = row(
        """SELECT s.status,s.plan_id,p.name,p.entitlements,s.updated_at
           FROM organisation_subscriptions s JOIN plans p ON p.id=s.plan_id
           WHERE s.organisation_id=?""",
        (organisation_id,),
    ) or {"status": "inactive", "plan_id": "internal", "name": "Internal", "entitlements": "{}", "updated_at": ""}
    subscription["entitlements"] = json.loads(subscription.get("entitlements") or "{}")
    subscription["billing_enabled"] = billing_enabled()
    return subscription


def check_entitlement(organisation_id: int, capability: str, requested_amount: int = 1) -> dict:
    status = subscription_status(organisation_id)
    limit = status["entitlements"].get(capability)
    if limit is None:
        return {"allowed": True, "limit": None}
    used = row(
        "SELECT COALESCE(SUM(amount),0) AS total FROM metered_usage WHERE organisation_id=? AND metric=?",
        (organisation_id, capability),
    )["total"]
    return {"allowed": used + requested_amount <= int(limit), "used": used, "limit": int(limit)}


def record_metered_usage(organisation_id: int, metric: str, amount: int, source_id: str) -> None:
    with transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO metered_usage(organisation_id,metric,amount,source_id,created_at) VALUES(?,?,?,?,?)",
            (organisation_id, metric, amount, source_id, utc_now()),
        )


def create_checkout_session(*_args, **_kwargs):
    if not billing_enabled():
        raise BillingDisabledError("Billing is not active")
    raise RuntimeError("No payment provider is configured")

