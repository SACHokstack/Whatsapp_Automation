"""The general-enquiry path: a non-technical caller who names no course.

Previously any course-scoped ask with no course selected returned the same line —
"Which course are you interested in?" — with no state, so the follow-up ("software
testing") was not read as a choice and the conversation dead-ended.
"""

import unittest

import main
from services.interpret import Request, TurnPlan


def _plan(intent, mode="exact"):
    return TurnPlan("none", False, (Request(intent, None, mode, intent.lower()),), 0.99, "test")


class CompanyLevelAnswersNeedNoCourse(unittest.TestCase):
    def test_generic_hrdc_question_is_answered_without_picking_a_course(self):
        """HRDC registration is company-level: every course carries it, so asking
        "which course?" for it was a dead-end for a question we can already answer."""
        reply, updates = main._process_conversation(
            "is it hrdc claimable?", {"status": "CONTACTED"}, plan=_plan("HRDC")
        )
        # None means "no lifecycle answer" — the turn falls through to the normal
        # router, which answers it from the company-level fact.
        self.assertIsNone(reply)

    def test_fees_still_need_a_course(self):
        reply, _ = main._process_conversation(
            "what is the fee?", {"status": "CONTACTED"}, plan=_plan("FEES")
        )
        self.assertIn("which course", reply.lower())


class CoursePickerFollowUp(unittest.TestCase):
    def _pick(self, answer_text, pending="FEES"):
        lead = {
            "status": "CONTACTED",
            "conversation_state": f"ASKING_COURSE_SELECT:{pending}",
        }
        return main._process_conversation(answer_text, lead)

    def test_naming_the_course_in_words_selects_it_and_answers(self):
        reply, updates = self._pick("software testing")
        self.assertEqual("sw-testing-aug-2026", updates["course"])
        self.assertEqual("", updates["conversation_state"])  # slot cleared
        self.assertIn("RM", reply)  # the fee they originally asked for

    def test_replying_with_the_number_selects_it(self):
        reply, updates = self._pick("1")
        self.assertTrue(updates["course"])
        self.assertEqual("", updates["conversation_state"])

    def test_an_unrecognisable_reply_re_asks_rather_than_guessing(self):
        reply, updates = self._pick("the blue one")
        self.assertIsNone(updates)
        self.assertIn("which course", reply.lower())

    def test_a_pending_course_choice_is_reported_as_the_open_slot(self):
        slot = main._pending_slot({"conversation_state": "ASKING_COURSE_SELECT:FEES"})
        self.assertIn("course", slot)


class QualificationToleratesOutOfOrderAnswers(unittest.TestCase):
    def test_funding_volunteered_during_the_tools_question_is_recorded(self):
        """"My company will pay for it" was answered with payment terms and recorded
        nothing, stalling the flow on ASKING_TECHNOLOGIES forever."""
        lead = {"status": "ENGAGED", "conversation_state": "ASKING_TECHNOLOGIES"}
        reply, updates = main._process_conversation(
            "my company will pay for it", lead, plan=_plan("PAYMENT")
        )
        self.assertEqual("Company/HRDC", updates["funding_path"])
        # The flow stays on the unanswered slot instead of skipping it
        self.assertNotIn("conversation_state", updates)
        self.assertIn("tools", reply.lower())

    def test_a_funding_question_is_still_answered_not_recorded(self):
        lead = {"status": "ENGAGED", "conversation_state": "ASKING_TECHNOLOGIES"}
        reply, updates = main._process_conversation(
            "is it hrdc claimable?", lead, plan=_plan("HRDC")
        )
        self.assertIsNone(updates)

    def test_experience_years_stores_the_number_not_the_sentence(self):
        lead = {"status": "ENGAGED", "conversation_state": "ASKING_EXPERIENCE_YEARS"}
        _, updates = main._process_conversation(
            "I am a QA engineer with 3 years experience", lead
        )
        self.assertEqual("3", updates["experience_years"])
        # the descriptive background is kept rather than discarded
        self.assertIn("QA engineer", updates["experience"])


if __name__ == "__main__":
    unittest.main()


class PickerIsInterruptible(unittest.TestCase):
    """The picker must not hold the conversation hostage.

    Asking about fees, seeing the list, then pivoting to "how do we pay?" used to return
    "Sorry, I didn't catch which course that was" and re-show the fee picker on every
    later turn — with no escape except naming a course the customer never wanted to name.
    """

    def _pending(self, intent="FEES"):
        return {"status": "CONTACTED", "conversation_state": f"ASKING_COURSE_SELECT:{intent}"}

    def test_pivot_to_a_global_intent_is_answered_not_re_prompted(self):
        reply, updates = main._process_conversation(
            "how do we pay?", self._pending(), plan=_plan("PAYMENT")
        )
        self.assertIsNone(reply)  # routed onward and answered globally
        self.assertEqual("", updates["conversation_state"])  # trap released

    def test_pivot_to_cancellation_is_also_released(self):
        reply, updates = main._process_conversation(
            "can we cancel and refund?", self._pending(), plan=_plan("CANCELLATION")
        )
        self.assertIsNone(reply)
        self.assertEqual("", updates["conversation_state"])

    def test_pivot_to_a_different_scoped_question_repoints_the_picker(self):
        reply, updates = main._process_conversation(
            "when does it start?", self._pending("FEES"), plan=_plan("SCHEDULE")
        )
        self.assertIn("which course", reply.lower())
        # the picker now tracks the NEW question, not the stale one
        self.assertEqual("ASKING_COURSE_SELECT:SCHEDULE", updates["conversation_state"])

    def test_a_mistyped_course_name_still_gets_re_asked(self):
        reply, updates = main._process_conversation(
            "the blue one", self._pending(), plan=None
        )
        self.assertIn("didn't catch", reply)
        self.assertIsNone(updates)

    def test_a_valid_selection_is_unaffected(self):
        reply, updates = main._process_conversation(
            "software testing", self._pending(), plan=None
        )
        self.assertEqual("sw-testing-aug-2026", updates["course"])
        self.assertIn("RM", reply)
