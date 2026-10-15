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

    def test_financial_data_is_private_per_user(self):
        status = self.client.get("/api/status").get_json()
        self.assertTrue(status["setup_required"])

        created = self.json(self.client, "/api/setup", "POST", {
            "bootstrap_token": os.environ["BOOTSTRAP_TOKEN"],
            "username": "mainadmin",
            "password": "strong-initial-password-123",
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        admin_csrf = created.get_json()["csrf_token"]

        # The original admin writes a private financial record.
        empty = self.json(self.client, "/api/state").get_json()
        admin_data = empty["data"]
        admin_data["expenses"] = [{"id": "admin-expense-1", "amount": 12.5, "currency": "USD", "category": "comida", "description": "Almuerzo admin", "method": "efectivo", "bank": "", "date": "2026-10-14T12:00:00Z"}]
        admin_saved = self.json(self.client, "/api/state", "PUT", {"base_data": empty["data"], "base_revision": empty["revision"], "data": admin_data}, admin_csrf)
        self.assertEqual(admin_saved.status_code, 200, admin_saved.get_json())
        self.assertEqual(len(admin_saved.get_json()["data"]["expenses"]), 1)

        # Create two separate authorized accounts.
        member1_response = self.json(self.client, "/api/users", "POST", {"username": "memberone", "role": "member"}, admin_csrf)
        member2_response = self.json(self.client, "/api/users", "POST", {"username": "membertwo", "role": "member"}, admin_csrf)
        self.assertEqual(member1_response.status_code, 201, member1_response.get_json())
        self.assertEqual(member2_response.status_code, 201, member2_response.get_json())

        def authenticated_member(login_name, temporary_password, new_password):
            client = self.app.test_client()
            login = self.json(client, "/api/login", "POST", {"username": login_name, "password": temporary_password})
            self.assertEqual(login.status_code, 200, login.get_json())
            csrf = login.get_json()["csrf_token"]
            changed = self.json(client, "/api/change-password", "POST", {"old_password": temporary_password, "new_password": new_password}, csrf)
            self.assertEqual(changed.status_code, 200, changed.get_json())
            return client, changed.get_json()["csrf_token"]

        member1, member1_csrf = authenticated_member("memberone", member1_response.get_json()["temporary_password"], "memberone-new-secure-password-987")
        member2, _member2_csrf = authenticated_member("membertwo", member2_response.get_json()["temporary_password"], "membertwo-new-secure-password-987")

        # Each member sees an empty, independent state — never the admin's records.
        member1_state_response = self.json(member1, "/api/state")
        member1_state = member1_state_response.get_json()
        self.assertEqual(member1_state_response.status_code, 200)
        self.assertEqual(member1_state["data"]["expenses"], [])
        self.assertEqual(member1_state["data"]["debts"], [])

        # Members are allowed to fully manage their own finances.
        member1_data = member1_state["data"]
        member1_data["expenses"] = [{"id": "member1-expense", "amount": 8, "currency": "USD", "category": "transporte", "description": "Transporte propio", "method": "efectivo", "bank": "", "date": "2026-10-14T13:00:00Z"}]
        member1_saved = self.json(member1, "/api/state", "PUT", {"base_data": member1_state["data"], "base_revision": member1_state["revision"], "data": member1_data}, member1_csrf)
        self.assertEqual(member1_saved.status_code, 200, member1_saved.get_json())
        self.assertEqual([x["id"] for x in member1_saved.get_json()["data"]["expenses"]], ["member1-expense"])

        # Neither admin nor another member sees member one's private data.
        admin_after = self.json(self.client, "/api/state").get_json()
        self.assertEqual([x["id"] for x in admin_after["data"]["expenses"]], ["admin-expense-1"])
        member2_after = self.json(member2, "/api/state").get_json()
        self.assertEqual(member2_after["data"]["expenses"], [])
        self.assertEqual(member2_after["data"]["debts"], [])

        # User administration remains admin-only.
        self.assertEqual(self.json(member1, "/api/users").status_code, 403)
        self.assertEqual(self.json(member1, "/api/audit-log").status_code, 403)
        self.assertEqual(self.client.get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
