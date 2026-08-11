"""Chat with the Timmins bot locally without sending WhatsApp messages.

Usage:
    python scripts/local_chat.py
    python scripts/local_chat.py --course embedded-c-july-2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import main  # noqa: E402
from services.course_loader import get_active_courses, get_course  # noqa: E402


class LocalChatSession:
    """In-memory adapter around the same reply pipeline used by the webhook."""

    def __init__(self, course_slug: str | None = None):
        if course_slug and get_course(course_slug) is None:
            raise ValueError(f"Unknown course slug: {course_slug}")
        self.lead: dict[str, str | int] = {
            "status": "CONTACTED",
            "conversation_state": "",
            "qualification_step": "",
            "course": course_slug or "",
        }
        self.history: list[dict[str, str]] = []

    @property
    def course(self):
        slug = str(self.lead.get("course") or "")
        return get_course(slug) if slug else None

    def reset(self, course_slug: str | None = None) -> None:
        self.__init__(course_slug)

    def reply(self, message: str) -> str:
        message = message.strip()
        if not message:
            return "Please type a message."

        course = main._resolve_course(self.lead, message)
        if course is not None:
            self.lead["course"] = course.slug

        escalation = main._human_escalation_reason(message)
        if escalation:
            reply = (
                "Of course! I'll flag this to our consultant right away, and someone will "
                "reach out to you shortly."
            )
        else:
            reply, updates = main._process_conversation(message, self.lead, course=course)
            if reply is None:
                catalog = main._is_course_catalog_question(message) or (
                    main._is_ambiguous_catalog_followup(message)
                    and main._history_mentions_multiple_courses(self.history)
                )
                reply = main._faq_reply(
                    message,
                    course=course,
                    history=self.history,
                    catalog=catalog,
                )
            if updates:
                self.lead.update(updates)

        self.history.extend(
            (
                {"direction": "inbound", "body": message},
                {"direction": "outbound", "body": reply},
            )
        )
        return reply


def _course_lines() -> str:
    return "\n".join(f"  {course.slug} — {course.name}" for course in get_active_courses())


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Chat with the Timmins bot locally")
    parser.add_argument("--course", help="Start with a configured course slug")
    args = parser.parse_args()

    try:
        session = LocalChatSession(args.course)
    except ValueError as exc:
        parser.error(str(exc))

    print("Timmins local chat — no messages are sent to WhatsApp.")
    print("Commands: /courses, /course <slug>, /state, /reset, /quit")
    if session.course:
        print(f"Current course: {session.course.name}")

    while True:
        try:
            message = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nChat ended.")
            return 0

        if message in {"/quit", "/exit"}:
            print("Chat ended.")
            return 0
        if message == "/courses":
            print("Available course slugs:\n" + _course_lines())
            continue
        if message.startswith("/course "):
            slug = message.removeprefix("/course ").strip()
            course = get_course(slug)
            if course is None:
                print("Unknown course. Use /courses to see valid slugs.")
            else:
                session.lead["course"] = slug
                print(f"Course changed to: {course.name}")
            continue
        if message == "/state":
            print(f"Course: {getattr(session.course, 'name', 'not selected')}")
            print(f"State: {session.lead.get('conversation_state') or 'idle'}")
            print(f"Messages in memory: {len(session.history)}")
            continue
        if message == "/reset":
            session.reset(args.course)
            print("Conversation memory reset.")
            continue
        if message.startswith("/"):
            print("Unknown command. Use /courses, /course <slug>, /state, /reset, or /quit.")
            continue

        print("\nBot: " + session.reply(message))


if __name__ == "__main__":
    raise SystemExit(main_cli())
