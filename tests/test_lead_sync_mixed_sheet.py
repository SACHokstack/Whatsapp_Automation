"""Reading the raw Meta Lead Ads export: one flat sheet, every course mixed together.

The fixture rows use the real export's 19 columns and real ad_name values, so a change to
the column set or the ad-name parsing shows up here rather than in production.
"""

import unittest
import unittest.mock

from services.lead_sync import sync_leads
from services.persistence import get_lead

# Exactly the header set of the Meta export
COLUMNS = [
    "id",
    "created_time",
    "ad_id",
    "ad_name",
    "adset_id",
    "adset_name",
    "campaign_id",
    "campaign_name",
    "form_id",
    "form_name",
    "is_organic",
    "platform",
    "who_will_pay?",
    "full_name",
    "whatsapp_number",
    "email",
    "job_title",
    "company_name",
    "lead_status",
]


def _export_row(ad_name, full_name, phone, lead_status=""):
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        {
            "id": "l:37021794584100812",
            "created_time": "2026-07-04T06:24:13-05:00",
            "ad_id": "ag:120251196291790721",
            "ad_name": ad_name,
            "adset_name": "May Cold Audience | Penang",
            "campaign_name": "Jul-Aug 2026 | Leads",
            "platform": "fb",
            "is_organic": "false",
            "who_will_pay?": "company",
            "full_name": full_name,
            "whatsapp_number": phone,
            "email": "lead@example.com",
            "job_title": "Engineer",
            "company_name": "Acme Sdn Bhd",
            "lead_status": lead_status,
        }
    )
    return row


_phone_seq = iter(range(100, 999))


class MixedSheetTests(unittest.TestCase):
    def setUp(self):
        # Each test gets fresh phone numbers — leads are upserted, never deleted, so reusing
        # a number across tests would make the second test see an "already known" lead.
        block = next(_phone_seq)
        self.phones = [f"6013{block}0000{index}" for index in range(3)]
        self.rows = [
            _export_row("Ads 8 - ELSI -  Nona rollerblade song", "Jasrul Amir", self.phones[0]),
            _export_row(
                "Ads 1 - Y0CTO - Vid Engineer Pain-OBH (16_9)", "Vincent R", self.phones[1]
            ),
            _export_row("Ads 3 - brand awareness", "No Course", self.phones[2]),
        ]
        env = unittest.mock.patch.dict("os.environ", {"LEAD_SYNC_WORKSHEET": "Sheet1"})
        env.start()
        self.addCleanup(env.stop)
        rows = unittest.mock.patch(
            "services.google_sheets.get_rows_from", side_effect=lambda *a, **k: self.rows
        )
        rows.start()
        self.addCleanup(rows.stop)

    def test_each_row_is_routed_to_its_course_by_ad_name(self):
        result = sync_leads()
        self.assertEqual(2, result.added)
        self.assertEqual(1, result.unmatched)  # the brand-awareness ad matches no course

        internals = get_lead(self.phones[0])
        yocto = get_lead(self.phones[1])
        self.assertIn("internals", internals["course"])
        self.assertIn("yocto", yocto["course"])
        self.assertEqual("CREATED", internals["status"])
        self.assertIsNone(get_lead(self.phones[2]))  # unmatched row is not written

    def test_export_columns_map_onto_the_lead_record(self):
        sync_leads()
        lead = get_lead(self.phones[0])
        self.assertEqual("Jasrul Amir", lead["name"])
        self.assertEqual("Engineer", lead["job_title"])
        self.assertEqual("Acme Sdn Bhd", lead["company_name"])
        self.assertEqual("company", lead["who_will_pay"])  # from the 'who_will_pay?' column

    def test_a_row_already_marked_contacted_is_not_reimported_as_new(self):
        self.rows = [
            _export_row("Ads 8 - ELSI - x", "Already Done", self.phones[0], lead_status="CONTACTED")
        ]
        sync_leads()
        # Imported with the sheet's status, so the outreach pass will not message them again
        self.assertEqual("CONTACTED", get_lead(self.phones[0])["status"])

    def test_leads_predating_the_cutoff_are_never_queued_for_auto_contact(self):
        """The pre-launch leads were called by hand — the bot must not message them again."""
        from services.auto_outreach import PENDING_STATUSES
        from services.lead_sync import BASELINE_STATUS

        rows = [_export_row("Ads 8 - ELSI - x", "Old Lead", self.phones[0])]
        rows[0]["created_time"] = "2026-07-04T06:24:13-05:00"  # before launch
        rows.append(_export_row("Ads 8 - ELSI - x", "New Lead", self.phones[1]))
        rows[1]["created_time"] = "2026-08-20T09:00:00-05:00"  # after launch
        self.rows = rows

        with unittest.mock.patch.dict(
            "os.environ", {"LEAD_SYNC_CONTACT_CUTOFF": "2026-08-15T00:00:00+00:00"}
        ):
            result = sync_leads()

        self.assertEqual(2, result.added)
        self.assertEqual(1, result.baseline)
        self.assertEqual(BASELINE_STATUS, get_lead(self.phones[0])["status"])
        self.assertEqual("CREATED", get_lead(self.phones[1])["status"])
        # The decisive check: the old lead is not in a status outreach will pick up
        self.assertNotIn(get_lead(self.phones[0])["status"], PENDING_STATUSES)

    def test_cutoff_demotes_leads_imported_before_it_was_configured(self):
        """The live case: leads synced with no cutoff sit at CREATED and would all be
        messaged the moment outreach is switched on. Setting the cutoff must clear them."""
        from services.lead_sync import BASELINE_STATUS
        from services.persistence import upsert_lead

        rows = [
            _export_row("Ads 8 - ELSI - x", "Awaiting", self.phones[0]),
            _export_row("Ads 8 - ELSI - x", "Already Contacted", self.phones[1]),
            _export_row("Ads 8 - ELSI - x", "Replied", self.phones[2]),
        ]
        for row in rows:
            row["created_time"] = "2026-07-04T06:24:13-05:00"
        self.rows = rows

        # Imported earlier, before any cutoff existed
        sync_leads()
        self.assertEqual("CREATED", get_lead(self.phones[0])["status"])
        upsert_lead(self.phones[1], status="CONTACTED")
        upsert_lead(self.phones[2], status="ENGAGED")

        with unittest.mock.patch.dict(
            "os.environ", {"LEAD_SYNC_CONTACT_CUTOFF": "2026-08-15T00:00:00+00:00"}
        ):
            result = sync_leads()

        self.assertEqual(1, result.rebaselined)
        self.assertEqual(BASELINE_STATUS, get_lead(self.phones[0])["status"])
        # An outreach that already happened, and a live conversation, must not be rewritten
        self.assertEqual("CONTACTED", get_lead(self.phones[1])["status"])
        self.assertEqual("ENGAGED", get_lead(self.phones[2])["status"])

    def test_rebaseline_is_reported_but_not_written_on_a_dry_run(self):
        rows = [_export_row("Ads 8 - ELSI - x", "Awaiting", self.phones[0])]
        rows[0]["created_time"] = "2026-07-04T06:24:13-05:00"
        self.rows = rows
        sync_leads()

        with unittest.mock.patch.dict(
            "os.environ", {"LEAD_SYNC_CONTACT_CUTOFF": "2026-08-15T00:00:00+00:00"}
        ):
            result = sync_leads(dry_run=True)
        self.assertEqual(1, result.rebaselined)
        self.assertEqual("CREATED", get_lead(self.phones[0])["status"])  # unchanged

    def test_a_row_with_no_date_is_treated_as_pre_cutoff(self):
        from services.lead_sync import BASELINE_STATUS

        rows = [_export_row("Ads 8 - ELSI - x", "Undated", self.phones[0])]
        rows[0]["created_time"] = ""
        self.rows = rows
        with unittest.mock.patch.dict(
            "os.environ", {"LEAD_SYNC_CONTACT_CUTOFF": "2026-08-15T00:00:00+00:00"}
        ):
            sync_leads()
        self.assertEqual(BASELINE_STATUS, get_lead(self.phones[0])["status"])

    def test_without_a_cutoff_every_row_is_contactable(self):
        with unittest.mock.patch.dict("os.environ", {"LEAD_SYNC_CONTACT_CUTOFF": ""}):
            result = sync_leads()
        self.assertEqual(0, result.baseline)
        self.assertEqual("CREATED", get_lead(self.phones[0])["status"])

    def test_per_course_tabs_still_work_when_no_mixed_worksheet_is_configured(self):
        with unittest.mock.patch.dict("os.environ", {"LEAD_SYNC_WORKSHEET": ""}):
            with unittest.mock.patch("services.lead_sync.get_active_courses", return_value=[]):
                result = sync_leads()
        self.assertEqual(0, result.scanned)


if __name__ == "__main__":
    unittest.main()
