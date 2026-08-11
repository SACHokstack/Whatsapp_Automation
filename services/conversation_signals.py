"""Centralized conversation signals shared across intent detection systems.

This module is the single source of truth for:
- Topic repair / confusion recovery signals
- Month abbreviations for date-based course detection
- Query expansion terms for common questions
- Stop / human handoff / social acknowledgment patterns

By centralizing these patterns, `deterministic_plan()`, `decide_reply()`, and
`understand()` all agree on what constitutes each signal — eliminating the gaps
where one system knew about a pattern but another did not.
"""

from __future__ import annotations

import re

# ── Topic repair / confusion recovery ──────────────────────────────────────
# Messages where the user is correcting the bot's topic choice or expressing
# confusion. These must NEVER be routed to the LLM generator — they need the
# deterministic repair handler that can reference conversation history.

TOPIC_REPAIR_SIGNALS: tuple[str, ...] = (
    "didn't ask about",
    "did not ask about",
    "don't know what i asked",
    "do not know what i asked",
    "don't know what ask",
    "what did i ask",
    "what did i ask for",
    "i am lost",
    "i'm lost",
    "im lost",
    "too confusing",
    "confusing",
    "overwhelming",
    "not sure which",
    "not sure what",
    "i didn't ask about",
    "i didn't ask that",
    "didn't ask that",
    "that is not what i asked",
    "that's not what i asked",
    "you misunderstood",
    "why deleted",
    "why was it deleted",
    "this is not about",
    "this isn't about",
    "i didn't come here for",
)

_TOPIC_REPAIR_RE = re.compile(
    r"\b(?:didn't ask|did not ask|don't know what i asked|do not know what i asked|"
    r"don't know what ask|what did i ask|i am lost|i'm lost|im lost|too confusing|"
    r"confusing|overwhelming|not sure which|not sure what|didn't ask that|did not ask that|"
    r"that is not what i asked|that's not what i asked|you misunderstood|"
    r"why deleted|why was it deleted|this is not about|this isn't about|"
    r"i didn't come here for)\b",
    re.IGNORECASE,
)


def is_topic_repair(message: str) -> bool:
    """Return True if the message signals topic repair or confusion recovery."""
    return bool(_TOPIC_REPAIR_RE.search(message.lower().replace("'", "'")))


# ── Context repair signals ────────────────────────────────────────────────────
# Signals for topic repair that also exist in conversation_controller.py's _CONTEXT_SIGNALS
# This is the same as TOPIC_REPAIR_SIGNALS but with additional context-specific checks.

CONTEXT_REPAIR_SIGNALS: tuple[str, ...] = TOPIC_REPAIR_SIGNALS

def is_context_repair(message: str) -> bool:
    """Return True if the message signals context repair or confusion recovery."""
    return is_topic_repair(message)


# ── Descriptive signals ──────────────────────────────────────────────────────
# Signals indicating the user wants descriptive/course content information

DESCRIPTIVE_SIGNALS: tuple[str, ...] = (
    "course outline",
    "curriculum",
    "syllabus",
    "what will i learn",
    "course content",
    "tell me about the course",
    "prerequisite",
    "beginner",
    "online",
    "virtual",
    "batch size",
    "class size",
    "requirements",
    "need experience",
    "hardware",
)


def is_descriptive(message: str) -> bool:
    """Return True if the message indicates a descriptive/course content request."""
    lower = message.lower().replace("'", "'")
    return any(signal in lower for signal in DESCRIPTIVE_SIGNALS)


# ── Fact challenge signals ─────────────────────────────────────────────────
# Messages where the user challenges a specific fact the bot stated.

FACT_CHALLENGE_SIGNALS: tuple[str, ...] = (
    "no such claim",
    "no such claims",
    "not on the website",
    "website doesn't say",
    "website does not say",
    "that is not true",
    "that's not true",
    "you are wrong",
    "you're wrong",
    "where did you get",
    "that doesn't match",
    "that doesn't sound right",
)


def is_fact_challenge(message: str) -> bool:
    """Return True if the message challenges a specific claim."""
    lower = message.lower().replace("’", "'")
    return any(signal in lower for signal in FACT_CHALLENGE_SIGNALS)


# ── Month abbreviations ────────────────────────────────────────────────────
# WhatsApp users commonly abbreviate month names. All course detection must
# resolve these abbreviations to full month names.

MONTH_ABBREV: dict[str, str] = {
    "jan": "january",
    "feb": "february",
    "mar": "march",
    "apr": "april",
    "jun": "june",
    "jul": "july",
    "aug": "august",
    "sep": "september",
    "oct": "october",
    "nov": "november",
    "dec": "december",
}

MONTHS: tuple[str, ...] = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


def resolve_month(token: str) -> str | None:
    """Given a token, return the full month name if it's a month or abbreviation."""
    lower = token.lower().strip()
    if lower in MONTHS:
        return lower
    return MONTH_ABBREV.get(lower)


# ── Social acknowledgment signals ──────────────────────────────────────────

SOCIAL_ACK_SIGNALS: tuple[str, ...] = (
    "sorry wrong",
    "wrong message",
    "wrong chat",
    "wrong number",
    "oops",
    "my bad",
    "ignore that",
    "disregard",
    "sent by mistake",
    "accident",
    "typo",
    "apolog",
)


def is_social_ack(message: str) -> bool:
    """Return True if the message is a social acknowledgment/apology."""
    lower = message.lower().replace("’", "'")
    return any(signal in lower for signal in SOCIAL_ACK_SIGNALS)


# ── Recommendation signals ─────────────────────────────────────────────────

RECOMMENDATION_SIGNALS: tuple[str, ...] = (
    "suggest",
    "recommend",
    "recommendation",
    "background",
    "which course should",
    "which one should",
    "best course",
    "suitable course",
    "right course",
    "fit for me",
    "good for me",
    "what should i take",
)


def is_recommendation_request(message: str) -> bool:
    """Return True if the message asks for a course recommendation."""
    lower = message.lower().replace("’", "'")
    return any(signal in lower for signal in RECOMMENDATION_SIGNALS)


# ── Trust / company legitimacy signals ─────────────────────────────────────

TRUST_QUESTION_SIGNALS: tuple[str, ...] = (
    "scam",
    "legit",
    "legitimate",
    "trustworthy",
    "reputable",
    "reputation",
    "fake company",
    "real company",
    "are you real",
    "is this real",
)


def is_trust_question(message: str) -> bool:
    """Return True if the message asks about company legitimacy/reputation."""
    lower = message.lower().replace("’", "'")
    return any(re.search(rf"\b{re.escape(term)}\b", lower) for term in TRUST_QUESTION_SIGNALS)


# ── Query expansion: syllabus / curriculum terms ────────────────────────────

# Terms that indicate the user wants syllabus/curriculum content. These are
# used for query expansion in the retriever and for intent detection.

SYLLABUS_EXPANSION_TERMS: tuple[str, ...] = (
    "course content",
    "learning outcomes",
    "skills",
    "tools",
    "practical exercises",
    "key topics",
    "course structure",
    "modules",
)

SYLLABUS_DETECTION_RE = re.compile(
    r"\b(?:syllabus|curriculum|course content|covered topics|modules|learning outcomes|"
    r"what (?:will|do) (?:i|you) (?:learn|teach)|course structure|key topics|"
    r"practical exercises|practical labs)\b",
    re.IGNORECASE,
)


def is_syllabus_request(message: str) -> bool:
    """Return True if the message asks about syllabus/curriculum content."""
    return bool(SYLLABUS_DETECTION_RE.search(message.lower().replace("’", "'")))


# ── Query expansion: payment / installment terms ──────────────────────────

PAYMENT_EXPANSION_TERMS: tuple[str, ...] = (
    "payment",
    "bank transfer",
    "invoice",
    "quotation",
    "payment plan",
    "installments",
)
