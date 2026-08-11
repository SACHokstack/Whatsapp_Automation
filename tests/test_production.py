from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from unittest.mock import patch

from services.conversation_controller import decide_reply
from services.course_loader import get_course
from services.durable_queue import claim_event, complete_event, enqueue_event
from services.response_guard import validate_reply
from services.structured_facts import exact_answer


class ConversationControllerTests(unittest.TestCase):
    def setUp(self):
        self.course = get_course("embedded-linux-yocto-aug-2026")

    def test_exact_commercial_fact_bypasses_rag(self):
        decision = decide_reply("What is the fee?", course=self.course)
        self.assertEqual("exact", decision.route)
        self.assertEqual("FEES", decision.intent)

    def test_descriptive_question_uses_rag(self):
        decision = decide_reply("What will I learn?", course=self.course)
        self.assertEqual("rag", decision.route)

    def test_content_and_fee_question_creates_mixed_plan(self):
        decision = decide_reply(
            "Can you share with me the syllabus and the price",
            course=self.course,
        )
        self.assertEqual("mixed", decision.route)
        self.assertEqual("COURSE_CONTENT_WITH_FEES", decision.intent)

    def test_unknown_question_with_course_routes_to_rag(self):
        # When a course is already selected, unclear messages go to RAG rather than
        # blocking the user with a clarification — course context is enough to attempt an answer.
        decision = decide_reply("Tell me something surprising", course=self.course)
        self.assertEqual("rag", decision.route)

    def test_course_intro_uses_concept_groups(self):
        messages = (
            "i came here thorugh add what is this",
            "I received this on Facebook, can you brief me",
            "saw this on Meta what is it",
            "what are you offering for this training",
        )
        for message in messages:
            with self.subTest(message=message):
                decision = decide_reply(message, course=self.course)
                self.assertEqual("rag", decision.route)
                self.assertEqual("COURSE_INTRO", decision.intent)

    def test_grouped_policy_and_action_intents(self):
        grouped_cases = (
            ("how do I join the class", "ENROLLMENT"),
            ("company claim possible?", "HRDC"),
            ("do we get food and materials", "CERTIFICATION"),
            ("is this hybrid or physical", "ONLINE"),
        )
        for message, expected_intent in grouped_cases:
            with self.subTest(message=message):
                decision = decide_reply(message, course=self.course)
                self.assertEqual(expected_intent, decision.intent)

    def test_structured_fee_answer_uses_selected_course(self):
        answer = exact_answer("FEES", self.course, message="fee")
        self.assertIn("RM7,500", answer)
        self.assertIn(self.course.name, answer)

    def test_multi_part_commercial_question_answers_every_part(self):
        answer = exact_answer(
            "FEES",
            self.course,
            message="What are the fee, date, venue and duration?",
        )
        self.assertIn("RM7,500", answer)
        self.assertIn("10–14 August 2026", answer)
        self.assertIn("Timmins Training Center, Penang", answer)
        self.assertIn("5-day", answer)

    def test_trainer_question_uses_exact_course_fact(self):
        course = get_course("embedded-c-july-2026")
        decision = decide_reply("Who is the trainer?", course=course)
        answer = exact_answer(decision.intent, course, message="Who is the trainer?")
        self.assertEqual("exact", decision.route)
        self.assertIn("experienced embedded Linux consultant", answer)
        self.assertNotIn("software testing", answer.lower())

    def test_company_question_is_structured(self):
        decision = decide_reply("Who is Timmins?", course=None)
        answer = exact_answer(decision.intent, None, message="Who is Timmins?")
        self.assertEqual("COMPANY", decision.intent)
        self.assertIn("Malaysia-based", answer)


class ResponseGuardTests(unittest.TestCase):
    def setUp(self):
        self.course = get_course("embedded-c-july-2026")

    def test_rejects_invented_fee(self):
        # Retrieval now spans all courses, so any real course fee is allowed;
        # the guard only rejects figures that match no active course at all.
        result = validate_reply("The fee is RM9,999.", course=self.course)
        self.assertFalse(result.valid)

    def test_accepts_other_active_course_fee(self):
        # A fee from a different active course is legitimate under global retrieval.
        result = validate_reply("The fee is RM7,500 per participant.", course=self.course)
        self.assertTrue(result.valid)

    def test_rejects_internal_language(self):
        result = validate_reply(
            "According to the knowledge base, the fee is RM3,200.", course=self.course
        )
        self.assertFalse(result.valid)

    def test_accepts_grounded_course_fee(self):
        result = validate_reply("The fee is RM3,200 per participant.", course=self.course)
        self.assertTrue(result.valid)


class DurableQueueTests(unittest.TestCase):
    def test_event_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "queue.db")
            with patch.dict(
                os.environ,
                {"WHATSAPP_DB_PATH": path, "DATABASE_URL": ""},
                clear=False,
            ):
                payload = {
                    "messages": [{"id": "wamid.queue-1", "from": "60111111111", "type": "text"}]
                }
                self.assertTrue(enqueue_event("message:wamid.queue-1", "60111111111", payload))
                self.assertFalse(enqueue_event("message:wamid.queue-1", "60111111111", payload))
                event = claim_event()
                self.assertEqual("message:wamid.queue-1", event["event_id"])
                self.assertEqual(payload, event["payload"])
                complete_event(event["event_id"])
                self.assertIsNone(claim_event())


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL integration URL not configured")
class PostgreSQLStoreTests(unittest.TestCase):
    def test_lead_and_message_round_trip(self):
        from services import postgres_store

        phone = "test-" + uuid.uuid4().hex
        with patch.dict(os.environ, {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}):
            postgres_store.init_db()
            postgres_store.upsert_lead(
                phone,
                status="ENGAGED",
                course="embedded-c-july-2026",
                last_intent="FEES",
            )
            postgres_store.add_message(
                phone,
                direction="inbound",
                body="What is the fee?",
                message_id="wamid." + uuid.uuid4().hex,
            )
            lead = postgres_store.get_lead(phone)
            history = postgres_store.get_messages(phone)
            self.assertEqual("ENGAGED", lead["status"])
            self.assertEqual("embedded-c-july-2026", lead["course"])
            self.assertEqual("What is the fee?", history[-1]["body"])
            with postgres_store.get_connection() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM messages WHERE phone = %s", (phone,))
                cursor.execute("DELETE FROM leads WHERE phone = %s", (phone,))


if __name__ == "__main__":
    unittest.main()
