"""Lead sync: new rows in the source land in the leads table, and only once.

Runs against the temp SQLite database conftest.py points the suite at; the Postgres path
uses the same persistence API (see tests/test_pg_store.py for the Postgres-specific store).
"""

import unittest
import unittest.mock

from services.lead_sync import detect_course_from_ad, lead_fields, normalize_phone, sync_leads
from services.persistence import get_lead, upsert_lead


def _row(phone, name, ad_name, **extra):
    row = {
        "whatsapp_number": phone,
        "full_name": name,
        "ad_name": ad_name,
        "email": f"{name.split()[0].lower()}@example.com",
        "job_title": "Engineer",
        "company_name": "Acme",
        "who_will_pay?": "company",
    }
    row.update(extra)
    return row


class ParsingTests(unittest.TestCase):
    def test_normalize_phone_handles_excel_floats_and_prefixes(self):
        self.assertEqual("919444209374", normalize_phone("919444209374.0"))
        self.assertEqual("60123456789", normalize_phone("+60 12-345 6789"))
        self.assertIsNone(normalize_phone("12345"))
        self.assertIsNone(normalize_phone(None))

    def test_who_will_pay_question_mark_column_variant(self):
        fields = lead_fields(_row("60123456789", "Ada L", "ELSI"), "slug")
        self.assertEqual("company", fields["who_will_pay"])
        self.assertEqual("slug", fields["course_slug"])

    def test_course_detection_falls_back_to_ad_abbreviations(self):
        from services.course_loader import load_courses

        courses = load_courses()
        slug = detect_course_from_ad("YOCT_Aug26_leadform", courses)
        self.assertIsNotNone(slug)
        self.assertIn("yocto", slug)
        self.assertIsNone(detect_course_from_ad("generic brand awareness", courses))


class SyncTests(unittest.TestCase):
    """sync_leads is patched onto a fake Excel source so no file or network is touched."""

    def setUp(self):
        self.rows = [_row("60111000001", "Ada Lovelace", "ELSI_Aug26")]
        patcher = unittest.mock.patch(
            "services.lead_sync.rows_from_excel", side_effect=lambda _p: self.rows
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def _sync(self, **kwargs):
        return sync_leads(excel_path="fake.xlsx", **kwargs)

    def test_new_row_is_added_then_not_added_again(self):
        first = self._sync()
        self.assertEqual(1, first.added)
        self.assertEqual("CREATED", get_lead("60111000001")["status"])

        second = self._sync()
        self.assertEqual(0, second.added)
        self.assertEqual(1, second.skipped_existing)

    def test_only_the_new_row_is_written_on_a_later_pass(self):
        self._sync()
        self.rows.append(_row("60111000002", "Grace Hopper", "ELSI_Aug26"))
        result = self._sync()
        self.assertEqual(1, result.added)
        self.assertEqual(["60111000002"], result.added_phones)
        self.assertEqual(2, result.scanned)

    def test_update_pass_never_resets_an_in_flight_conversation(self):
        self._sync()
        upsert_lead("60111000001", status="QUALIFYING")
        result = self._sync(update=True)
        self.assertEqual(1, result.updated)
        self.assertEqual("QUALIFYING", get_lead("60111000001")["status"])

    def test_dry_run_reports_without_writing(self):
        result = self._sync(dry_run=True)
        self.assertEqual(1, result.added)
        self.assertIsNone(get_lead("60111000001"))

    def test_invalid_phone_and_unmatched_ad_are_counted_not_written(self):
        self.rows = [
            _row("123", "Bad Phone", "ELSI_Aug26"),
            _row("60111000003", "No Course", "brand awareness generic"),
        ]
        result = self._sync()
        self.assertEqual(0, result.added)
        self.assertEqual(1, result.skipped_invalid)
        self.assertEqual(1, result.unmatched)


class SyncEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import main

        cls.main = main
        cls.client = TestClient(main.app)

    def test_endpoint_is_disabled_without_a_configured_token(self):
        with unittest.mock.patch.dict("os.environ", {"LEAD_SYNC_TOKEN": ""}):
            response = self.client.post("/admin/sync-leads")
        self.assertEqual(503, response.status_code)

    def test_endpoint_rejects_a_wrong_token(self):
        with unittest.mock.patch.dict("os.environ", {"LEAD_SYNC_TOKEN": "right"}):
            response = self.client.post("/admin/sync-leads", headers={"x-sync-token": "wrong"})
        self.assertEqual(401, response.status_code)

    def test_endpoint_runs_sync_then_outreach_with_a_valid_token(self):
        with (
            unittest.mock.patch.dict("os.environ", {"LEAD_SYNC_TOKEN": "right"}),
            unittest.mock.patch.object(
                self.main, "_run_lead_sync", return_value={"source": "sheet", "added": 2}
            ),
            unittest.mock.patch.object(self.main, "dispatch_outreach") as dispatch,
        ):
            dispatch.return_value.as_dict.return_value = {"sent": 2}
            response = self.client.post("/admin/sync-leads", headers={"x-sync-token": "right"})
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(2, body["sync"]["added"])
        self.assertEqual(2, body["outreach"]["sent"])
        dispatch.assert_called_once()

    def test_poller_is_off_unless_an_interval_is_configured(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("LEAD_SYNC_INTERVAL_MINUTES", None)
            self.assertEqual(0, self.main._lead_sync_interval_seconds())
            os.environ["LEAD_SYNC_INTERVAL_MINUTES"] = "15"
            self.assertEqual(900, self.main._lead_sync_interval_seconds())
            os.environ["LEAD_SYNC_INTERVAL_MINUTES"] = "nonsense"
            self.assertEqual(0, self.main._lead_sync_interval_seconds())
            os.environ.pop("LEAD_SYNC_INTERVAL_MINUTES", None)


if __name__ == "__main__":
    unittest.main()
