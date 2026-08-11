from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from services.ai_reply import _deterministic_reply, _recent_conversation, _sanitize_reply
from services.course_loader import get_course
from services.google_sheets import _update_worksheet
from services.sqlite_store import add_message, get_messages
from services.whatsapp import _messages_url


class GoogleSheetsTests(unittest.TestCase):
    def test_updates_are_sent_in_one_batch(self):
        worksheet = Mock()
        worksheet.row_values.return_value = ["phone", "status", "last_reply"]
        worksheet.get_all_records.return_value = [{"phone": "+60 12-345 6789"}]

        result = _update_worksheet(
            worksheet,
            "60123456789",
            [("status", "ENGAGED"), ("last_reply", "Hello")],
        )

        self.assertTrue(result)
        worksheet.batch_update.assert_called_once()
        payload = worksheet.batch_update.call_args.args[0]
        self.assertEqual(2, len(payload))


class WhatsAppConfigurationTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "WHATSAPP_API_VERSION": "v99.0",
            "PHONE_NUMBER_ID": "123",
            "WHATSAPP_PHONE_NUMBER_ID": "123",
        },
        clear=False,
    )
    def test_api_version_is_configurable(self):
        self.assertEqual("https://graph.facebook.com/v99.0/123/messages", _messages_url())


class SQLiteHistoryTests(unittest.TestCase):
    def test_history_limit_returns_most_recent_messages_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "history.db")
            with patch.dict(os.environ, {"WHATSAPP_DB_PATH": path}):
                add_message("60000000001", direction="inbound", body="first")
                add_message("60000000001", direction="outbound", body="second")
                add_message("60000000001", direction="inbound", body="third")
                messages = get_messages("60000000001", limit=2)
        self.assertEqual(["second", "third"], [item["body"] for item in messages])


class DeterministicReplyTests(unittest.TestCase):
    def setUp(self):
        self.course = get_course("embedded-linux-yocto-aug-2026")

    def test_fee_schedule_and_venue_are_answered_without_ai(self):
        reply = _deterministic_reply("What is the fee, date and venue?", self.course)
        self.assertIn("RM7,500", reply)
        self.assertIn("10–14 August 2026", reply)
        self.assertIn("Penang", reply)

    def test_beginner_question_uses_course_knowledge(self):
        reply = _deterministic_reply("I am new to Yocto. Can I join?", self.course)
        self.assertIn("Previous Yocto experience is not required", reply)

    def test_duration_is_answered(self):
        reply = _deterministic_reply("How many days is the course?", self.course)
        self.assertIn("5-day", reply)

    def test_shared_payment_policy_is_answered(self):
        reply = _deterministic_reply("Can I pay by installment?", self.course)
        self.assertIn("not listed as a standard", reply.lower())
        self.assertIn("bank transfer", reply.lower())

    def test_installment_claim_challenge_stays_on_topic_and_corrects_it(self):
        history = [
            {
                "direction": "outbound",
                "body": "Yes, we can discuss installment arrangements if needed.",
            }
        ]
        reply = _deterministic_reply(
            "I checked their website and no such claims", self.course, history=history
        )
        self.assertIn("overstated", reply)
        self.assertIn("bank transfer", reply)
        self.assertNotIn("Embedded C", reply)
        self.assertNotIn("cancellation", reply.lower())

    def test_cancellation_correction_recovers_previous_payment_topic(self):
        history = [
            {"direction": "inbound", "body": "Do you accept installments?"},
            {
                "direction": "outbound",
                "body": "Here is the course and cancellation policy.",
            },
        ]
        reply = _deterministic_reply(
            "I didn't ask about cancellation?", self.course, history=history
        )
        self.assertIn("we were discussing installments", reply)
        self.assertIn("bank transfer", reply)
        self.assertNotIn("cancellation policy", reply)

    def test_payment_installation_typo_is_understood(self):
        reply = _deterministic_reply("I told you about payment installation", self.course)
        self.assertIn("Installments are not listed", reply)
        self.assertIn("bank transfer", reply)

    def test_inactive_intake_does_not_claim_to_be_next(self):
        expired = get_course("sw-testing-july-2026")
        reply = _deterministic_reply("When is the course?", expired)
        self.assertIn("was scheduled", reply)
        self.assertIn("next available dates", reply)

    def test_model_markdown_is_sanitized_for_whatsapp(self):
        reply = _sanitize_reply("## Course Breakdown\n\n**Fees:** RM7,500")
        self.assertEqual("Course Breakdown\n\nFees: RM7,500", reply)

    def test_catalog_question_uses_all_matching_courses(self):
        reply = _deterministic_reply("Any embedded courses in August?", self.course, catalog=True)
        self.assertIn("Embedded Linux System Internals", reply)
        self.assertIn("Embedded Linux with Yocto", reply)
        self.assertNotIn("30–31 July", reply)

        priced = _deterministic_reply(
            "What other courses are available and how much do they cost?",
            self.course,
            catalog=True,
        )
        self.assertIn("RM", priced)

    def test_trainer_catalog_question_does_not_fall_back_to_schedule_catalog(self):
        reply = _deterministic_reply(
            "Any other course where I can know the trainer profile?",
            self.course,
            catalog=True,
        )
        self.assertIn("Embedded Linux System Internals", reply)
        self.assertIn("18+ years", reply)
        self.assertNotIn("currently scheduled", reply)

    def test_embedded_c_trainer_never_becomes_software_testing_trainer(self):
        embedded_c = get_course("embedded-c-july-2026")
        reply = _deterministic_reply("How about the trainer?", embedded_c)
        self.assertIn("full profile", reply)
        self.assertIn("experienced embedded Linux consultant", reply)
        self.assertNotIn("software testing", reply.lower())

    def test_trainer_correction_uses_selected_course_fact(self):
        embedded_c = get_course("embedded-c-july-2026")
        reply = _deterministic_reply(
            "We are talking about Embedded C, but you discussed a software testing trainer.",
            embedded_c,
        )
        self.assertIn("reference was incorrect", reply)
        self.assertIn("experienced embedded Linux consultant", reply)

    def test_company_contact_details_come_from_shared_knowledge(self):
        reply = _deterministic_reply("What is the office phone and email?", self.course)
        self.assertIn("info@timmins-consulting.com", reply)
        self.assertIn("+60 14-395 3661", reply)

    def test_recent_conversation_excludes_duplicated_current_message(self):
        history = [
            {"direction": "outbound", "body": "Would you like the trainer profile?"},
            {"direction": "inbound", "body": "Yes"},
        ]
        rendered = _recent_conversation(history, "Yes")
        self.assertIn("trainer profile", rendered)
        self.assertNotIn("Customer: Yes", rendered)


if __name__ == "__main__":
    unittest.main()
