from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx

os.environ.setdefault(
    "WHATSAPP_DB_PATH", os.path.join(tempfile.gettempdir(), "timmins-whatsapp-tests.db")
)

import main
from services.course_loader import get_course
from services.interpret import deterministic_plan


class ConversationTests(unittest.TestCase):
    def test_first_contact_fee_question_without_course_offers_a_picker(self):
        reply, updates = main._process_conversation("What is the fee?", {"status": "CONTACTED"})
        self.assertIn("which course", reply.lower())
        # A numbered list, not a bare question the customer has to answer blind
        self.assertIn("1. ", reply)
        # ...and the ask is remembered, so the choice can be answered rather than re-asked
        self.assertTrue(updates["conversation_state"].startswith("ASKING_COURSE_SELECT"))

    def test_day_question_without_course_offers_a_picker(self):
        plan = deterministic_plan("what is on day one")
        reply, updates = main._process_conversation(
            "what is on day one", {"status": "CONTACTED"}, plan=plan
        )
        self.assertIn("which course", reply.lower())
        self.assertIn("1. ", reply)
        self.assertTrue(updates["conversation_state"].startswith("ASKING_COURSE_SELECT"))

    def test_general_kb_plan_is_not_scoped_to_selected_course(self):
        course = get_course("embedded-linux-internals-aug-2026")
        plan = deterministic_plan("What industries does Timmins serve?", current_slug=course.slug)
        with patch.object(
            main,
            "rag_answer",
            return_value="Timmins serves engineering and technology organisations.",
        ) as mock_rag:
            reply = main._reply_from_plan(
                "What industries does Timmins serve?",
                course,
                plan,
                lead={"status": "ENGAGED", "course": course.slug},
                history=[],
            )
        self.assertIn("engineering", reply)
        self.assertIsNone(mock_rag.call_args.kwargs["course"])

    def test_teasing_message_does_not_trigger_human_handoff(self):
        course = get_course("embedded-linux-internals-aug-2026")
        plan = deterministic_plan("are you dummy", current_slug=course.slug)
        reply, updates = main._process_conversation(
            "are you dummy",
            {"status": "ENGAGED", "course": course.slug},
            course=course,
            plan=plan,
        )
        self.assertIsNone(reply)
        self.assertIsNone(updates)
        response = main._reply_from_plan(
            "are you dummy",
            course,
            plan,
            lead={"status": "ENGAGED", "course": course.slug},
            history=[],
        )
        self.assertIn("automated course assistant", response)
        self.assertNotIn("consultant", response)

    def test_first_contact_greeting_gets_welcome(self):
        reply, updates = main._process_conversation("Hello", {"status": "CONTACTED"})
        self.assertIn("Timmins", reply)
        self.assertEqual({"status": "ENGAGED"}, updates)

    def test_repeat_greeting_does_not_resend_welcome(self):
        reply, updates = main._process_conversation("Hi", {"status": "ENGAGED"})
        self.assertEqual("Hi! I'm here. What would you like help with?", reply)
        self.assertIsNone(updates)

    def test_no_does_not_advance_active_qualification(self):
        lead = {
            "status": "ASKING_EXPERIENCE_YEARS",
            "conversation_state": "ASKING_EXPERIENCE_YEARS",
        }
        reply, updates = main._process_conversation("no", lead)
        self.assertIn("speak with a consultant", reply)
        self.assertIsNone(updates)

    def test_exact_screenshot_human_requests_are_detected(self):
        requests = (
            "i said i want to speak to somebody",
            "i want to speak to someone",
            "can i call",
        )
        expected = {
            "i said i want to speak to somebody": "Requested human agent",
            "i want to speak to someone": "Requested human agent",
            "can i call": "Requested callback",
        }
        for message in requests:
            with self.subTest(message=message):
                self.assertEqual(expected[message], main._human_escalation_reason(message))

    def test_callback_request_does_not_advance_active_qualification(self):
        lead = {
            "name": "Test",
            "status": "ASKING_LEARNING_GOALS",
            "conversation_state": "ASKING_LEARNING_GOALS",
            "qualification_step": "ASKING_LEARNING_GOALS",
        }
        reply, updates = main._process_conversation("can i call", lead)
        self.assertIn("consultant", reply)
        self.assertEqual("YES", updates["needs_human"])
        self.assertEqual("Requested callback", updates["human_reason"])
        self.assertNotIn("learning_goals", updates)

    def test_human_request_reply_includes_direct_contact(self):
        reply, updates = main._process_conversation(
            "can i speak to someone",
            {"name": "Test", "status": "ENGAGED", "conversation_state": ""},
        )
        self.assertIn("consultant", reply)
        self.assertIn("+60 14-395 3661", reply)
        self.assertIn("info@timmins-consulting.com", reply)
        self.assertEqual("YES", updates["needs_human"])
        self.assertEqual("Requested human agent", updates["human_reason"])

    def test_quotation_request_flags_human_without_silencing_bot(self):
        from services.interpret import deterministic_plan

        plan = deterministic_plan("can i get a quotation", current_slug="embedded-c-july-2026")
        self.assertTrue(any(r.intent == "QUOTATION" for r in plan.requests))
        lead = {
            "name": "Test",
            "status": "ENGAGED",
            "conversation_state": "",
            "course": "embedded-c-july-2026",
        }
        reply, updates = main._process_conversation("can i get a quotation", lead, plan=plan)
        # No canned reply — the FAQ path answers with the quotation acknowledgement — but the
        # lead is flagged for a human so the banner shows and the consultant is notified.
        self.assertIsNone(reply)
        self.assertEqual("YES", updates["needs_human"])
        self.assertEqual("OPEN", updates["human_status"])
        self.assertEqual("QUOTATION", updates["last_intent"])
        # The reason must NOT start with "Requested", so the bot is not silenced afterwards.
        self.assertFalse(updates["human_reason"].upper().startswith("REQUESTED"))
        self.assertFalse(main._human_handoff_open({**lead, **updates}))

    def test_quotation_not_reflagged_when_handoff_already_open(self):
        from services.interpret import deterministic_plan

        plan = deterministic_plan("can i get a quotation", current_slug="embedded-c-july-2026")
        lead = {
            "name": "Test",
            "status": "ENGAGED",
            "needs_human": "YES",
            "human_status": "OPEN",
            "course": "embedded-c-july-2026",
        }
        reply, updates = main._process_conversation("can i get a quotation", lead, plan=plan)
        self.assertIsNone(reply)
        self.assertIsNone(updates)

    def test_quotation_answer_mentions_consultant_handoff(self):
        from services.course_loader import get_course
        from services.structured_facts import exact_answer

        answer = exact_answer(
            "QUOTATION", get_course("embedded-c-july-2026"), message="can i get a quotation"
        )
        self.assertIn("quotation", answer.lower())
        self.assertIn("consultant", answer.lower())
        # Contact details so the customer can reach out first if they prefer.
        company = main.load_policies()["company"]
        self.assertIn(company["phone"], answer)
        self.assertIn(company["email"], answer)

    def test_handoff_notify_is_skipped_without_support_phone(self):
        with (
            patch.dict(
                os.environ,
                {
                    "HANDOFF_NOTIFY_PHONE": "",
                    "SUPPORT_NOTIFY_PHONE": "",
                    "CONSULTANT_NOTIFY_PHONE": "",
                },
                clear=False,
            ),
            patch.object(main, "send_text") as mock_send,
        ):
            result = main._notify_handoff_owner(
                "60111111111",
                {"name": "Test", "course": "embedded-linux-yocto-aug-2026"},
                reason="Requested human agent",
                source="test",
                message="can i speak to someone",
            )
        self.assertFalse(result)
        mock_send.assert_not_called()

    def test_handoff_notify_sends_whatsapp_to_configured_support_number(self):
        class Response:
            status_code = 200
            ok = True

        with (
            patch.dict(os.environ, {"HANDOFF_NOTIFY_PHONE": "+60 12-345 6789"}, clear=False),
            patch.object(main, "send_template", return_value=Response()) as mock_send,
        ):
            result = main._notify_handoff_owner(
                "60111111111",
                {
                    "name": "Test",
                    "course": "embedded-linux-yocto-aug-2026",
                    "company_name": "Acme",
                },
                reason="Requested human agent",
                source="test",
                message="can i speak to someone",
                worksheet_name="Yocto Leads",
            )
        self.assertTrue(result)
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        notify_to = call_kwargs.args[0]
        self.assertEqual("60123456789", notify_to)
        named_vars = call_kwargs.kwargs.get("named_variables", {})
        self.assertEqual(named_vars.get("customer_phone"), "60111111111")
        self.assertIn("can i speak to someone", named_vars.get("last_message", ""))

    def test_open_human_handoff_suppresses_automation(self):
        self.assertTrue(
            main._human_handoff_open(
                {
                    "needs_human": "YES",
                    "human_status": "OPEN",
                    "human_reason": "Requested human agent",
                }
            )
        )
        self.assertFalse(
            main._human_handoff_open(
                {
                    "needs_human": "YES",
                    "human_status": "CLOSED",
                    "human_reason": "Requested human agent",
                }
            )
        )

    def test_hot_lead_queue_does_not_silence_course_answers(self):
        self.assertFalse(
            main._human_handoff_open(
                {
                    "needs_human": "YES",
                    "human_status": "OPEN",
                    "human_reason": "HOT lead score 9",
                }
            )
        )

    def test_stop_conversation_sets_persistent_pause(self):
        reply, updates = main._process_conversation("Let's stop it", {"status": "ENGAGED"})
        self.assertIn("stop the automated replies", reply)
        self.assertEqual("BOT_PAUSED", updates["status"])
        self.assertEqual("BOT_PAUSED", updates["conversation_state"])

    def test_bot_paused_state_suppresses_later_messages(self):
        self.assertTrue(
            main._automation_paused({"status": "BOT_PAUSED", "conversation_state": "BOT_PAUSED"})
        )

    def test_other_embedded_courses_is_catalog_question(self):
        self.assertTrue(main._is_course_catalog_question("Do you have other embedded courses?"))
        self.assertTrue(
            main._is_course_catalog_question("I want to know more about embedded linux courses")
        )
        self.assertTrue(
            main._is_course_catalog_question("Which other trainer course is HRDC claimable?")
        )

    def test_generic_embedded_linux_courses_gets_catalog_reply(self):
        message = "I want to know more about embedded linux courses"
        plan = deterministic_plan(message)
        self.assertIsNotNone(plan)
        self.assertEqual("CATALOG", plan.requests[0].intent)

        reply = main._faq_reply(
            message,
            lead={"status": "ENGAGED"},
            history=[],
            catalog=main._is_course_catalog_question(message),
            plan=plan,
        )
        self.assertIn("These matching courses are currently scheduled", reply)
        self.assertIn("Embedded Linux", reply)
        self.assertNotIn("don't have", reply.lower())

    def test_cross_course_trainer_profile_question_uses_verified_profile_catalog(self):
        course = get_course("sw-testing-aug-2026")
        message = "ny other course where i can know the trainer's profile"
        reply = main._faq_reply(
            message,
            course=course,
            lead={"status": "ENGAGED", "course": course.slug},
            history=[],
            catalog=True,
        )
        self.assertIn("Embedded Linux System Internals", reply)
        self.assertIn("18+ years", reply)
        self.assertNotIn("These matching courses are currently scheduled", reply)
        self.assertNotIn("Ibis PJCC", reply)

        compound_reply = main._faq_reply(
            "Which other course has a trainer profile, where is it held, and how much?",
            course=course,
            lead={"status": "ENGAGED", "course": course.slug},
            history=[],
            catalog=True,
        )
        self.assertIn("Timmins Training Center, Penang", compound_reply)
        self.assertIn("RM", compound_reply)
        self.assertNotIn("Ibis PJCC", compound_reply)

        hrdc_reply = main._faq_reply(
            "Which other trainer course is HRDC claimable and when does it run?",
            course=course,
            lead={"status": "ENGAGED", "course": course.slug},
            history=[],
            catalog=True,
        )
        self.assertIn("HRDC claimable", hrdc_reply)
        self.assertIn("August 2026", hrdc_reply)
        self.assertNotIn("Ibis PJCC", hrdc_reply)

    def test_topic_repair_signal_routed_to_deterministic_handler(self):
        plan = deterministic_plan(
            "I didn't ask about cancellation?", current_slug="embedded-linux-yocto-aug-2026"
        )
        self.assertIsNotNone(plan)
        self.assertEqual("CONTEXT_REPAIR", plan.requests[0].intent)

    def test_why_deleted_signal_routed_to_deterministic_handler(self):
        plan = deterministic_plan("Why deleted?")
        self.assertIsNotNone(plan)
        self.assertEqual("CONTEXT_REPAIR", plan.requests[0].intent)

    def test_month_abbreviation_resolves_course(self):
        from services.course_loader import detect_explicit_course

        course = detect_explicit_course("i want to take aug 3-7")
        self.assertIsNotNone(course)
        self.assertEqual("embedded-linux-internals-aug-2026", course.slug)

    def test_reputable_question_routed_to_company(self):
        plan = deterministic_plan("is timmins reputable")
        self.assertIsNotNone(plan)
        self.assertEqual("COMPANY", plan.requests[0].intent)

    def test_explicit_course_name_overrides_stored_course(self):
        course = main._resolve_course(
            {"course": "embedded-c-july-2026"},
            "The course I am looking for is Embedded Linux System Internals",
        )
        self.assertEqual("embedded-linux-internals-aug-2026", course.slug)

    def test_hrdc_number_overrides_stored_course(self):
        course = main._resolve_course({"course": "embedded-c-july-2026"}, "HRDC 10001688034")
        self.assertEqual("embedded-linux-internals-aug-2026", course.slug)

    def test_unique_date_range_resolves_course(self):
        course = main._resolve_course(
            {"course": "embedded-c-july-2026"},
            "What course runs 3rd August to 7th August?",
        )
        self.assertEqual("embedded-linux-internals-aug-2026", course.slug)

    def test_unknown_course_asks_instead_of_guessing(self):
        reply, updates = main._process_conversation(
            "Tell me about the course fee", {"status": "ENGAGED"}, None
        )
        self.assertIn("which course", reply.lower())
        self.assertIn("1. ", reply)
        self.assertTrue(updates["conversation_state"].startswith("ASKING_COURSE_SELECT"))

    def test_new_ad_ids_resolve_hidden_course(self):
        internals = main._detect_course_from_referral({"source_id": "120251196291790721"})
        yocto = main._detect_course_from_referral({"source_id": "120251593530460721"})
        self.assertEqual("embedded-linux-internals-aug-2026", internals.slug)
        self.assertEqual("embedded-linux-yocto-aug-2026", yocto.slug)

    def test_scoring_uses_selected_course_technology(self):
        course = get_course("embedded-linux-yocto-aug-2026")
        score = main._lead_score(
            {
                "experience_years": "4 years",
                "funding_path": "Company HRDC",
                "technologies": "Yocto and BitBake",
                "availability": "Immediately",
            },
            course=course,
        )
        self.assertEqual(10, score)
        self.assertEqual("HOT", main._lead_status_from_score(score))

    def test_configured_budget_threshold_affects_score(self):
        course = get_course("embedded-c-july-2026")
        below = main._lead_score({"budget": "RM 1,999"}, course=course)
        at_threshold = main._lead_score({"budget": "RM 2,000"}, course=course)
        self.assertEqual(below + 2, at_threshold)

    def test_not_interested_does_not_start_qualification(self):
        reply, updates = main._process_conversation(
            "No thanks, I am not interested", {"status": "CONTACTED"}
        )
        self.assertIn("not interested", reply)
        self.assertEqual("NOT_INTERESTED", updates["status"])

    def test_enrollment_question_does_not_start_qualification(self):
        course = get_course("sw-testing-aug-2026")
        lead = {"status": "ENGAGED", "conversation_state": ""}
        reply, updates = main._process_conversation(
            "what is the procedure to enroll in the course",
            lead,
            course,
        )
        self.assertIsNone(reply)
        self.assertIsNone(updates)

        answer = main._faq_reply(
            "what is the procedure to enroll in the course",
            course=course,
            lead=lead,
            history=[],
        )
        self.assertIn("To enroll", answer)
        self.assertIn("quotation", answer)
        self.assertNotIn("How many years", answer)

    def test_explicit_interest_still_starts_qualification(self):
        reply, updates = main._process_conversation(
            "interested",
            {"status": "ENGAGED", "conversation_state": ""},
            get_course("sw-testing-aug-2026"),
        )
        self.assertIn("How many years", reply)
        self.assertEqual("ASKING_EXPERIENCE_YEARS", updates["conversation_state"])

    def test_factual_question_does_not_advance_qualification(self):
        course = get_course("embedded-linux-yocto-aug-2026")
        lead = {
            "status": "ASKING_EXPERIENCE_YEARS",
            "conversation_state": "ASKING_EXPERIENCE_YEARS",
        }
        reply, updates = main._process_conversation("Before that, what is the fee?", lead, course)
        self.assertIsNone(reply)
        self.assertIsNone(updates)

    def test_syllabus_and_price_uses_mixed_plan_not_rag_only(self):
        course = get_course("embedded-linux-yocto-aug-2026")
        with patch.object(main, "rag_answer", return_value="RAG-only answer") as mock_rag:
            answer = main._faq_reply(
                "Can you share with me the syllabus and the price",
                course=course,
                lead={"status": "ENGAGED"},
                history=[],
            )
        mock_rag.assert_not_called()
        self.assertIn("RM7,500", answer)
        self.assertIn("Yocto", answer)
        self.assertIn("BitBake", answer)

    def test_terse_syllabus_uses_standalone_retrieval_query(self):
        course = get_course("embedded-linux-debugging-aug-2026")
        plan = deterministic_plan("syllabus", current_slug=course.slug)
        with patch.object(
            main,
            "rag_answer",
            return_value=(
                "This course develops a structured approach to production debugging.\n\n"
                "• Diagnose boot failures\n• Analyze kernel panics"
            ),
        ) as mock_rag:
            answer = main._reply_from_plan(
                "syllabus",
                course,
                plan,
                lead={"status": "ENGAGED", "course": course.slug},
                history=[],
            )
        self.assertIn("production debugging", answer)
        self.assertEqual("syllabus", mock_rag.call_args.args[0])
        self.assertEqual(
            "course syllabus, curriculum, and covered topics",
            mock_rag.call_args.kwargs["retrieval_query"],
        )

    def test_fees_and_syllabus_returns_both_requested_parts(self):
        course = get_course("embedded-c-july-2026")
        plan = deterministic_plan("ok fees and syllabus", current_slug=course.slug)
        with patch.object(
            main,
            "rag_answer",
            return_value=(
                "This hands-on course covers systematic debugging and optimization.\n\n"
                "• Boot and kernel failures\n• CPU and memory bottlenecks"
            ),
        ):
            answer = main._reply_from_plan(
                "ok fees and syllabus",
                course,
                plan,
                lead={"status": "ENGAGED", "course": course.slug},
                history=[],
            )
        self.assertIn("RM3,200", answer)
        self.assertIn("systematic debugging", answer)

    def test_post_greeting_location_question_answers_without_qualification(self):
        reply, updates = main._process_conversation(
            "where is this located",
            {"status": "ENGAGED", "conversation_state": ""},
            None,
        )
        self.assertIsNone(reply)
        self.assertIsNone(updates)

        answer = main._faq_reply(
            "where is this located",
            lead={"status": "ENGAGED", "conversation_state": ""},
            history=[],
        )
        self.assertIn("Current scheduled public venues", answer)
        self.assertIn("Ibis PJCC", answer)
        self.assertNotIn("Which course", answer)

    def test_sorry_wrong_message_gets_friendly_ack(self):
        answer = main._faq_reply("sorry wrong message", lead={"status": "ENGAGED"}, history=[])
        self.assertNotIn("fees, schedule, curriculum", answer)
        self.assertIn("No worries", answer)

    def test_who_is_this_answered_as_company(self):
        answer = main._faq_reply("who is this", lead={"status": "ENGAGED"}, history=[])
        self.assertIn("Timmins", answer)
        self.assertNotIn("fees, schedule, curriculum", answer)

    def test_identity_question_with_greeting_is_not_swallowed_as_greeting(self):
        course = get_course("embedded-linux-debugging-aug-2026")
        plan = deterministic_plan("hi who is this", current_slug=course.slug)
        self.assertEqual("COMPANY", plan.requests[0].intent)
        self.assertEqual("exact", plan.requests[0].mode)
        with patch.object(main, "rag_answer", side_effect=AssertionError("RAG not expected")):
            answer = main._reply_from_plan(
                "hi who is this",
                course,
                plan,
                lead={"status": "CONTACTED", "course": course.slug},
                history=[],
            )
        self.assertIn("Timmins Training & Consulting", answer)
        self.assertIn("Malaysia-based", answer)

        fallback_answer, _ = main._process_conversation(
            "hi who is this",
            {"status": "CONTACTED", "course": course.slug},
            course=course,
            plan=None,
        )
        self.assertIn("Timmins Training & Consulting", fallback_answer)

    def test_hrdc_definition_is_not_answered_as_course_claimability(self):
        course = get_course("embedded-linux-debugging-aug-2026")
        plan = deterministic_plan("what is hrdc", current_slug=course.slug)
        answer = main._reply_from_plan(
            "what is hrdc",
            course,
            plan,
            lead={"status": "ENGAGED", "course": course.slug},
            history=[],
        )
        self.assertIn("Human Resource Development Corporation", answer)
        self.assertIn("e-TRiS", answer)
        self.assertNotIn("approval deadline", answer)

    def test_conversation_repair_and_health_check_are_deterministic(self):
        repair, _ = main._process_conversation("no it did not ask that", {"status": "ENGAGED"})
        working, _ = main._process_conversation("are u working", {"status": "ENGAGED"})
        unclear, _ = main._process_conversation("what", {"status": "ENGAGED"})
        self.assertIn("misunderstood", repair)
        self.assertIn("Yes, I'm working", working)
        self.assertIn("clarify", unclear)

    def test_is_it_based_in_malaysia_answered(self):
        answer = main._faq_reply("is it based in malaysia", lead={"status": "ENGAGED"}, history=[])
        self.assertIn("Malaysia", answer)
        self.assertNotIn("fees, schedule, curriculum", answer)

    def test_policy_intents_route_to_correct_answers_with_typos(self):
        course = get_course("sw-testing-aug-2026")
        cases = [
            ("what is the patymnet process", "PAYMENT", "bank transfer"),
            ("what is the cancellatoin policy", "CANCELLATION", "Cancellation terms"),
            ("will i get certification", "CERTIFICATION", "certificates"),
            ("what is the batchsize", "BATCH_SIZE", "Class sizes"),
            ("do you provide placement support", "PLACEMENT", "career guidance"),
            ("who are the traniers", "TRAINER", "trainer details"),
            ("how to apply for hrdc grant", "HRDC", "HRDC portal"),
            ("is this online or physical", "ONLINE", "scheduled venue"),
            ("do i need to bring laptop", "REQUIREMENTS", "laptop"),
        ]
        for message, intent, expected in cases:
            with self.subTest(message=message):
                decision = main.decide_reply(message, course=course, history=[])
                self.assertEqual("exact", decision.route)
                self.assertEqual(intent, decision.intent)
                answer = main._faq_reply(
                    message,
                    course=course,
                    lead={"status": "ENGAGED"},
                    history=[],
                )
                self.assertIn(expected, answer)

    def test_course_specific_requirements_use_rag_not_generic_policy(self):
        course = get_course("embedded-linux-internals-aug-2026")
        for message in ("what hardware is used in this course", "what are the prerequisites"):
            with self.subTest(message=message):
                decision = main.decide_reply(message, course=course, history=[])
                self.assertEqual("rag", decision.route)
                self.assertEqual("REQUIREMENTS", decision.intent)

    def test_testing_background_gets_course_recommendation(self):
        answer = main._faq_reply(
            "my background is in testing what would you suggest",
            lead={"status": "ENGAGED"},
            history=[
                {
                    "direction": "outbound",
                    "body": "These matching courses are currently scheduled.",
                }
            ],
        )
        self.assertIn("Modern Software Testing", answer)
        self.assertIn("testing background", answer)
        self.assertNotIn("fees, schedule, curriculum", answer)

    def test_confusing_after_testing_context_simplifies_choice(self):
        answer = main._faq_reply(
            "it is too confusing",
            lead={"status": "ENGAGED"},
            history=[
                {"direction": "inbound", "body": "my background is in testing"},
                {
                    "direction": "outbound",
                    "body": "Based on your testing background, I'd suggest Modern Software Testing.",
                },
            ],
        )
        self.assertIn("Modern Software Testing", answer)
        self.assertIn("Let me simplify", answer)
        self.assertNotIn("fees, schedule, curriculum", answer)

    def test_course_specific_examples_are_used(self):
        course = get_course("embedded-linux-yocto-aug-2026")
        reply, _ = main._process_conversation(
            "4 years",
            {
                "status": "ASKING_EXPERIENCE_YEARS",
                "conversation_state": "ASKING_EXPERIENCE_YEARS",
            },
            course,
        )
        self.assertIn("BitBake", reply)
        self.assertNotIn("Selenium", reply)

    def test_ambiguous_course_keywords_do_not_select_arbitrarily(self):
        self.assertIsNone(main.detect_course("training in Penang"))

    def test_expired_duplicate_does_not_override_current_course(self):
        course = main.detect_course("software testing course")
        self.assertEqual("sw-testing-aug-2026", course.slug)

    def test_multiple_courses_in_history_keep_catalog_context(self):
        history = [
            {
                "direction": "outbound",
                "body": "Embedded Linux System Internals and Embedded Linux with Yocto: Deep Foundation & Customization are available.",
            }
        ]
        self.assertTrue(main._history_mentions_multiple_courses(history))
        self.assertTrue(main._is_ambiguous_catalog_followup("How much for this course?"))
        self.assertFalse(main._is_ambiguous_catalog_followup("Why deleted?"))

    def test_other_courses_same_duration_uses_catalog_not_current_course(self):
        course = get_course("sw-testing-aug-2026")
        self.assertTrue(main._is_course_catalog_question("any other course in this duration"))
        answer = main._faq_reply(
            "any other course in this duration",
            course=course,
            lead={"status": "ENGAGED", "conversation_state": ""},
            history=[
                {"direction": "inbound", "body": "how many days is this course"},
                {"direction": "outbound", "body": f"{course.name} is a 4-day course."},
            ],
            catalog=True,
        )
        self.assertIn("another active 4-day course", answer)
        self.assertNotEqual(f"{course.name} is a 4-day course.", answer)

    def test_other_courses_same_duration_lists_matching_courses(self):
        course = get_course("embedded-c-july-2026")
        answer = main._faq_reply(
            "any other course in this duration",
            course=course,
            lead={"status": "ENGAGED", "conversation_state": ""},
            history=[],
            catalog=True,
        )
        self.assertIn("Other active 2-day courses", answer)
        self.assertIn("Linux Kernel Programming", answer)
        self.assertNotIn("Embedded C Programming and GDB Debugging:", answer)


class RagTestChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db = os.environ.get("RAG_TEST_DB_PATH")
        os.environ["RAG_TEST_DB_PATH"] = os.path.join(self.tmp.name, "rag-test.sqlite")

    @staticmethod
    def _request(method: str, path: str, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def tearDown(self):
        if self.previous_db is None:
            os.environ.pop("RAG_TEST_DB_PATH", None)
        else:
            os.environ["RAG_TEST_DB_PATH"] = self.previous_db
        self.tmp.cleanup()

    def test_rag_test_page_is_served(self):
        response = main.rag_test_chat()
        self.assertEqual(200, response.status_code)
        body = response.body.decode()
        self.assertIn("RAG v2 Test Chat", body)
        self.assertIn("Reset memory", body)
        self.assertIn("await res.text()", body)
        self.assertIn("Error ID", body)

    def test_rag_test_chat_persists_context_by_session(self):
        with patch.object(main, "rag_answer", return_value="Testing course answer") as mock_rag:
            first = self._request(
                "POST", "/rag-test/message", json={"message": "What testing course?"}
            )
            self.assertEqual(200, first.status_code)
            session_id = first.json()["session_id"]

            second = self._request(
                "POST",
                "/rag-test/message",
                json={"session_id": session_id, "message": "What are the fees?"},
            )
            self.assertEqual(200, second.status_code)

        history = main.rag_test_history(session_id)["history"]
        self.assertEqual(
            [
                "What testing course?",
                "Testing course answer",
                "What are the fees?",
                "Testing course answer",
            ],
            [item["body"] for item in history],
        )
        self.assertEqual("What testing course?", mock_rag.call_args.kwargs["history"][-2]["body"])
        self.assertEqual("Testing course answer", mock_rag.call_args.kwargs["history"][-1]["body"])
        self.assertIsNone(mock_rag.call_args.kwargs["course"])

    def test_rag_test_reset_clears_persisted_context(self):
        with patch.object(main, "rag_answer", return_value="Answer"):
            response = self._request("POST", "/rag-test/message", json={"message": "hello"})
        session_id = response.json()["session_id"]
        self.assertTrue(main.rag_test_history(session_id)["history"])

        reset = self._request("POST", "/rag-test/reset", json={"session_id": session_id})
        self.assertEqual(200, reset.status_code)
        self.assertEqual([], main.rag_test_history(session_id)["history"])

    def test_unhandled_simulator_failure_returns_json_error(self):
        async def send():
            transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/simulate/message",
                    json={
                        "session_id": "forced-json-error",
                        "message": "test failure",
                        "course": "embedded-linux-debugging-aug-2026",
                    },
                )

        with patch.object(main, "understand", side_effect=RuntimeError("forced failure")):
            response = asyncio.run(send())
        self.assertEqual(500, response.status_code)
        self.assertEqual("application/json", response.headers["content-type"])
        self.assertIn("internal error", response.json()["detail"])
        self.assertRegex(response.json()["error_id"], r"^[a-f0-9]{12}$")


class WebhookBatchTests(unittest.TestCase):
    @patch("main._handle_webhook_value")
    def test_all_messages_and_statuses_are_processed(self, handle_value):
        body = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [{"id": "m1"}, {"id": "m2"}],
                                "statuses": [{"id": "s1"}],
                            }
                        }
                    ]
                },
                {"changes": [{"value": {"messages": [{"id": "m3"}]}}]},
            ]
        }

        main._handle_webhook_body(body)

        self.assertEqual(4, handle_value.call_count)
        for call in handle_value.call_args_list:
            value = call.args[0]
            self.assertFalse("messages" in value and "statuses" in value)

    def test_messages_for_same_sender_are_serialized(self):
        active = 0
        max_active = 0
        counter_lock = threading.Lock()

        def fake_handler(_value):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with counter_lock:
                active -= 1

        value = {"messages": [{"from": "60111111111", "type": "text"}]}
        with patch("main._handle_webhook_value_unlocked", side_effect=fake_handler):
            threads = [
                threading.Thread(target=main._handle_webhook_value, args=(value,)) for _ in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(1, max_active)


if __name__ == "__main__":
    unittest.main()
