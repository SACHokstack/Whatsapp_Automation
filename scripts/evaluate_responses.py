"""Replay representative customer messages without sending anything to WhatsApp.

Usage:
    python scripts/evaluate_responses.py          # deterministic/offline replies
    python scripts/evaluate_responses.py --live-ai  # real Groq replies
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from main import (  # noqa: E402
    _detect_course_from_referral,
    _form_fill_greeting,
    _history_mentions_multiple_courses,
    _human_escalation_reason,
    _parse_form_fill,
    _process_conversation,
    _resolve_course,
)
from services.ai_reply import _deterministic_reply, ai_reply  # noqa: E402
from services.course_loader import get_active_courses, get_course  # noqa: E402

CASES = [
    {
        "name": "Screenshot: first test message",
        "message": "Test",
        "lead": {"status": "CONTACTED", "name": "Aisha"},
        "groups": [("timmins",), ("ask me anything",)],
    },
    {
        "name": "Screenshot: course breakdown and fees",
        "message": "Can you share the course breakdown and fees?",
        "groups": [("rm7,500", "rm 7,500"), ("yocto", "bitbake")],
        "excludes": ("consultant will contact you shortly",),
    },
    {
        "name": "Screenshot: request a person",
        "message": "Can I talk to a person?",
        "groups": [("consultant",), ("reach out",)],
    },
    {
        "name": "Screenshot: natural consultant request",
        "message": "I want to speak to someone",
        "lead": {
            "status": "ASKING_EXPERIENCE_YEARS",
            "conversation_state": "ASKING_EXPERIENCE_YEARS",
        },
        "groups": [("consultant",), ("reach out",)],
        "excludes": ("years of experience",),
    },
    {
        "name": "Screenshot 15: repeated human request overrides qualification",
        "message": "I said I want to speak to somebody",
        "lead": {
            "status": "ASKING_TECHNOLOGIES",
            "conversation_state": "ASKING_TECHNOLOGIES",
        },
        "groups": [("consultant",), ("reach out",)],
        "excludes": ("what prompted", "technologies"),
    },
    {
        "name": "Screenshot 15: bare no does not advance questionnaire",
        "message": "no",
        "lead": {
            "status": "ASKING_EXPERIENCE_YEARS",
            "conversation_state": "ASKING_EXPERIENCE_YEARS",
        },
        "groups": [("course questions",), ("speak with a consultant",)],
        "excludes": ("tools or technologies", "what prompted"),
    },
    {
        "name": "Screenshot 16: explicit HRDC course switches context",
        "message": "The course I am looking for is HRDC: 10001688034, Embedded Linux System Internals",
        "course": "embedded-c-july-2026",
        "groups": [("embedded linux system internals",), ("3–7 august 2026",), ("hrdc",)],
        "excludes": ("embedded c programming", "30–31 july"),
    },
    {
        "name": "Screenshot 16: unique dates identify the course",
        "message": "What course do you have that runs 3rd August to 7th August?",
        "course": "embedded-c-july-2026",
        "groups": [("embedded linux system internals",), ("3–7 august 2026",)],
        "excludes": ("embedded c programming", "30–31 july"),
    },
    {
        "name": "Screenshot: acknowledgement does not restart",
        "message": "OK",
        "lead": {"status": "ENGAGED"},
        "groups": [("feel free", "i'm here")],
        "excludes": ("thanks for getting back",),
    },
    {
        "name": "Interest starts qualification",
        "message": "I am interested",
        "lead": {"status": "ENGAGED", "job_title": "Embedded Engineer"},
        "groups": [("years of experience",)],
    },
    {
        "name": "Negative interest opts out",
        "message": "No thanks, I am not interested",
        "lead": {"status": "CONTACTED"},
        "groups": [("not interested",), ("won't continue", "won’t continue")],
        "excludes": ("years of experience",),
    },
    {
        "name": "Fee during qualification preserves flow",
        "message": "Before that, what is the fee?",
        "lead": {
            "status": "ASKING_EXPERIENCE_YEARS",
            "conversation_state": "ASKING_EXPERIENCE_YEARS",
        },
        "groups": [("rm7,500", "rm 7,500")],
    },
    {
        "name": "Schedule, venue and duration",
        "message": "When and where is the class, and how many days?",
        "groups": [("10",), ("august",), ("penang",), ("5-day", "5 day")],
    },
    {
        "name": "HRDC funding",
        "message": "Is it HRDC claimable and what is the deadline?",
        "groups": [("hrdc",), ("5 august 2026",)],
    },
    {
        "name": "Beginner suitability",
        "message": "I know Linux but I have never used Yocto. Can I join?",
        "groups": [("yocto experience is not required", "suitable")],
    },
    {
        "name": "Installment request",
        "message": "Can I pay in installments?",
        "groups": [("not",), ("standard", "confirmed"), ("bank transfer",)],
        "excludes": ("yes, installment", "installment plan"),
    },
    {
        "name": "Screenshot 20: customer challenges installment claim",
        "message": "I checked their website and no such claims",
        "history": [
            {
                "direction": "outbound",
                "body": "Yes, we can discuss installment arrangements if needed.",
            }
        ],
        "groups": [("right",), ("overstated",), ("bank transfer",), ("can't promise",)],
        "excludes": ("embedded c", "cancellation", "course covers"),
    },
    {
        "name": "Screenshot 21: correction recovers installment topic",
        "message": "I didn't ask about cancellation?",
        "history": [
            {"direction": "inbound", "body": "Do you accept installments?"},
            {
                "direction": "outbound",
                "body": "Here are the Embedded C course details and cancellation policy.",
            },
        ],
        "groups": [("right",), ("installments",), ("bank transfer",)],
        "forbidden": ("cancellation policy", "course outline"),
        "excludes": ("what can i help", "course covers"),
    },
    {
        "name": "Screenshot 21: payment installation typo",
        "message": "I told you about payment installation",
        "groups": [("installments",), ("not listed",), ("bank transfer",)],
        "excludes": ("yes, installment", "knowledge base"),
    },
    {
        "name": "Screenshot 21: other embedded courses uses real catalog",
        "message": "Do you have other embedded courses?",
        "catalog": True,
        "groups": [
            ("embedded linux system internals",),
            ("embedded linux with yocto",),
            ("linux kernel programming",),
        ],
        "excludes": ("firmware development", "visit our website"),
    },
    {
        "name": "Screenshot 22: stop pauses automation",
        "message": "Let's stop it",
        "groups": [("stop",), ("automated replies",)],
        "excludes": ("hrdc", "reach out", "let me check"),
    },
    {
        "name": "Cancellation policy",
        "message": "What is the cancellation policy?",
        "groups": [("14 days",), ("50%",), ("full fee",)],
    },
    {
        "name": "Certification",
        "message": "Will I receive a certificate?",
        "groups": [("printed",), ("electronic",), ("certificate",)],
    },
    {
        "name": "Unknown service does not hallucinate",
        "message": "Do you provide free hotel accommodation and airport pickup?",
        "groups": [
            ("not confirmed", "don't have confirmed", "do not have confirmed"),
            ("verify", "consultant"),
        ],
        "excludes": ("we don't provide", "you need to arrange"),
    },
    {
        "name": "Screenshot 9: August embedded-course catalog",
        "message": "Do you have any embedded course coming up in August?",
        "course": "embedded-c-july-2026",
        "catalog": True,
        "groups": [
            ("embedded linux system internals",),
            ("embedded linux with yocto",),
            ("embedded linux debugging",),
        ],
        "excludes": ("30–31 july", "30-31 july", "don't have information on august"),
    },
    {
        "name": "Screenshot 9: ambiguous catalog follow-up",
        "message": "How much for this course?",
        "course": "embedded-c-july-2026",
        "history": [
            {
                "direction": "outbound",
                "body": "Embedded Linux System Internals, Embedded Linux with Yocto: Deep Foundation & Customization, and Embedded Linux Debugging and Performance Optimization are available in August.",
            }
        ],
        "groups": [("which course", "which one")],
        "excludes": ("rm3,200", "rm 3,200"),
    },
    {
        "name": "Screenshot 13: Embedded C trainer",
        "message": "Ok good, how about the trainer?",
        "course": "embedded-c-july-2026",
        "groups": [("embedded linux consultant",), ("profile",)],
        "excludes": ("software testing professional",),
    },
    {
        "name": "Screenshot 13: trainer highlights",
        "message": "Do you have a summary of the trainer highlights?",
        "course": "embedded-c-july-2026",
        "groups": [("embedded linux consultant",), ("profile",)],
        "excludes": ("software testing professional",),
    },
    {
        "name": "Screenshot 13: customer corrects trainer mix-up",
        "message": "We are talking about Embedded C, but you discussed a software testing trainer.",
        "course": "embedded-c-july-2026",
        "groups": [("right",), ("embedded c",), ("embedded linux consultant",), ("profile",)],
    },
    {
        "name": "Screenshot 10: yes refers to trainer profile",
        "message": "Yes",
        "course": "embedded-c-july-2026",
        "history": [
            {
                "direction": "outbound",
                "body": "I don't have a confirmed trainer profile here. Would you like me to ask our consultant to share it?",
            }
        ],
        "groups": [("trainer profile",), ("consultant", "share")],
        "excludes": ("30–31 july", "course fee"),
    },
    {
        "name": "Screenshot 11: Timmins office phone",
        "message": "Can I have the Timmins office phone number?",
        "course": "embedded-c-july-2026",
        "groups": [("+60 14-395 3661",)],
    },
    {
        "name": "Screenshot 11: Timmins email",
        "message": "Do you have an email address?",
        "course": "embedded-c-july-2026",
        "groups": [("info@timmins-consulting.com",)],
        "excludes": ("timminstraining@gmail.com",),
    },
    {
        "name": "Screenshot 11: customer disputes email",
        "message": "That email does not work. It is the wrong email ID.",
        "course": "embedded-c-july-2026",
        "history": [
            {
                "direction": "outbound",
                "body": "You can email Timmins at info@timmins-consulting.com.",
            }
        ],
        "groups": [("sorry",), ("confirm", "consultant", "verify")],
        "excludes": ("share the correct email",),
    },
    {
        "name": "Screenshot 12: confused customer",
        "message": "I am lost",
        "course": "embedded-c-july-2026",
        "history": [
            {"direction": "inbound", "body": "I asked for the office number."},
            {"direction": "outbound", "body": "Sorry for the confusion."},
        ],
        "groups": [("sorry",), ("continue", "pause", "consultant")],
        "excludes": ("what's on your mind",),
    },
    {
        "name": "Screenshot 12: farewell",
        "message": "Bye",
        "course": "embedded-c-july-2026",
        "groups": [("great day", "goodbye", "take care")],
        "excludes": ("course details",),
    },
]


def _reply(case: dict, *, live_ai: bool, course) -> tuple[str, dict | None]:
    course = get_course(case.get("course", course.slug))
    lead = {"status": "ENGAGED", "course": course.slug, **case.get("lead", {})}
    course = _resolve_course(lead, case["message"]) or course
    history = case.get("history") or []
    catalog = bool(case.get("catalog")) or _history_mentions_multiple_courses(history)
    reason = _human_escalation_reason(case["message"].lower())
    if reason:
        return (
            "Of course! I'll flag this to our consultant right away, and someone will reach out to you shortly.",
            {"route": "human_handoff", "reason": reason},
        )

    reply, updates = _process_conversation(case["message"], lead, course)
    if reply is None:
        generator = ai_reply if live_ai else _deterministic_reply
        reply = generator(case["message"], course, history=history, catalog=catalog)
    return reply, updates


def _matches(case: dict, reply: str) -> tuple[bool, list[str]]:
    lower = reply.lower()
    failures = []
    for alternatives in case.get("groups", ()):
        if not any(term.lower() in lower for term in alternatives):
            failures.append("expected one of: " + " | ".join(alternatives))
    for term in case.get("excludes", ()):
        if term.lower() in lower:
            failures.append("must not contain: " + term)
    if len(reply.split()) > 120:
        failures.append("reply exceeds 120 words")
    return not failures, failures


def _form_fill_case() -> tuple[bool, str]:
    message = """Hello! I filled in your form and would like to know more.
Full name: Aisha Rahman
Who will pay?: Company/HRDC
Company name: Example Systems
Job title: Embedded Engineer
    Email: aisha@example.com"""
    parsed = _parse_form_fill(message)
    course = _detect_course_from_referral(
        {
            "headline": "Embedded Linux System Internals",
            "body": "5-day system-level training from bootloader to kernel debugging",
        }
    )
    if course is None:
        return False, "Course referral was not resolved"
    reply = _form_fill_greeting(parsed, course)
    required = (
        "aisha",
        "embedded engineer",
        "example systems",
        "hrdc",
        "linux system internals",
    )
    return all(term in reply.lower() for term in required), reply


def _all_course_fact_cases() -> tuple[int, int]:
    passed = 0
    courses = get_active_courses()
    print("Active-course template coverage\n")
    for course in courses:
        reply = _deterministic_reply("What are the fee, dates, venue, and duration?", course)
        expected = (
            "rm",
            course.dates.lower(),
            course.venue.lower(),
            "day",
        )
        ok = all(term in reply.lower() for term in expected)
        passed += int(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {course.slug}")
        print(f"Bot: {reply}\n")
    return passed, len(courses)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-ai", action="store_true", help="Use the configured Groq API key")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="Seconds between live Groq cases to reduce rate limiting",
    )
    args = parser.parse_args()

    course = get_course("embedded-linux-yocto-aug-2026")
    failures = 0
    mode = "LIVE GROQ" if args.live_ai else "OFFLINE FALLBACK"
    print(f"Response evaluation mode: {mode}\n")

    for case in CASES:
        reply, updates = _reply(case, live_ai=args.live_ai, course=course)
        passed, reasons = _matches(case, reply)
        failures += int(not passed)
        print(f"[{'PASS' if passed else 'FAIL'}] {case['name']}")
        print(f"Customer: {case['message']}")
        print(f"Bot: {reply}")
        if updates:
            print(f"Route/state: {updates}")
        for reason in reasons:
            print(f"Mismatch: {reason}")
        print()
        if args.live_ai:
            time.sleep(max(args.delay, 0))

    form_passed, form_reply = _form_fill_case()
    failures += int(not form_passed)
    print(f"[{'PASS' if form_passed else 'FAIL'}] Screenshot: personalized Meta form reply")
    print(f"Bot: {form_reply}\n")

    course_passed, course_total = _all_course_fact_cases()
    failures += course_total - course_passed

    total = len(CASES) + 1 + course_total
    print(f"Result: {total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
