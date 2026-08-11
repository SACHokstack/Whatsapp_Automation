"""Unit tests for the single understanding step (services/interpret.understand).

Groq is mocked — no network. These assert the structured TurnPlan, not model quality.
"""

from __future__ import annotations

import json
import os
import types
import unittest
from unittest.mock import Mock, patch

from services.course_loader import get_active_courses
from services.interpret import deterministic_plan, understand

_ENV = {
    "USE_INTERPRETER": "true",
    "INTERPRETER_PROVIDER": "groq",
    "GROQ_API_KEY": "test-key",
}


def _fake_groq(content: str | None = None, raise_exc: Exception | None = None):
    class FakeCompletions:
        def create(self, **kwargs):
            if raise_exc is not None:
                raise raise_exc
            message = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    class FakeGroq:
        def __init__(self, *args, **kwargs):
            self.chat = types.SimpleNamespace(completions=FakeCompletions())

    return FakeGroq


def _run(payload: dict, **kwargs):
    with (
        patch.dict(os.environ, _ENV, clear=False),
        patch("groq.Groq", _fake_groq(json.dumps(payload))),
    ):
        return understand(**kwargs)


class TestUnderstand(unittest.TestCase):
    def setUp(self):
        self.slugs = [c.slug for c in get_active_courses()]
        self.assertGreaterEqual(len(self.slugs), 2)

    def test_single_exact_fee(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [{"intent": "FEES", "course_slug": self.slugs[0], "mode": "exact"}],
                "confidence": 0.9,
                "reason": "fee",
            },
            message="how much is it",
        )
        self.assertEqual(plan.control, "none")
        self.assertEqual(len(plan.requests), 1)
        self.assertEqual(plan.requests[0].intent, "FEES")
        self.assertEqual(plan.requests[0].mode, "exact")

    def test_compound_is_decomposed(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [
                    {"intent": "COURSE_CONTENT", "course_slug": self.slugs[0], "mode": "retrieve"},
                    {"intent": "FEES", "course_slug": self.slugs[0], "mode": "exact"},
                ],
                "confidence": 0.9,
                "reason": "syllabus + price",
            },
            message="what is the syllabus and the price",
        )
        self.assertEqual(len(plan.requests), 2)
        modes = {r.intent: r.mode for r in plan.requests}
        self.assertEqual(modes["COURSE_CONTENT"], "retrieve")
        self.assertEqual(modes["FEES"], "exact")

    def test_day_by_day_is_retrieve_not_duration(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [
                    {"intent": "COURSE_CONTENT", "course_slug": self.slugs[0], "mode": "retrieve"}
                ],
                "confidence": 0.8,
                "reason": "curriculum",
            },
            message="can i know the day by day breakdown",
        )
        self.assertEqual(plan.requests[0].intent, "COURSE_CONTENT")
        self.assertEqual(plan.requests[0].mode, "retrieve")

    def test_session_count_is_course_content_not_duration(self):
        # "how many sessions" must retrieve the session breakdown, not fall back to the LLM
        # and get labelled DURATION ("2-day course"). "how many days" stays DURATION.
        plan = deterministic_plan("how many sessions are there", current_slug="embedded-c-july-2026")
        self.assertIsNotNone(plan)
        self.assertEqual("COURSE_CONTENT", plan.requests[0].intent)
        self.assertEqual("retrieve", plan.requests[0].mode)

        days = deterministic_plan("how many days is it", current_slug="embedded-c-july-2026")
        self.assertEqual("DURATION", days.requests[0].intent)

    def test_learn_goal_is_not_forced_to_course_content(self):
        # "what will I learn" is about the course -> COURSE_CONTENT. But "I want to learn
        # <off-catalogue topic>" is a goal; it must NOT be force-fit to the current course's
        # content (which dumped the wrong syllabus) — leave it to the interpreter to route.
        content = deterministic_plan("what will i learn", current_slug="embedded-linux-internals-aug-2026")
        self.assertEqual("COURSE_CONTENT", content.requests[0].intent)
        goal = deterministic_plan(
            "i want to learn mobile app development", current_slug="embedded-linux-internals-aug-2026"
        )
        self.assertIsNone(goal)

    def test_price_superlatives_route_to_recommendation(self):
        for q in ("which is the cheapest", "which is the most expensive", "whats the priciest course"):
            plan = deterministic_plan(q, current_slug=None)
            self.assertIsNotNone(plan, q)
            self.assertEqual(["RECOMMENDATION"], [r.intent for r in plan.requests], q)

    def test_mixed_background_statement_routes_to_recommendation(self):
        # "I know C, C++ and Selenium" spans testing + systems -> recommender (both paths).
        plan = deterministic_plan("i know c, cpp and selenium", current_slug=None)
        self.assertEqual(["RECOMMENDATION"], [r.intent for r in plan.requests])
        # A single-topic or course-specific statement must NOT be hijacked by the rule.
        self.assertIsNone(deterministic_plan("i know selenium", current_slug=None))
        catalog_plan = deterministic_plan("i know python, do you have a python course", current_slug=None)
        self.assertNotIn("RECOMMENDATION", [r.intent for r in catalog_plan.requests])

    def test_beginner_fit_question_does_not_also_dump_catalog(self):
        # "which course is beginner friendly" is a fit question, not a catalog browse —
        # it must not append a spurious CATALOG that dumps the whole course list.
        plan = deterministic_plan("which course is beginner friendly", current_slug=None)
        self.assertEqual(["BEGINNER_FIT"], [r.intent for r in plan.requests])

    def test_deterministic_day_one_normalizes_ordinal(self):
        plan = deterministic_plan("whiat is covered on day one", current_slug=self.slugs[0])
        self.assertIsNotNone(plan)
        self.assertEqual("COURSE_CONTENT", plan.requests[0].intent)
        self.assertIn("day 1", plan.requests[0].query)
        self.assertFalse(plan.answers_pending_slot)

    def test_cross_course_trainer_question_is_not_split_into_catalog_and_venue(self):
        plan = deterministic_plan(
            "Any other course where I can know the trainer's profile?",
            current_slug="sw-testing-aug-2026",
        )
        self.assertIsNotNone(plan)
        self.assertEqual(["TRAINER_CATALOG"], [request.intent for request in plan.requests])
        self.assertIsNone(plan.requests[0].course_slug)

        compound = deterministic_plan(
            "Which other course has a trainer profile, where is it held, and how much?",
            current_slug="sw-testing-aug-2026",
        )
        self.assertEqual(["TRAINER_CATALOG"], [request.intent for request in compound.requests])

    def test_deterministic_planner_composes_independent_intents_before_routing(self):
        cases = (
            ("What is the fee and when is the course?", {"FEES", "SCHEDULE"}),
            ("When is the course and what is the fee?", {"FEES", "SCHEDULE"}),
            ("What is the price and venue?", {"FEES", "VENUE"}),
            ("Is it HRDC claimable and how much does it cost?", {"FEES", "HRDC"}),
            ("Who is the trainer and what is the fee?", {"TRAINER", "FEES"}),
            ("What are the prerequisites and fees?", {"REQUIREMENTS", "FEES"}),
            (
                "Who is Timmins and how much is the Embedded C course?",
                {"COMPANY", "FEES"},
            ),
            (
                "What happens after registration and is this course HRDC claimable?",
                {"OPERATIONS", "HRDC"},
            ),
            ("When and where is this course held?", {"SCHEDULE", "VENUE"}),
            ("What is the syllabus and how much is it?", {"COURSE_CONTENT", "FEES"}),
            ("How much is it and what will I learn?", {"COURSE_CONTENT", "FEES"}),
            ("What is on day 1 and how much is it?", {"COURSE_CONTENT", "FEES"}),
            ("What will I learn and is it HRDC claimable?", {"COURSE_CONTENT", "HRDC"}),
            (
                "What happens after registration and when is the course?",
                {"OPERATIONS", "SCHEDULE"},
            ),
            (
                "Tell me the fee, venue, dates and HRDC status",
                {"FEES", "SCHEDULE", "VENUE", "HRDC"},
            ),
            ("cn i get teh sylabus and how mch is it", {"COURSE_CONTENT", "FEES"}),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                plan = deterministic_plan(message, current_slug="embedded-c-july-2026")
                self.assertEqual(expected, {request.intent for request in plan.requests})

    def test_catalog_scope_absorbs_requested_price_instead_of_using_current_course(self):
        cases = (
            ("What other courses are available and how much do they cost?", "CATALOG"),
            ("what are the courses availablel", "CATALOG"),
            ("what courses are avaialble", "CATALOG"),
            ("Which other courses are HRDC claimable?", "CATALOG"),
            ("all embedded linux courses", "CATALOG"),
            (
                "Which other trainer course is HRDC claimable and when does it run?",
                "TRAINER_CATALOG",
            ),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                plan = deterministic_plan(message, current_slug="sw-testing-aug-2026")
                self.assertEqual([expected], [request.intent for request in plan.requests])

    def test_client_company_questions_use_the_general_kb(self):
        for message in (
            "What industries does Timmins serve?",
            "Who are some of Timmins' clients?",
            "Is Timmins HRDC registered?",
            "Can Timmins support vendor registration?",
        ):
            with self.subTest(message=message):
                plan = deterministic_plan(message, current_slug=self.slugs[0])
                self.assertEqual("COMPANY", plan.requests[0].intent)
                self.assertEqual("retrieve", plan.requests[0].mode)
                self.assertIsNone(plan.requests[0].course_slug)

    def test_post_registration_question_uses_operations_kb(self):
        plan = deterministic_plan("What happens after registration?", current_slug=self.slugs[0])
        self.assertEqual("OPERATIONS", plan.requests[0].intent)
        self.assertEqual("retrieve", plan.requests[0].mode)
        self.assertIsNone(plan.requests[0].course_slug)

    def test_scam_concern_uses_company_evidence_without_handoff(self):
        plan = deterministic_plan("is this a scam", current_slug=self.slugs[0])
        self.assertEqual("none", plan.control)
        self.assertEqual("COMPANY", plan.requests[0].intent)
        self.assertEqual("retrieve", plan.requests[0].mode)
        self.assertIsNone(plan.requests[0].course_slug)

    def test_teasing_messages_get_boundary_response_without_handoff(self):
        for message in ("are you dummy", "wait are you fat", "you are useless"):
            with self.subTest(message=message):
                plan = deterministic_plan(message, current_slug=self.slugs[0])
                self.assertEqual("none", plan.control)
                self.assertEqual("BOUNDARY", plan.requests[0].intent)

    def test_answers_pending_slot_true(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": True,
                "requests": [{"intent": "UNKNOWN", "course_slug": None, "mode": "retrieve"}],
                "confidence": 0.9,
                "reason": "answer",
            },
            message="3 years",
            pending_slot="how many years of experience they have",
        )
        self.assertTrue(plan.answers_pending_slot)

    def test_answers_pending_slot_false_for_question(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [{"intent": "FEES", "course_slug": self.slugs[0], "mode": "exact"}],
                "confidence": 0.9,
                "reason": "question, not answer",
            },
            message="what is the fee?",
            pending_slot="how many years of experience they have",
        )
        self.assertFalse(plan.answers_pending_slot)

    def test_course_switch_slug(self):
        target = self.slugs[-1]
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [{"intent": "SCHEDULE", "course_slug": target, "mode": "exact"}],
                "confidence": 0.9,
                "reason": "switch",
            },
            message=f"tell me about {target}",
        )
        self.assertEqual(plan.requests[0].course_slug, target)

    def test_llm_cannot_switch_course_when_message_names_none(self):
        # Regression: on a course-less question ("what is the syllubus"), the model
        # non-deterministically tagged the request with a different but valid slug,
        # silently switching courses and abstaining. The plan must anchor to the
        # current course, not the model's unwarranted switch.
        current, other = self.slugs[0], self.slugs[1]
        self.assertNotEqual(current, other)
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [{"intent": "COURSE_CONTENT", "course_slug": other, "mode": "retrieve"}],
                "confidence": 0.8,
                "reason": "syllabus",
            },
            message="what is the syllubus",
            lead={"course": current},
        )
        self.assertEqual(plan.requests[0].course_slug, current)

    def test_message_reference_gate(self):
        from services.interpret import _message_references_slug

        courses = get_active_courses()
        yocto = next((c for c in courses if "yocto" in c.name.lower()), None)
        self.assertIsNotNone(yocto)
        self.assertTrue(_message_references_slug("can i see the yocto outline", yocto.slug, courses))
        self.assertTrue(_message_references_slug(f"about {yocto.slug}", yocto.slug, courses))
        self.assertFalse(_message_references_slug("what is the syllabus", yocto.slug, courses))

    def test_control_human(self):
        plan = _run(
            {
                "control": "human",
                "answers_pending_slot": False,
                "requests": [{"intent": "UNKNOWN", "course_slug": None, "mode": "retrieve"}],
                "confidence": 0.9,
                "reason": "wants human",
            },
            message="i want to call and speak to someone",
        )
        self.assertEqual(plan.control, "human")

    def test_control_stop(self):
        plan = _run(
            {
                "control": "stop",
                "answers_pending_slot": False,
                "requests": [{"intent": "UNKNOWN", "course_slug": None, "mode": "retrieve"}],
                "confidence": 0.9,
                "reason": "exit",
            },
            message="exit",
        )
        self.assertEqual(plan.control, "stop")

    def test_invalid_intent_and_mode_are_sanitised(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [{"intent": "BOGUS", "course_slug": "nope", "mode": "sideways"}],
                "confidence": 0.5,
                "reason": "x",
            },
            message="???",
        )
        self.assertEqual(plan.requests[0].intent, "UNKNOWN")
        self.assertIsNone(plan.requests[0].course_slug)
        self.assertIn(plan.requests[0].mode, {"exact", "retrieve"})

    def test_empty_requests_defaults_to_unknown(self):
        plan = _run(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [],
                "confidence": 0.3,
                "reason": "none",
            },
            message="uh",
        )
        self.assertEqual(len(plan.requests), 1)
        self.assertEqual(plan.requests[0].intent, "UNKNOWN")

    def test_disabled_returns_none(self):
        with patch.dict(os.environ, {"USE_INTERPRETER": "false", "GROQ_API_KEY": "k"}, clear=False):
            self.assertIsNone(understand("fee?"))

    def test_obvious_route_does_not_need_api_key(self):
        with patch.dict(os.environ, {"USE_INTERPRETER": "true", "GROQ_API_KEY": ""}, clear=False):
            plan = understand("fee?")
        self.assertEqual("FEES", plan.requests[0].intent)

    def test_groq_error_returns_none(self):
        with (
            patch.dict(os.environ, _ENV, clear=False),
            patch("groq.Groq", _fake_groq(raise_exc=RuntimeError("boom"))),
        ):
            self.assertIsNone(understand("could you explain that a little more?"))

    @patch("requests.post")
    def test_openrouter_interpreter_uses_configured_model(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "control": "none",
                                "answers_pending_slot": False,
                                "requests": [
                                    {
                                        "intent": "UNKNOWN",
                                        "course_slug": self.slugs[0],
                                        "mode": "retrieve",
                                        "query": "explain prior answer",
                                    }
                                ],
                                "confidence": 0.8,
                                "reason": "ambiguous follow-up",
                            }
                        )
                    }
                }
            ]
        }
        post.return_value = response
        with patch.dict(
            os.environ,
            {
                "USE_INTERPRETER": "true",
                "INTERPRETER_PROVIDER": "openrouter",
                "INTERPRETER_MODEL": "openai/gpt-oss-120b",
                "OPEN_ROUTER_API_KEY": "test-openrouter-key",
            },
            clear=False,
        ):
            plan = understand(
                "could you explain that a little more?",
                course=get_active_courses()[0],
            )

        self.assertIsNotNone(plan)
        _, kwargs = post.call_args
        self.assertEqual("openai/gpt-oss-120b", kwargs["json"]["model"])
        self.assertEqual("Bearer test-openrouter-key", kwargs["headers"]["Authorization"])

    @patch("services.interpret.converse_json")
    def test_bedrock_interpreter_uses_gpt_oss_and_strict_schema(self, converse):
        converse.return_value = "{\n" + json.dumps(
            {
                "control": "none",
                "answers_pending_slot": False,
                "requests": [
                    {
                        "intent": "UNKNOWN",
                        "course_slug": self.slugs[0],
                        "mode": "retrieve",
                        "query": "explain prior answer",
                    }
                ],
                "confidence": 0.8,
                "reason": "ambiguous follow-up",
            }
        )
        with patch.dict(
            os.environ,
            {
                "USE_INTERPRETER": "true",
                "INTERPRETER_PROVIDER": "bedrock",
                "INTERPRETER_MODEL": "openai.gpt-oss-120b-1:0",
                "BEDROCK_REGION": "ap-south-1",
            },
            clear=False,
        ):
            plan = understand(
                "could you explain that a little more?",
                course=get_active_courses()[0],
            )

        self.assertIsNotNone(plan)
        kwargs = converse.call_args.kwargs
        self.assertEqual("openai.gpt-oss-120b-1:0", kwargs["model"])
        self.assertEqual("ap-south-1", kwargs["region"])
        self.assertEqual("turn_plan", kwargs["schema_name"])
        self.assertEqual("object", kwargs["schema"]["type"])


if __name__ == "__main__":
    unittest.main()
