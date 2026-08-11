from __future__ import annotations

import unittest

import main
from services.course_loader import get_course


class MultiTurnConversationTests(unittest.TestCase):
    def test_payment_challenge_keeps_payment_context(self):
        course = get_course("embedded-c-july-2026")
        first = main._faq_reply("Do you accept installments?", course=course, history=[])
        history = [
            {"direction": "inbound", "body": "Do you accept installments?"},
            {"direction": "outbound", "body": first},
        ]
        second = main._faq_reply(
            "I checked the website and no such claims", course=course, history=history
        )
        self.assertIn("overstated", second)
        self.assertIn("bank transfer", second)
        self.assertNotIn("cancellation", second.lower())

    def test_catalog_followup_does_not_guess(self):
        course = get_course("embedded-c-july-2026")
        first = main._faq_reply(
            "Do you have other embedded courses?", course=course, history=[], catalog=True
        )
        history = [{"direction": "outbound", "body": first}]
        second = main._faq_reply(
            "How much for this course?", course=course, history=history, catalog=True
        )
        self.assertIn("Which course", second)
        self.assertNotIn("RM", second)

    def test_explicit_course_switch_changes_exact_facts(self):
        selected = main._resolve_course(
            {"course": "embedded-c-july-2026"},
            "I mean Embedded Linux System Internals",
        )
        reply = main._faq_reply("What is the fee?", course=selected, history=[])
        self.assertIn("Embedded Linux System Internals", reply)
        self.assertIn("RM7,500", reply)
        self.assertNotIn("RM3,200", reply)

    def test_stop_state_is_not_treated_as_another_prompt(self):
        reply, updates = main._process_conversation("Let's stop it", {"status": "ENGAGED"})
        lead = {**updates}
        self.assertIn("stop", reply.lower())
        self.assertTrue(main._automation_paused(lead))


if __name__ == "__main__":
    unittest.main()
