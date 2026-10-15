"""Run after installing requirements: python tests/smoke_test.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_DB = Path(tempfile.gettempdir()) / "control_financiero_smoke_test.db"
try:
    TEST_DB.unlink()
except FileNotFoundError:
    pass
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["APP_SECRET_KEY"] = "test-secret-key-for-control-financiero"
os.environ["BOOTSTRAP_TOKEN"] = "test-bootstrap-token-with-over-32-characters"
os.environ["COOKIE_SECURE"] = "false"

import app as app_module  # noqa: E402


class ControlFinancieroSmokeTest(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.client = self.app.test_client()

    def json(self, client, path, method="GET", body=None, csrf=None):
        headers = {"Content-Type": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        return client.open(path, method=method, json=body, headers=headers)

    def test_setup_login_shared_state_permissions(self):
        status = self.client.get("/api/status").get_json()
        self.assertTrue(status["setup_required"])

        created = self.json(self.client, "/api/setup", "POST", {
            "bootstrap_token": os.environ["BOOTSTRAP_TOKEN"],
            "username": "mainadmin",
            "password": "strong-initial-password-123",
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        admin_csrf = created.get_json()["csrf_token"]

        empty = self.json(self.client, "/api/state")
        data = empty.get_json()["data"]
        data["expenses"] = [{"id": "1001", "amount": 12.5, "currency": "USD", "category": "comida", "description": "Almuerzo", "method": "efectivo", "bank": "", "date": "2026-10-14T12:00:00Z"}]
        saved = self.json(self.client, "/api/state", "PUT", {"base_data": empty.get_json()["data"], "base_revision": empty.get_json()["revision"], "data": data}, admin_csrf)
        self.assertEqual(saved.status_code, 200, saved.get_json())

        new_user = self.json(self.client, "/api/users", "POST", {"username": "memberone", "role": "member"}, admin_csrf)
        self.assertEqual(new_user.status_code, 201, new_user.get_json())
        temporary_password = new_user.get_json()["temporary_password"]

        member = self.app.test_client()
        login = self.json(member, "/api/login", "POST", {"username": "memberone", "password": temporary_password})
        self.assertEqual(login.status_code, 200, login.get_json())
        member_csrf = login.get_json()["csrf_token"]

        password_change = self.json(member, "/api/change-password", "POST", {"old_password": temporary_password, "new_password": "member-new-secure-password-987"}, member_csrf)
        self.assertEqual(password_change.status_code, 200, password_change.get_json())
        member_csrf = password_change.get_json()["csrf_token"]

        member_state = self.json(member, "/api/state").get_json()
        original = member_state["data"]
        removed = {**original, "expenses": []}
        denied = self.json(member, "/api/state", "PUT", {"base_data": original, "base_revision": member_state["revision"], "data": removed}, member_csrf)
        self.assertEqual(denied.status_code, 403, denied.get_json())

        allowed = self.json(member, "/api/users")
        self.assertEqual(allowed.status_code, 403, allowed.get_json())

        shared = self.json(member, "/api/state").get_json()
        self.assertEqual(len(shared["data"]["expenses"]), 1)
        self.assertEqual(self.client.get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
