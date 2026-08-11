from __future__ import annotations

import unittest

from scripts.local_chat import LocalChatSession


class LocalChatSessionTests(unittest.TestCase):
    def test_real_failure_sequence_keeps_context_and_grounding(self):
        chat = LocalChatSession("embedded-c-july-2026")

        card = chat.reply("Can I pay with the credit card?")
        self.assertIn("not listed as a confirmed option", card)

        chat.reply("Do you accept installments?")
        correction = chat.reply("I didn’t ask about cancellation?")
        self.assertIn("we were discussing installments", correction)
        self.assertIn("bank transfer", correction)

        catalog = chat.reply("Do you have other embedded courses?")
        self.assertIn("Embedded Linux System Internals", catalog)
        self.assertNotIn("Firmware Development", catalog)
        self.assertNotIn("Embedded C Programming and GDB Debugging", catalog)

        deleted = chat.reply("Why deleted?")
        self.assertIn("WhatsApp message", deleted)
        self.assertNotIn("course was deleted", deleted.lower())

        negative = chat.reply("Not at all")
        self.assertIn("won't assume", negative)
        self.assertNotIn("upcoming training program", negative)

    def test_course_can_be_discovered_from_customer_message(self):
        chat = LocalChatSession()
        reply = chat.reply("What is the fee for Embedded Linux System Internals?")
        self.assertIn("RM7,500", reply)
        self.assertEqual("embedded-linux-internals-aug-2026", chat.course.slug)


if __name__ == "__main__":
    unittest.main()
