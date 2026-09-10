import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app import main
from app.main import app


class AuthenticationApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_database = db.DATABASE_PATH
        db.DATABASE_PATH = Path(self.temporary.name) / "api-auth.sqlite3"
        self.original_bootstrap_token = main.BOOTSTRAP_TOKEN
        main.BOOTSTRAP_TOKEN = "one-time-server-setup-code"
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.DATABASE_PATH = self.original_database
        main.BOOTSTRAP_TOKEN = self.original_bootstrap_token
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


if __name__ == "__main__":
    unittest.main()
