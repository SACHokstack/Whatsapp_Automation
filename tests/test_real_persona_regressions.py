from __future__ import annotations

import unittest

import main
from services.course_loader import get_course
from services.fallbacks import warm_fallback
from services.interpret import deterministic_plan


class RealPersonaRegressionTests(unittest.TestCase):
    """Acceptance tests written from the prospect's side of the conversation."""

    def reply(self, message: str, slug: str) -> tuple[object, str]:
        course = get_course(slug)
        self.assertIsNotNone(course)
        plan = deterministic_plan(message, current_slug=slug)
        self.assertIsNotNone(plan, message)
        response = main._reply_from_plan(
            message,
            course,
            plan,
            lead={"status": "ENGAGED", "course": slug},
            history=[],
        )
        self.assertNotIn("don't have enough verified", response.lower())
        return plan, response

    def test_confused_prospect_gets_orientation_and_beginner_reassurance(self):
        cases = (
            (
                "my son say i should ask about the computer programming course",
                "COURSE_INTRO",
                ("Embedded C", "2-day", "which would help"),
            ),
            (
                "is this the C one or the linux one i forgot",
                "COURSE_CONFIRMATION",
                ("C course", "not an Embedded Linux"),
            ),
            (
                "i dont know what is gdb is it hard for old people",
                "BEGINNER_FIT",
                ("GNU Debugger", "taught from scratch", "not your age"),
            ),
        )
        for message, intent, expected in cases:
            with self.subTest(message=message):
                plan, response = self.reply(message, "embedded-c-july-2026")
                self.assertEqual([intent], [request.intent for request in plan.requests])
                for phrase in expected:
                    self.assertIn(phrase, response)

    def test_discount_questions_use_documented_rates_and_never_handoff(self):
        cases = (
            ("aiyoo expensive no discount ah", "RM3,040", "RM2,944"),
            ("any discount if my team of 4 joins", "team of 4", "RM2,944"),
            ("do you have a group booking rate", "group pricing", "RM2,944"),
        )
        for message, first, second in cases:
            with self.subTest(message=message):
                self.assertIsNone(main._human_escalation_reason(message))
                plan, response = self.reply(message, "embedded-c-july-2026")
                self.assertEqual(["DISCOUNT"], [request.intent for request in plan.requests])
                self.assertIn(first, response)
                self.assertIn(second, response)
                self.assertNotIn("reach out to you", response)

    def test_value_and_comparison_are_grounded_buyer_answers(self):
        _, youtube = self.reply(
            "why should i take this instead of just watching youtube",
            "embedded-linux-internals-aug-2026",
        )
        self.assertIn("YouTube can be useful", youtube)
        self.assertIn("Buildroot", youtube)
        self.assertNotIn("Class sizes", youtube)

        _, worth = self.reply(
            "is it really worth 7500, honestly",
            "embedded-linux-internals-aug-2026",
        )
        self.assertIn("practical outcomes", worth)
        self.assertIn("RM7,500", worth)

        plan, comparison = self.reply(
            "how is this different from the embedded c course",
            "embedded-linux-internals-aug-2026",
        )
        self.assertEqual("embedded-linux-internals-aug-2026", plan.requests[0].course_slug)
        self.assertIn("Embedded Linux System Internals", comparison)
        self.assertIn("Embedded C Programming and GDB Debugging", comparison)
        self.assertIn("starting level", comparison)

    def test_comparison_reference_does_not_switch_the_active_course(self):
        current = get_course("embedded-linux-internals-aug-2026")
        resolved = main._resolve_course(
            {"course": current.slug},
            "how is this different from the embedded c course",
        )
        self.assertEqual(current.slug, resolved.slug)

    def test_emotional_and_repetition_repairs_do_not_repeat_facts(self):
        cases = (
            ("you are a robot right", "BOT_IDENTITY", "automated course assistant"),
            ("this is useless", "FRUSTRATION", "right to be frustrated"),
            (
                "i already asked you the price why you repeat",
                "REPETITION_REPAIR",
                "I won't repeat it",
            ),
        )
        for message, intent, expected in cases:
            with self.subTest(message=message):
                plan, response = self.reply(message, "embedded-c-july-2026")
                self.assertEqual([intent], [request.intent for request in plan.requests])
                self.assertIn(expected, response)
        self.assertNotIn(
            "RM",
            self.reply("i already asked you the price why you repeat", "embedded-c-july-2026")[1],
        )

    def test_procurement_phrasing_cannot_become_venue_or_batch_size(self):
        plan, replacement = self.reply(
            "can i replace a participant last minute",
            "sw-testing-aug-2026",
        )
        self.assertEqual(["PARTICIPANT_REPLACEMENT"], [r.intent for r in plan.requests])
        self.assertIn("generally allowed", replacement)
        self.assertNotIn("Ibis", replacement)
        self.assertNotIn("Class sizes", replacement)

        plan, documents = self.reply(
            "what documents needed for hrdc grant",
            "sw-testing-aug-2026",
        )
        self.assertEqual(["HRDC_DOCUMENTS"], [r.intent for r in plan.requests])
        self.assertIn("Official quotation", documents)
        self.assertIn("course outline", documents)
        self.assertIn("e-TRiS", documents)

    def test_free_learning_request_cannot_become_trainer_profile(self):
        plan, response = self.reply("teach me embedded c for free", "embedded-c-july-2026")
        self.assertEqual(["FREE_TUTORING"], [request.intent for request in plan.requests])
        self.assertIn("can't deliver the full training", response)
        self.assertNotIn("trainer profile", response.lower())

        unrelated = deterministic_plan(
            "do you sell laptops", current_slug="embedded-c-july-2026"
        )
        self.assertTrue(
            unrelated is None or all(request.intent != "TRAINER" for request in unrelated.requests)
        )

    def test_compound_variants_keep_every_independent_ask(self):
        cases = (
            ("what is covered and any group discount for 4 people", {"COURSE_CONTENT", "DISCOUNT"}),
            ("is it worth it and what is the team of 4 rate", {"COURSE_VALUE", "DISCOUNT"}),
            (
                "what hrdc documents do we need and can we replace a participant",
                {"HRDC_DOCUMENTS", "PARTICIPANT_REPLACEMENT"},
            ),
            ("why is the fee so expensive and when is it", {"COURSE_VALUE", "SCHEDULE"}),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                plan = deterministic_plan(message, current_slug="embedded-c-july-2026")
                self.assertIsNotNone(plan)
                self.assertEqual(expected, {request.intent for request in plan.requests})

    def test_unanswered_copy_always_redirects_warmly(self):
        response = warm_fallback(get_course("embedded-c-july-2026"))
        self.assertIn("don't have that specific detail confirmed", response)
        self.assertIn("consultant", response)
        # It gives the consultant contact and stops — no "I can help with a, b, c" tail.
        company = main.load_policies()["company"]
        self.assertIn(company["phone"], response)
        self.assertIn(company["email"], response)
        self.assertNotIn("In the meantime", response)
        self.assertNotIn("enough verified information", response)


if __name__ == "__main__":
    unittest.main()
