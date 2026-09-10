import tempfile
import unittest
from pathlib import Path

from app import db
from app import secret_store
from app.ai_control import get_ai_config, record_usage, save_ai_config, usage_summary
from app.billing import BillingDisabledError, create_checkout_session, record_metered_usage, subscription_status
from app.auth import (
    create_session, current_organisation_id, hash_password, load_session,
    reset_context, set_context, verify_password,
)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_database = db.DATABASE_PATH
        self.original_secret_path = secret_store.KEY_PATH
        db.DATABASE_PATH = Path(self.temporary.name) / "auth.sqlite3"
        secret_store.KEY_PATH = Path(self.temporary.name) / ".secret-key"
        db.init_db()

    def tearDown(self):
        db.DATABASE_PATH = self.original_database
        secret_store.KEY_PATH = self.original_secret_path
        self.temporary.cleanup()

    def test_password_hash_and_organisation_session(self):
        password_hash = hash_password("correct horse battery staple")
        self.assertTrue(verify_password(password_hash, "correct horse battery staple"))
        self.assertFalse(verify_password(password_hash, "incorrect password"))
        now = db.utc_now()
        with db.transaction() as connection:
            user_id = connection.execute(
                """INSERT INTO users(email,email_normalized,name,password_hash,status,platform_role,email_verified_at,created_at,updated_at)
                   VALUES('owner@example.com','owner@example.com','Owner',?,'active','owner',?,?,?)""",
                (password_hash, now, now, now),
            ).lastrowid
            connection.execute(
                "INSERT INTO organisation_memberships(organisation_id,user_id,role,status,created_at) VALUES(1,?,'owner','active',?)",
                (user_id, now),
            )
        raw_token, context = create_session(user_id, 1)
        self.assertNotIn(raw_token, str(db.row("SELECT token_hash FROM user_sessions")))
        self.assertEqual(load_session(raw_token)["user_id"], user_id)
        context_token = set_context(context)
        try:
            self.assertEqual(current_organisation_id(), 1)
            saved = save_ai_config({"api_key": "sk-test-secret-key-1234567890", "model": "test-model", "input_eur_per_million": "2", "output_eur_per_million": "8"})
            self.assertEqual(saved["key_last4"], "7890")
            self.assertNotIn("api_key", saved)
            self.assertEqual(get_ai_config(True)["api_key"], "sk-test-secret-key-1234567890")
            self.assertNotIn("sk-test-secret", str(db.row("SELECT * FROM ai_settings WHERE organisation_id=1")))
            record_usage("invoice_layout_learning", "test-model", "req_1", {"input_tokens": 1000, "output_tokens": 100})
            self.assertEqual(usage_summary()["total_tokens"], 1100)
        finally:
            reset_context(context_token)

    def test_short_password_is_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("too short")

    def test_billing_foundation_is_inactive_and_usage_is_idempotent(self):
        self.assertFalse(subscription_status(1)["billing_enabled"])
        record_metered_usage(1, "invoices_per_month", 1, "invoice:7")
        record_metered_usage(1, "invoices_per_month", 1, "invoice:7")
        self.assertEqual(db.row("SELECT SUM(amount) AS total FROM metered_usage")["total"], 1)
        with self.assertRaises(BillingDisabledError):
            create_checkout_session(1, "starter")


if __name__ == "__main__":
    unittest.main()
