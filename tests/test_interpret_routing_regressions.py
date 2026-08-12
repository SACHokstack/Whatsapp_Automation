"""Routing regressions found in live WhatsApp testing.

Both bugs had the same shape: a question was mapped onto a nearby intent and answered
confidently from an exact fact, instead of being answered on its own terms or escalated.
These exercise the deterministic layer only, so they need no API key and no network.
"""

import unittest

from services.interpret import deterministic_plan

YOCTO = "embedded-linux-yocto-aug-2026"


def _intents(message, slug=YOCTO):
    plan = deterministic_plan(message, current_slug=slug)
    return None if plan is None else [r.intent for r in plan.requests]


class FactAboutANamedCourseIsNotTheCatalogue(unittest.TestCase):
    """"what is the fee for the yocto course" returned the whole course list.

    The catalogue rule matched the literal phrase "yocto course" (its course-noun pattern
    includes the singular), and being a deterministic rule it short-circuited before the
    interpreter ever saw the message.
    """

    def test_fee_for_a_named_course_is_fees_not_catalog(self):
        self.assertEqual(["FEES"], _intents("what is the fee for the yocto course?"))
        self.assertEqual(["FEES"], _intents("how much is the yocto course"))

    def test_other_facts_about_a_named_course_survive_the_same_trap(self):
        self.assertEqual(["SCHEDULE"], _intents("when is the embedded linux course"))
        self.assertEqual(["VENUE"], _intents("where is the yocto course held"))
        self.assertEqual(["DURATION"], _intents("how long is the yocto course"))

    def test_genuine_catalogue_questions_still_route_to_catalog(self):
        for message in (
            "what courses are there",
            "what other courses do you offer",
            "do you have embedded linux courses",
            "any other trainings available",
        ):
            with self.subTest(message=message):
                self.assertEqual(["CATALOG"], _intents(message))


class VenueFactsDoNotAnswerFacilityQuestions(unittest.TestCase):
    """"is there parking" was answered with the venue address.

    Parking, food, wifi and similar are not the venue fact. The deterministic layer must
    not claim them; they belong to the interpreter, which routes them to OPERATIONS so an
    unconfirmed detail reaches the consultant instead of being answered with the address.
    """

    def test_facility_questions_are_not_claimed_deterministically(self):
        for message in (
            "is there parking",
            "is parking facility available",
            "will there be snacks",
            "is there wifi at the venue",
            "is the food halal",
        ):
            with self.subTest(message=message):
                plan = deterministic_plan(message, current_slug=YOCTO)
                if plan is not None:
                    self.assertNotIn(
                        "VENUE",
                        [r.intent for r in plan.requests],
                        f"{message!r} must not be answered with the venue address",
                    )

    def test_a_real_venue_question_is_still_answered(self):
        self.assertEqual(["VENUE"], _intents("where is it held"))
        self.assertEqual(["VENUE"], _intents("what is the venue"))
        self.assertEqual(["VENUE"], _intents("where will the training take place"))


class UngroundedAnswersReachTheConsultant(unittest.TestCase):
    def test_warm_fallback_names_the_consultant(self):
        """The reply an unanswerable question ends at: RAG finds no evidence -> not_found
        -> runtime returns warm_fallback."""
        from services.fallbacks import warm_fallback

        reply = warm_fallback(None)
        self.assertIn("don't have that specific detail confirmed", reply)
        self.assertIn("consultant", reply.lower())


if __name__ == "__main__":
    unittest.main()
