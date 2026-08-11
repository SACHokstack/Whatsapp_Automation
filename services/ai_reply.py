from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from groq import Groq, RateLimitError

from services.conversation_signals import (
    FACT_CHALLENGE_SIGNALS as _FACT_CHALLENGE_SIGNALS,
)
from services.conversation_signals import (
    TOPIC_REPAIR_SIGNALS as _TOPIC_REPAIR_SIGNALS,
)
from services.knowledge_base import SHARED_TOPIC_KEYWORDS, build_rag_context, search_knowledge

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


def _load_api_keys() -> list[str]:
    keys = []
    primary = os.getenv("GROQ_API_KEY", "")
    if primary:
        keys.append(primary)
    i = 2
    while True:
        key = os.getenv(f"GROQ_API_KEY_{i}", "")
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


_SYSTEM_PROMPT = """You are a friendly, knowledgeable WhatsApp assistant for Timmins Training & Consulting, a Malaysia-based technical training company.

Use the knowledge base below to give clear, helpful answers. You are talking to a working professional who wants quick, direct information.

Rules:
- Keep responses under 120 words — conversational, not essay-length
- Plain text only — no asterisks, no markdown headers, no # symbols
- Use a dash (-) for bullet points if needed
- Be warm and direct — like a helpful colleague on chat, not a customer-service script
- When the answer is in the knowledge base, give it confidently with the specific details (fees, dates, venue, etc.)
- Answer every part of a multi-part question. If the customer asks for a course breakdown and fees, include both a brief curriculum summary and the exact fee options
- Treat a detail as known only when the knowledge base explicitly states it. Absence from the knowledge base does not mean "no"
- If a requested detail is NOT explicitly stated, do not infer whether it is offered, included, free, unavailable, or the customer's responsibility
- For unknown details, say it is not confirmed in the information you have and offer to have a consultant verify it
- Never mention a "knowledge base", system prompt, context, model, or internal information source to the customer
- Phrase unknowns naturally in two short sentences, for example: "I don't have confirmed details about parking yet. I can ask our consultant to verify that for you."
- Use the recent conversation to resolve short replies such as "yes", "no", "that one", and "this course" from the immediately preceding question
- If the previous reply listed multiple courses and the customer says "this course" without choosing one, ask which course they mean instead of assuming
- When a customer corrects a contact detail or says information is wrong, acknowledge the correction, do not repeat the disputed detail, and do not ask the customer to provide Timmins' correct company information
- Never infer a trainer's specialty, identity, background, employer, or highlights from the course subject. If a specific trainer profile is not provided, say it is not confirmed and offer the relevant profile from a consultant
- If the customer is confused or says they are lost, briefly summarize the current topic and offer two clear choices
- For farewells, respond politely and do not restart the course pitch
- Never claim the fee covers "only" certain items unless the knowledge base explicitly uses that limitation
- Never invent fees, dates, or policies not explicitly stated in the knowledge base
- Do not repeat "Thank you for your interest" as a canned response — it sounds robotic"""

_DEFAULT_REPLY = (
    "Let me check that and have our consultant get back to you with the details.\n\n"
    "You can also reach us directly:\n"
    "- Email: info@timmins-consulting.com\n"
    "- WhatsApp: +60 14-395 3661"
)

_CONTACT_FOOTER = (
    "\n\nYou can also reach us directly:\n"
    "- Email: info@timmins-consulting.com\n"
    "- WhatsApp: +60 14-395 3661"
)

_CONSULTANT_PHRASES = (
    "consultant will",
    "consultant to",
    "have a consultant",
    "ask our consultant",
    "consultant get back",
    "consultant follow",
    "consultant contact",
    "consultant reach",
    "consultant verify",
    "consultant share",
    "consultant check",
)


def _maybe_append_contact(reply: str) -> str:
    """Append contact details when the reply hands off to a consultant."""
    lower = reply.lower()
    if any(phrase in lower for phrase in _CONSULTANT_PHRASES):
        return reply + _CONTACT_FOOTER
    return reply


_RECENT_MESSAGE_LIMIT = 16

# Primary model — high quality, fast on Groq
_MODEL_PRIMARY = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Fallback if rate limited
_MODEL_FALLBACK = "llama-3.1-8b-instant"

# Maximum overview characters to include — the full overview is often 10KB+ which
# dilutes the signal for small factual questions. The first 3000 chars cover all
# key facts (fees, dates, prereqs, trainer). Full content is still available for
# curriculum-specific questions via keyword search.
_MAX_OVERVIEW_CHARS = 3000


def _overview_answer(overview: str, message: str) -> str | None:
    """Extract a concise answer from Q&A sections in a course overview."""
    query = message.lower()
    wanted: tuple[str, ...] = ()
    if any(
        word in query
        for word in (
            "prerequisite",
            "requirement",
            "beginner",
            "new to",
            "never used",
            "no experience",
        )
    ):
        wanted = ("prerequisite", "beginner")
    elif any(
        word in query
        for word in ("curriculum", "syllabus", "content", "learn", "cover", "breakdown")
    ):
        wanted = ("course about", "learn", "cover")
    elif "hardware" in query or "board" in query:
        wanted = ("hardware", "platform")

    if not wanted:
        return None

    sections = overview.split("\nQ")
    for section in sections:
        heading, _, body = section.partition("\n")
        if any(term in heading.lower() for term in wanted):
            cleaned = " ".join(
                line.strip("- ")
                for line in body.splitlines()
                if line.strip() and line.strip().lower() != "answer:"
            )
            if cleaned:
                return cleaned[:550].rstrip()
    return None


def _deterministic_catalog_reply(message: str) -> str:
    from services.course_loader import get_active_courses

    lower = message.lower()
    courses = get_active_courses()
    months = ("july", "august", "september", "october", "november", "december")
    requested_month = next((month for month in months if month in lower), None)
    if requested_month:
        courses = [course for course in courses if requested_month in course.dates.lower()]
    if "embedded" in lower:
        courses = [
            course
            for course in courses
            if "embedded" in course.name.lower() or "linux kernel" in course.name.lower()
        ]
    if not courses:
        return "I don't see a matching active course in the current schedule. I can ask our consultant to check upcoming intakes for you."
    lines = ["Yes — these matching courses are currently scheduled:"]
    lines.extend(f"- {course.name}: {course.dates}, {course.venue}" for course in courses)
    lines.append("Which one would you like details about?")
    return "\n".join(lines)


def _trainer_reply(course=None, *, correction: bool = False) -> str:
    """Answer only with trainer facts explicitly present in the selected course."""
    fact = None
    if course:
        prefixes = (
            "the course is delivered by ",
            "the trainer has ",
            "instructor-led guidance from ",
        )
        for raw_line in course.overview.splitlines():
            line = raw_line.strip().strip("- ")
            if line.lower().startswith(prefixes):
                fact = line.rstrip(".") + "."
                break

    if correction:
        course_name = course.name if course else "this course"
        prefix = (
            f"You're right — we're discussing {course_name}, and the software-testing "
            "trainer reference was incorrect. "
        )
    else:
        prefix = ""

    if fact:
        return (
            f"{prefix}{fact} I don't have the trainer's name or full profile here, "
            "but I can ask our consultant to share it with you."
        )
    return (
        f"{prefix}I don't have confirmed trainer details for this course. "
        "I can ask our consultant to share the relevant trainer profile with you."
    )


def _deterministic_reply(
    message: str,
    course=None,
    *,
    history: list[dict] | None = None,
    catalog: bool = False,
) -> str:
    """Useful local fallback when the AI provider is unavailable."""
    lower = message.lower().replace("’", "'").strip()
    if catalog and re.search(r"\b(?:trainer|trainers|instructor|facilitator)\b", lower):
        from services.structured_facts import exact_answer

        return exact_answer("TRAINER_CATALOG", None, message=message) or _trainer_reply(None)
    if catalog:
        if message.strip().lower() in {"this course", "that course", "how much for this course?"}:
            return "I mentioned a few courses, so I don't want to assume. Which course would you like the fee for?"
        from services.structured_facts import exact_answer

        return exact_answer("CATALOG", course, message=message) or _deterministic_catalog_reply(
            message
        )

    recent = _recent_conversation(history, message).lower()
    payment_wording = (
        "installment" in lower
        or "instalment" in lower
        or ("payment" in lower and "installation" in lower)
    )
    if "why deleted" in lower or "why was it deleted" in lower:
        return (
            "I can't see why a WhatsApp message was deleted, and I shouldn't assume that a course "
            "was removed. I can resend the current course list here if you'd like."
        )
    topic_repair = any(signal in lower for signal in _TOPIC_REPAIR_SIGNALS)
    if topic_repair and any(term in recent for term in ("installment", "instalment")):
        return (
            "You're right — we were discussing installments. They are not a standard confirmed "
            "option; bank transfer is the documented method. I can ask a consultant whether an "
            "exception is possible, but I can't promise one."
        )
    is_fact_challenge = any(signal in lower for signal in _FACT_CHALLENGE_SIGNALS)
    if is_fact_challenge and any(term in recent for term in ("installment", "instalment")):
        return (
            "You're right to question that — I overstated it. Installments are not a standard "
            "confirmed payment option; bank transfer is the documented method. If you need "
            "flexibility, I can ask a consultant whether an exception is possible, but I can't "
            "promise one."
        )
    if is_fact_challenge:
        return (
            "You're right to question that, and I don't want to defend an unverified claim. "
            "Which detail would you like me to recheck with a consultant?"
        )
    if course and "course i am looking for" in lower:
        return (
            f"Got it — you're interested in {course.name}, scheduled for {course.dates}. "
            "It is HRDC claimable. Would you like the fees, course outline, or registration details?"
        )
    if (
        course
        and "what course" in lower
        and any(
            month in lower
            for month in ("july", "august", "september", "october", "november", "december")
        )
    ):
        return (
            f"That is {course.name}, scheduled for {course.dates}. "
            "Would you like the fees or course outline?"
        )
    if lower in {"yes", "yes please", "sure"} and "trainer" in recent:
        return (
            "Sure — I’ll ask our consultant to share the trainer profile for this course with you."
        )
    if "trainer" in lower and any(
        phrase in lower for phrase in ("software testing", "wrong trainer", "talk about embedded")
    ):
        return _trainer_reply(course, correction=True)
    if any(
        phrase in lower for phrase in ("wrong email", "email does not work", "email doesn't work")
    ):
        return "Sorry about that. I won’t repeat the disputed email address, and I’ll ask our consultant to confirm the correct Timmins contact details for you."
    if lower in {"i am lost", "i'm lost", "im lost"}:
        return "Sorry for the confusion. We can either continue with the course details or pause here and have a consultant follow up — which would you prefer?"
    if any(
        phrase in lower
        for phrase in ("too confusing", "confusing", "overwhelming", "not sure which")
    ):
        if any(term in recent for term in ("testing", "qa", "selenium", "playwright")):
            return (
                "Let me simplify it: based on a testing/QA background, the best match is "
                "Modern Software Testing with AI-Assisted Automation & CI/CD Integration. "
                "It focuses on Playwright, API testing, AI-assisted testing, and CI/CD. "
                "Would you like the fees or course outline?"
            )
        return (
            "Let me simplify it: if your background is testing/QA, choose the Modern Software Testing course; "
            "if it is firmware, embedded, or Linux, choose one of the Embedded/Linux courses. "
            "Tell me your background and I’ll pick one."
        )
    parts: list[str] = []
    asks_fee = any(
        word in lower for word in ("fee", "fees", "cost", "price", "pricing", "discount")
    )
    asks_schedule = any(
        word in lower for word in ("date", "dates", "schedule", "when", "next class")
    )
    asks_venue = any(word in lower for word in ("venue", "where", "location", "held"))
    asks_hrdc = any(word in lower for word in ("hrdc", "claimable", "grant"))
    asks_duration = any(word in lower for word in ("duration", "how long", "how many days"))
    asks_content = any(
        word in lower
        for word in ("curriculum", "syllabus", "content", "learn", "cover", "breakdown")
    )

    if course and asks_fee and course.fees:
        fee_parts = []
        if course.fees.get("standard"):
            fee_parts.append(f"RM{course.fees['standard']:,} per participant")
        if course.fees.get("group_2"):
            fee_parts.append(f"RM{course.fees['group_2']:,} each for 2 participants")
        if course.fees.get("group_3_plus"):
            fee_parts.append(f"RM{course.fees['group_3_plus']:,} each for 3 or more")
        parts.append("The fee is " + "; ".join(fee_parts) + ".")

    if course and asks_schedule and course.dates:
        if course.active:
            parts.append(f"The scheduled dates are {course.dates}.")
        else:
            parts.append(
                f"That intake was scheduled for {course.dates}. I can ask a consultant for the next available dates."
            )
    if course and asks_duration:
        duration = re.search(r"\b(\d+)[- ]day\b", course.overview, re.IGNORECASE)
        if duration:
            parts.append(f"It is a {duration.group(1)}-day course.")
    if course and asks_venue and course.venue:
        parts.append(f"It will be held at {course.venue}.")
    if course and asks_hrdc:
        text = "The training is HRDC claimable"
        if course.hrdc_deadline:
            text += f", and the approval deadline is {course.hrdc_deadline}"
        parts.append(text + ". Timmins can provide the documents needed for the application.")

    if course and asks_content:
        overview_reply = _overview_answer(course.overview, message)
        if overview_reply:
            parts.append(overview_reply)

    if parts:
        return " ".join(parts) + " Would you like help with registration?"

    if course:
        overview_reply = _overview_answer(course.overview, message)
        if overview_reply:
            return overview_reply

    shared_docs = search_knowledge(message, limit=1)
    if shared_docs:
        topic = shared_docs[0]["topic"]
        explicit_topic = any(keyword in lower for keyword in SHARED_TOPIC_KEYWORDS.get(topic, ()))
        if not explicit_topic:
            return "I don't have confirmed details about that yet. I can ask our consultant to verify it for you."
        if topic == "payment":
            if payment_wording:
                return (
                    "Installments are not listed as a standard confirmed payment option. "
                    "Payment is by bank transfer. If you need flexibility, I can ask a consultant "
                    "whether an exceptional arrangement is possible, but I can't promise one."
                )
            return "Payment is by bank transfer. We can provide an official quotation or invoice, and a seat is confirmed after payment, HRDC approval, a PO, or a Letter of Undertaking is received."
        if topic == "cancellation":
            return "Our cancellation terms are:\n- More than 14 days before training: no cancellation fee\n- 7–13 days before: a 50% fee may apply\n- Less than 7 days before: the full fee may apply"
        if topic == "certification":
            return "Yes. After successful participation, you’ll receive both printed and electronic certificates of completion. The fee also includes training materials, refreshments, lunch, and hands-on labs."
        if topic == "online":
            return "The delivery mode depends on the intake. I don’t want to guess, so I can have a consultant confirm whether this class is in person or online and share the brochure."
        if topic == "trainers":
            return _trainer_reply(course)
        if topic == "company" and any(
            word in lower for word in ("email", "phone", "contact", "whatsapp")
        ):
            contact_lines = [
                line.removeprefix("- ").strip()
                for line in shared_docs[0]["content"].splitlines()
                if line.strip().lower().startswith(("- email:", "- phone / whatsapp:"))
            ]
            if contact_lines:
                return "You can contact Timmins here:\n- " + "\n- ".join(contact_lines)

        content = shared_docs[0]["content"]
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        answer = " ".join(" ".join(lines).split())
        words = answer.split()
        return " ".join(words[:110]) + ("…" if len(words) > 110 else "")

    if course:
        return "I don't have confirmed details about that yet. I can ask our consultant to verify it for you."
    return _DEFAULT_REPLY


def _trim_context(context: str) -> str:
    """Prevent the context from overwhelming the model on factual questions."""
    if len(context) <= _MAX_OVERVIEW_CHARS * 2:
        return context
    # Keep the course facts block (always first) in full; trim the overview section
    overview_marker = "[COURSE OVERVIEW]"
    idx = context.find(overview_marker)
    if idx == -1:
        return context[: _MAX_OVERVIEW_CHARS * 2]
    facts_block = context[:idx]
    overview_block = context[idx : idx + len(overview_marker) + _MAX_OVERVIEW_CHARS]
    rest = context[idx + len(overview_marker) + _MAX_OVERVIEW_CHARS :]
    return facts_block + overview_block + ("\n\n" + rest.strip() if rest.strip() else "")


def _sanitize_reply(reply: str) -> str:
    """Normalize model output to clean WhatsApp-friendly plain text."""
    reply = re.sub(r"(?m)^\s*#{1,6}\s*", "", reply)
    reply = reply.replace("**", "").replace("__", "")
    reply = re.sub(r"\n{3,}", "\n\n", reply)
    return reply.strip()


def _recent_conversation(history: list[dict] | None, current_message: str) -> str:
    if not history:
        return ""
    recent = list(history[-_RECENT_MESSAGE_LIMIT:])
    if (
        recent
        and recent[-1].get("direction") == "inbound"
        and recent[-1].get("body", "").strip() == current_message.strip()
    ):
        recent.pop()
    lines = []
    for item in recent:
        role = "Customer" if item.get("direction") == "inbound" else "Assistant"
        body = str(item.get("body") or "").strip()
        if body:
            lines.append(f"{role}: {body}")
    return "\n".join(lines)


def ai_reply(
    message: str,
    course=None,
    *,
    history: list[dict] | None = None,
    catalog: bool = False,
) -> str:
    lower = message.lower().replace("’", "'").strip()
    recent = _recent_conversation(history, message).lower()

    # Keep high-risk routing and exact company facts deterministic. These are
    # the areas where a generative answer can most easily mix courses or invent
    # a trainer/contact detail.
    if (
        "installment" in lower
        or "instalment" in lower
        or ("payment" in lower and "installation" in lower)
    ):
        return _deterministic_reply(message, course, history=history)
    if any(signal in lower for signal in _TOPIC_REPAIR_SIGNALS):
        return _deterministic_reply(message, course, history=history)
    if any(signal in lower for signal in _FACT_CHALLENGE_SIGNALS):
        return _deterministic_reply(message, course, history=history)
    if "why deleted" in lower or "why was it deleted" in lower:
        return _deterministic_reply(message, course, history=history)
    if catalog:
        return _deterministic_reply(message, course, history=history, catalog=True)
    if "trainer" in lower:
        return _deterministic_reply(message, course, history=history)
    if any(word in lower for word in ("email", "phone number", "office phone")):
        return _deterministic_reply(message, course, history=history)
    if lower in {"i am lost", "i'm lost", "im lost"}:
        return _deterministic_reply(message, course, history=history)
    if lower in {"yes", "yes please", "sure"} and "trainer" in recent:
        return _deterministic_reply(message, course, history=history)

    context = build_rag_context(message, course, include_catalog=catalog)
    if not context:
        return _DEFAULT_REPLY

    keys = _load_api_keys()
    if not keys:
        logger.error("event=ai_reply_failed reason=no_api_key")
        return _deterministic_reply(message, course, history=history, catalog=catalog)

    trimmed_context = _trim_context(context)
    recent_conversation = _recent_conversation(history, message)
    models_to_try = [(_MODEL_PRIMARY, key) for key in keys] + [(_MODEL_FALLBACK, keys[0])]

    for model, key in models_to_try:
        try:
            client = Groq(api_key=key, max_retries=0, timeout=15.0)
            response = client.chat.completions.create(
                model=model,
                max_tokens=450,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Knowledge base:\n{trimmed_context}\n\n"
                            f"Recent conversation:\n{recent_conversation or '[none]'}\n\n"
                            f"Current customer message: {message}"
                        ),
                    },
                ],
            )
            reply = _maybe_append_contact(_sanitize_reply(response.choices[0].message.content))
            logger.info(
                "event=ai_reply model=%s completion_tokens=%s",
                model,
                response.usage.completion_tokens if response.usage else "unknown",
            )
            return reply
        except RateLimitError:
            logger.warning("event=ai_reply_rate_limited model=%s", model)
            continue
        except Exception:
            logger.exception("event=ai_reply_failed model=%s", model)
            continue

    logger.error("event=ai_reply_failed reason=all_attempts_exhausted")
    return _maybe_append_contact(
        _deterministic_reply(message, course, history=history, catalog=catalog)
    )
