from __future__ import annotations

import unittest

import main
from services.conversation_controller import decide_reply
from services.course_loader import get_course


class CustomerTranscriptRegressionTests(unittest.TestCase):
    """Regression coverage for the failed WhatsApp conversation from 21 July 2026."""

    def setUp(self):
        self.course = get_course("embedded-c-july-2026")
        self.history: list[dict] = []

    def ask(self, message: str, *, catalog: bool = False) -> str:
        reply = main._faq_reply(
            message,
            course=self.course,
            history=self.history,
            catalog=catalog,
        )
        self.history.extend(
            [
                {"direction": "inbound", "body": message},
                {"direction": "outbound", "body": reply},
            ]
        )
        return reply

    def test_payment_correction_and_typo_keep_the_right_topic(self):
        card = self.ask("Can I pay with the credit card?")
        self.assertIn("not listed as a confirmed option", card)
        self.assertIn("Bank transfer", card)
        self.assertNotIn("don't accept credit card", card.lower())

        installments = self.ask("Do you accept installments?")
        self.assertIn("not a standard confirmed option", installments)
        self.assertIn("can't promise", installments)

        challenge = self.ask("I checked their website and no such claims")
        self.assertIn("overstated", challenge)
        self.assertIn("bank transfer", challenge)
        self.assertNotIn("cancellation", challenge.lower())

        correction = self.ask("I didn’t ask about cancellation?")
        self.assertIn("installments", correction.lower())
        self.assertIn("bank transfer", correction.lower())
        self.assertNotIn("course outline", correction.lower())

        lost_context = self.ask("You don’t know what ask about it?")
        self.assertIn("installments", lost_context.lower())
        self.assertIn("bank transfer", lost_context.lower())

        typo = self.ask("I folded about payment installation")
        self.assertIn("Installments are not a standard confirmed option", typo)
        self.assertNotIn("cancellation", typo.lower())

    def test_catalog_only_lists_real_configured_courses(self):
        other = self.ask("Do you have other embedded courses?", catalog=True)
        self.assertIn("Embedded Linux System Internals", other)
        self.assertNotIn("Embedded C Programming and GDB Debugging", other)
        for invented in (
            "Firmware Development",
            "Device Driver Development",
            "Advanced C Programming",
            "GDB Debugging Techniques",
        ):
            self.assertNotIn(invented, other)

        full_list = self.ask("Send me the list of courses", catalog=True)
        self.assertIn("Embedded C Programming and GDB Debugging", full_list)
        # Python Automation is deactivated (not in NEW_Courses) — must not be listed.
        self.assertNotIn("Python Automation for Engineers", full_list)
        self.assertNotIn("Firmware Development", full_list)

        linux = self.ask("Can you share the embedded linux course names?", catalog=True)
        # Embedded Linux Debugging is deactivated (not in NEW_Courses) — must not be listed.
        self.assertNotIn("Embedded Linux Debugging and Performance Optimization", linux)
        self.assertIn("Embedded Linux System Internals", linux)
        self.assertIn("Embedded Linux with Yocto", linux)
        self.assertNotIn("Embedded C Programming", linux)
        self.assertNotIn("Firmware Development", linux)

    def test_deleted_message_never_becomes_a_deleted_course_claim(self):
        decision = decide_reply("Why deleted?", course=self.course, history=self.history)
        self.assertEqual("context", decision.route)
        # The previous outbound list contains several courses. That memory must not
        # override this explicit conversation-repair intent.
        reply = self.ask("Why deleted?", catalog=True)
        self.assertIn("WhatsApp message", reply)
        self.assertIn("shouldn't assume", reply)
        self.assertNotIn("course details", reply.lower())

    def test_negative_acknowledgements_do_not_restart_the_pitch(self):
        for message in ("Nope", "Not at all"):
            reply, updates = main._process_conversation(
                message,
                {"status": "ENGAGED", "conversation_state": ""},
                course=self.course,
            )
            self.assertIn("won't assume", reply)
            self.assertNotIn("Embedded C", reply)
            self.assertNotIn("upcoming training program", reply)
            self.assertIsNone(updates)

    def test_overdue_followup_is_escalated(self):
        self.assertEqual(
            "Follow-up overdue",
            main._human_escalation_reason("No one has talked to me yet"),
        )

    def test_catalog_phrasing_is_detected_without_manual_flag(self):
        for message in (
            "Send me the list of courses",
            "Can you share the embedded linux course names?",
            "Do you have other embedded courses?",
            "what are the courses availablel",
            "what courses are availabel",
        ):
            self.assertTrue(main._is_course_catalog_question(message))


if __name__ == "__main__":
    unittest.main()
