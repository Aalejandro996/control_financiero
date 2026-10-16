"""Run with: python tests/reminder_logic_test.py (after installing requirements)."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "control_financiero_reminders_test.db"
try:
    TEST_DB.unlink()
except FileNotFoundError:
    pass
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["APP_SECRET_KEY"] = "test-secret-key-for-control-financiero"
os.environ["COOKIE_SECURE"] = "false"
os.environ["APP_TIMEZONE"] = "America/Caracas"

from send_payment_reminders import eligible_events  # noqa: E402


class ReminderLogicTest(unittest.TestCase):
    def test_debt_payment_reminders_use_only_7_4_1_and_0_day_windows(self):
        today = date(2026, 10, 16)
        rows = []
        for lead in (7, 4, 1, 0, 2, -1):
            rows.append({
                "id": f"debt-{lead}", "debtId": f"debt-{lead}",
                "entity": "Banco", "description": "Tarjeta", "amount": 10,
                "currency": "USD", "scheduleType": "date",
                "scheduledDate": (today + timedelta(days=lead)).isoformat(),
                "status": "planned",
            })
        rows.append({
            "id": "cancelled", "debtId": "cancelled", "amount": 10,
            "currency": "USD", "scheduleType": "date",
            "scheduledDate": (today + timedelta(days=1)).isoformat(),
            "status": "cancelled",
        })
        events = list(eligible_events({"plannedDebtPayments": rows}, today))
        leads = sorted(event[2] for event in events)
        self.assertEqual(leads, [0, 1, 4, 7])

    def test_school_payments_are_reminded_only_while_balance_remains(self):
        today = date(2026, 10, 16)
        data = {
            "plannedDebtPayments": [],
            "schoolExpenses": [
                {"id": "school-open", "student": "Ana", "description": "Mensualidad", "amount": 100, "currency": "USD", "dueDate": (today + timedelta(days=4)).isoformat()},
                {"id": "school-paid", "student": "Ana", "description": "Útiles", "amount": 50, "currency": "USD", "dueDate": (today + timedelta(days=1)).isoformat()},
            ],
            "schoolPayments": [
                {"id": "payment-1", "expenseId": "school-paid", "amount": 50, "currency": "USD"},
            ],
        }
        events = list(eligible_events(data, today))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "school:school-open")
        self.assertEqual(events[0][2], 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
