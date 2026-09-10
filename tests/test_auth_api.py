import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db, secret_store
from app import main
from app.main import app


class AuthenticationApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_database = db.DATABASE_PATH
        db.DATABASE_PATH = Path(self.temporary.name) / "api-auth.sqlite3"
        self.original_bootstrap_token = main.BOOTSTRAP_TOKEN
        self.original_secret_path = secret_store.KEY_PATH
        main.BOOTSTRAP_TOKEN = "one-time-server-setup-code"
        secret_store.KEY_PATH = Path(self.temporary.name) / ".secret-key"
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DATABASE_PATH = self.original_database
        main.BOOTSTRAP_TOKEN = self.original_bootstrap_token
        secret_store.KEY_PATH = self.original_secret_path
        self.temporary.cleanup()

    def test_owner_setup_csrf_admin_and_temporary_password(self):
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)
        created = self.client.post(
            "/api/auth/setup-owner",
            json={"name": "Owner", "email": "owner@example.com", "organisation": "Existing Co", "setup_code": "one-time-server-setup-code", "password": "correct horse battery staple"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        bootstrap = self.client.get("/api/bootstrap")
        self.assertEqual(bootstrap.status_code, 200, bootstrap.text)
        csrf = bootstrap.json()["auth"]["csrf_token"]
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 200)
        ai_saved = self.client.patch("/api/ai/settings", headers={"X-CSRF-Token": csrf}, json={"api_key": "sk-test-key-123456789012345", "model": "test-model", "monthly_budget_eur": "10"})
        self.assertEqual(ai_saved.status_code, 200, ai_saved.text)
        self.assertNotIn("sk-test-key", ai_saved.text)
        self.assertEqual(self.client.get("/api/ai/settings").json()["config"]["key_source"], "app")
        now = db.utc_now()
        with db.transaction() as connection:
            supplier_id = connection.execute(
                """INSERT INTO supplier_profiles(organisation_id,supplier_vat,supplier_name,layout_version,created_at,updated_at)
                   VALUES(1,'PT123456789','Example Supplier',2,?,?)""", (now, now),
            ).lastrowid
            first_template = connection.execute(
                """INSERT INTO supplier_template_versions(organisation_id,supplier_vat,version,layout_fingerprint,layout_mapping,source,active,created_at)
                   VALUES(1,'PT123456789',1,'old','[{"field":"invoice_number","page":1,"x":0.1,"y":0.1,"width":0.1,"height":0.1}]','manual',0,?)""", (now,),
            ).lastrowid
            connection.execute(
                """INSERT INTO supplier_template_versions(organisation_id,supplier_vat,version,layout_fingerprint,layout_mapping,source,active,created_at)
                   VALUES(1,'PT123456789',2,'new','[{"field":"line_sku","page":1,"x":0.1,"y":0.1,"width":0.1,"height":0.5}]','api-ai',1,?)""", (now,),
            )
        self.assertEqual(self.client.get("/api/suppliers").json()[0]["template_count"], 2)
        self.assertEqual(self.client.get(f"/api/suppliers/{supplier_id}").json()["templates"][0]["fields"], ["line_sku"])
        restored = self.client.post(f"/api/suppliers/{supplier_id}/templates/{first_template}/activate", headers={"X-CSRF-Token": csrf})
        self.assertEqual(restored.json()["active_version"], 1)
        self.assertEqual(self.client.post("/api/admin/accounts", json={}).status_code, 403)
        account = self.client.post(
            "/api/admin/accounts", headers={"X-CSRF-Token": csrf},
            json={"name": "Preparer", "email": "prep@example.com", "role": "preparer", "temporary_password": "temporary secure passphrase"},
        )
        self.assertEqual(account.status_code, 200, account.text)
        self.assertEqual(self.client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertEqual(self.client.get("/api/bootstrap").status_code, 401)
        signed_in = self.client.post("/api/auth/login", json={"email": "prep@example.com", "password": "temporary secure passphrase"})
        self.assertEqual(signed_in.status_code, 200, signed_in.text)
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/login")

    def test_exported_invoice_is_locked_and_correction_copies_rows(self):
        created = self.client.post(
            "/api/auth/setup-owner",
            json={"name": "Owner", "email": "owner@example.com", "organisation": "Existing Co", "setup_code": "one-time-server-setup-code", "password": "correct horse battery staple"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        csrf = self.client.get("/api/bootstrap").json()["auth"]["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        invoice = self.client.post("/api/invoices", headers=headers).json()
        now = db.utc_now()
        with db.transaction() as connection:
            connection.execute("UPDATE invoices SET invoice_number='INV-LOCKED',status='exported' WHERE id=?", (invoice["id"],))
            connection.execute(
                "INSERT INTO invoice_lines(invoice_id,position,sku,description,quantity,reviewed,created_at,updated_at) VALUES(?,1,'SKU-1','Product','3',1,?,?)",
                (invoice["id"], now, now),
            )
        locked = self.client.patch(f"/api/invoices/{invoice['id']}", headers=headers, json={"supplier_name": "Changed"})
        self.assertEqual(locked.status_code, 409)
        response = self.client.post(f"/api/invoices/{invoice['id']}/correction", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        correction = response.json()
        self.assertEqual(correction["parent_invoice_id"], invoice["id"])
        self.assertEqual(correction["correction_number"], 1)
        self.assertEqual(correction["status"], "needs_review")
        self.assertEqual(correction["lines"][0]["sku"], "SKU-1")
        self.assertEqual(correction["lines"][0]["reviewed"], 0)


if __name__ == "__main__":
    unittest.main()
