from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from services.conversation_signals import (
    CONTEXT_REPAIR_SIGNALS,
)
from services.conversation_signals import (
    DESCRIPTIVE_SIGNALS as _DESCRIPTIVE_SIGNALS,
)
from services.conversation_signals import (
    FACT_CHALLENGE_SIGNALS as _FACT_CHALLENGE_SIGNALS,
)
from services.conversation_signals import (
    RECOMMENDATION_SIGNALS as _RECOMMENDATION_SIGNALS,
)
from services.conversation_signals import (
    SOCIAL_ACK_SIGNALS as _SOCIAL_ACK_SIGNALS,
)


@dataclass(frozen=True)
class ReplyDecision:
    route: str
    intent: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class IntentProfile:
    intent: str
    examples: tuple[str, ...]
    priority: int
    course_required: bool = False
    prefer_rag_when_descriptive: bool = False
    concept_groups: tuple[tuple[str, ...], ...] = ()
    minimum_groups: int = 1


_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "can",
    "course",
    "do",
    "does",
    "for",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "please",
    "program",
    "programme",
    "that",
    "the",
    "this",
    "to",
    "training",
    "what",
    "when",
    "where",
    "which",
    "who",
    "will",
    "with",
    "you",
    "your",
}


_INTENT_PROFILES = (
    IntentProfile(
        "COMPANY",
        (
            "who is timmins",
            "who are you",
            "who is this",
            "what company is this",
            "are you based in malaysia",
            "where are you based",
            "where is timmins",
            "tell me about timmins",
            "what does your company do",
            "which industries do you serve",
        ),
        priority=100,
    ),
    IntentProfile(
        "CONTACT",
        (
            "contact number",
            "phone number",
            "whatsapp number",
            "email address",
            "how can i reach you",
            "how do i contact timmins",
        ),
        priority=95,
    ),
    IntentProfile(
        "ENROLLMENT",
        (
            "procedure to enroll",
            "procedure to enrol",
            "how to enroll",
            "how to enrol",
            "how do i enroll",
            "how do i enrol",
            "registration process",
            "enrollment process",
            "enrolment process",
            "how to register",
            "how do i register",
            "sign up process",
            "how to book a seat",
            "how to confirm seat",
        ),
        priority=92,
        concept_groups=(
            ("enroll", "enrol", "register", "registration", "sign up", "book", "seat", "join"),
            ("process", "procedure", "steps", "confirm", "reserve"),
        ),
    ),
    IntentProfile(
        "QUOTATION",
        (
            "quotation",
            "quote",
            "official quotation",
            "invoice",
            "proforma",
            "purchase order",
            "letter of undertaking",
        ),
        priority=95,
        concept_groups=(
            (
                "quotation",
                "quote",
                "invoice",
                "proforma",
                "purchase order",
                "po",
                "letter of undertaking",
                "lou",
            ),
        ),
    ),
    IntentProfile(
        "PAYMENT",
        (
            "payment method",
            "patymnet",
            "how to pay",
            "bank transfer",
            "credit card payment",
            "installment payment",
            "instalment payment",
            "when is payment due",
        ),
        priority=90,
        concept_groups=(
            (
                "pay",
                "payment",
                "bank",
                "transfer",
                "card",
                "installment",
                "instalment",
            ),
        ),
    ),
    IntentProfile(
        "FEES",
        (
            "fee",
            "fees",
            "course fee",
            "course fees",
            "price",
            "pricing",
            "cost",
            "how much",
            "discount",
        ),
        priority=88,
        course_required=True,
        prefer_rag_when_descriptive=True,
        concept_groups=(
            (
                "fee",
                "fees",
                "price",
                "pricing",
                "cost",
                "charges",
                "rate",
                "how much",
                "discount",
                "rm",
                "budget",
            ),
        ),
    ),
    IntentProfile(
        "HRDC",
        (
            "hrdc",
            "claimable",
            "hrdc grant",
            "hrdc approval",
            "hrdc portal",
            "grant application",
            "company sponsored",
            "employer sponsored",
            "approval deadline",
            "claim process",
        ),
        priority=86,
        concept_groups=(
            (
                "hrdc",
                "claim",
                "claimable",
                "grant",
                "levy",
                "employer",
                "sponsor",
                "sponsored",
                "company claim",
                "claim process",
                "course code",
                "registration number",
            ),
        ),
    ),
    IntentProfile(
        "SCHEDULE",
        (
            "schedule",
            "course date",
            "training date",
            "when is the class",
            "when does it start",
            "next intake",
            "date of training",
        ),
        priority=82,
        course_required=True,
        concept_groups=(
            ("schedule", "date", "dates", "when", "start", "intake", "available", "july", "august"),
        ),
    ),
    IntentProfile(
        "VENUE",
        (
            "venue",
            "location",
            "where is the class",
            "where is this located",
            "where is it located",
            "where is this",
            "where will it be held",
            "training center",
            "training centre",
            "address",
            "in person",
            "physical class",
        ),
        priority=82,
        concept_groups=(
            ("venue", "location", "address", "where", "held", "center", "centre", "place"),
        ),
    ),
    IntentProfile(
        "ONLINE",
        (
            "online",
            "virtual",
            "remote",
            "zoom",
            "google meet",
            "is this online",
            "is this physical",
            "online or in person",
            "delivery mode",
        ),
        priority=81,
        concept_groups=(
            (
                "online",
                "virtual",
                "remote",
                "hybrid",
                "zoom",
                "meet",
                "physical",
                "in person",
                "delivery mode",
            ),
        ),
    ),
    IntentProfile(
        "DURATION",
        (
            "duration",
            "how many days",
            "how long",
            "number of days",
            "hours",
            "training hours",
        ),
        priority=80,
        course_required=True,
        concept_groups=(("duration", "days", "day", "how long", "hours", "length"),),
    ),
    IntentProfile(
        "TRAINER",
        (
            "trainer",
            "trainers",
            "traniers",
            "instructor",
            "facilitator",
            "trainer profile",
            "who teaches this",
            "who is teaching",
            "speak with trainer",
            "trainer experience",
        ),
        priority=78,
        concept_groups=(
            (
                "trainer",
                "trainers",
                "tranier",
                "traniers",
                "instructor",
                "facilitator",
                "teacher",
                "teaches",
                "teaching",
                "profile",
            ),
        ),
    ),
    IntentProfile(
        "CANCELLATION",
        (
            "cancel",
            "cancellation",
            "cancellatoin",
            "refund",
            "reschedule",
            "postpone",
            "replacement participant",
            "change participant",
        ),
        priority=76,
        concept_groups=(
            (
                "cancel",
                "cancellation",
                "refund",
                "reschedule",
                "postpone",
                "replace",
                "replacement",
                "change participant",
            ),
        ),
    ),
    IntentProfile(
        "CERTIFICATION",
        (
            "certificate",
            "certification",
            "completion certificate",
            "what is included",
            "what's included",
            "what do i receive",
            "materials included",
            "lunch included",
            "refreshments included",
        ),
        priority=74,
        concept_groups=(
            (
                "certificate",
                "certification",
                "cert",
                "completion",
                "included",
                "materials",
                "lunch",
                "food",
                "refreshments",
            ),
        ),
    ),
    IntentProfile(
        "BATCH_SIZE",
        (
            "batch size",
            "batchsize",
            "class size",
            "how many students",
            "how many participants",
            "number of participants",
            "students per batch",
            "participants per class",
            "small class",
        ),
        priority=72,
        concept_groups=(
            (
                "batch",
                "class size",
                "students",
                "participants",
                "capacity",
                "seats",
                "small class",
                "per class",
                "per batch",
            ),
        ),
    ),
    IntentProfile(
        "PLACEMENT",
        (
            "placement",
            "job placement",
            "job support",
            "career support",
            "job guarantee",
            "career guidance",
            "post training support",
            "employment opportunity",
        ),
        priority=70,
        concept_groups=(
            ("placement", "job", "career", "employment", "opportunity", "guarantee", "support"),
        ),
    ),
    IntentProfile(
        "REQUIREMENTS",
        (
            "requirements",
            "prerequisite",
            "prerequisites",
            "what should i bring",
            "laptop required",
            "bring my own laptop",
            "beginner friendly",
            "need experience",
            "hardware required",
        ),
        priority=68,
        prefer_rag_when_descriptive=True,
        concept_groups=(
            (
                "requirement",
                "requirements",
                "prerequisite",
                "prerequisites",
                "beginner",
                "experience",
                "laptop",
                "hardware",
                "software",
                "bring",
            ),
        ),
    ),
    IntentProfile(
        "COURSE_INTRO",
        (),
        priority=62,
        concept_groups=(
            (
                "what is this",
                "what is it",
                "about",
                "explain",
                "overview",
                "brief",
                "info",
                "information",
                "details",
                "know more",
                "learn more",
                "more",
                "offer",
                "offering",
            ),
            (
                "this",
                "it",
                "course",
                "training",
                "program",
                "programme",
                "ad",
                "advert",
                "advertisement",
                "facebook",
                "meta",
                "lead form",
            ),
            ("came", "come", "source", "saw", "seen", "received", "through", "from"),
        ),
        minimum_groups=2,
    ),
)

_FIELD_CONCEPTS: dict[str, tuple[str, ...]] = {
    "FEES": (
        "fee",
        "fees",
        "price",
        "pricing",
        "cost",
        "charges",
        "rate",
        "how much",
        "discount",
        "rm",
        "budget",
    ),
    "COURSE_CONTENT": (
        "course outline",
        "outline",
        "curriculum",
        "syllabus",
        "content",
        "topics",
        "modules",
        "agenda",
        "what will i learn",
        "learn",
        "cover",
        "covered",
        "breakdown",
    ),
}


_QUESTION_STARTERS = {
    "what",
    "how",
    "when",
    "where",
    "who",
    "why",
    "which",
    "can",
    "could",
    "do",
    "does",
    "is",
    "are",
    "will",
}


def _looks_like_question(message: str) -> bool:
    lower = message.lower().replace("’", "'").strip()
    if not lower:
        return False
    if "?" in lower:
        return True
    # Check any word in the message, not just the first — WhatsApp users often
    # lead with context before the actual question ("i came from ad what is this").
    return any(w in _QUESTION_STARTERS for w in lower.split())


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))
    expanded: list[str] = []
    for token in raw:
        if token in _STOPWORDS:
            continue
        expanded.append(token)
        if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
            expanded.append(token[:-1])
        if token.endswith("ing") and len(token) > 5:
            expanded.append(token[:-3])
    return {token for token in expanded if len(token) > 1}


def _raw_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower().replace("’", "'")))


def _canonical_token(token: str) -> str:
    if token.endswith("ing") and len(token) > 5:
        return token[:-3]
    if token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        return token[:-1]
    return token


def _canonical_tokens(tokens: set[str]) -> set[str]:
    return {_canonical_token(token) for token in tokens}


def _term_matches(term: str, lower: str, msg_tokens: set[str], raw_tokens: set[str]) -> bool:
    normalized = term.lower().replace("’", "'").strip()
    if not normalized:
        return False
    if " " in normalized:
        return normalized in lower
    if normalized in raw_tokens or normalized in msg_tokens:
        return True
    if len(normalized) < 5:
        return False
    return any(
        len(token) >= 5 and SequenceMatcher(None, token, normalized).ratio() >= 0.80
        for token in raw_tokens | msg_tokens
    )


def _concept_group_score(
    profile: IntentProfile,
    lower: str,
    msg_tokens: set[str],
    raw_tokens: set[str],
) -> float:
    if not profile.concept_groups:
        return 0.0

    matched_groups = 0
    matched_terms = 0
    for group in profile.concept_groups:
        if any(_term_matches(term, lower, msg_tokens, raw_tokens) for term in group):
            matched_groups += 1
            matched_terms += 1

    if matched_groups < profile.minimum_groups:
        return 0.0

    coverage = matched_groups / len(profile.concept_groups)
    return 3.4 + (matched_terms * 0.7) + (coverage * 2.0)


def requested_fields(message: str) -> set[str]:
    lower = message.lower().replace("’", "'").strip()
    msg_tokens = _tokens(lower)
    raw_tokens = _raw_tokens(lower)
    fields: set[str] = set()
    for field, terms in _FIELD_CONCEPTS.items():
        if any(_term_matches(term, lower, msg_tokens, raw_tokens) for term in terms):
            fields.add(field)
    return fields


def _intent_score(message: str, profile: IntentProfile) -> float:
    lower = message.lower().replace("’", "'").strip()
    msg_tokens = _tokens(lower)
    raw_tokens = _raw_tokens(lower)
    score = _concept_group_score(profile, lower, msg_tokens, raw_tokens)
    for example in profile.examples:
        example_lower = example.lower()
        if example_lower in lower:
            score += 8.0
        example_tokens = _tokens(example_lower)
        if not example_tokens:
            continue
        overlap = msg_tokens & example_tokens
        fuzzy_overlap = {
            msg_token
            for msg_token in msg_tokens
            for example_token in example_tokens
            if msg_token not in overlap
            and len(msg_token) >= 5
            and len(example_token) >= 5
            and SequenceMatcher(None, msg_token, example_token).ratio() >= 0.78
        }
        overlap = overlap | fuzzy_overlap
        if overlap:
            canonical_overlap = _canonical_tokens(overlap)
            canonical_examples = _canonical_tokens(example_tokens)
            example_word_count = len(re.findall(r"[a-z0-9]+", example_lower))
            if len(canonical_examples) == 1 and example_word_count > 1:
                continue
            required_overlap = max(2, (len(canonical_examples) + 1) // 2)
            if len(canonical_examples) > 1 and len(canonical_overlap) < required_overlap:
                continue
            coverage = len(canonical_overlap) / len(canonical_examples)
            score += 2.0 + (4.0 * coverage)
    return score + (profile.priority / 100.0)


def _profile_intent(message: str) -> tuple[IntentProfile, float] | None:
    scored = [(profile, _intent_score(message, profile)) for profile in _INTENT_PROFILES]
    scored = [(profile, score) for profile, score in scored if score >= 3.8]
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[1], -item[0].priority))
    return scored[0]


def decide_reply(
    message: str,
    *,
    course=None,
    catalog: bool = False,
    history: list[dict] | None = None,
    safe_mode: bool = True,
) -> ReplyDecision:
    lower = message.lower().replace("’", "'").strip()
    recent = " ".join(str(item.get("body") or "").lower() for item in (history or [])[-6:])
    # Social/apology messages — acknowledge without interrogating
    if any(signal in lower for signal in _SOCIAL_ACK_SIGNALS):
        return ReplyDecision("social_ack", "SOCIAL_ACK", 1.0, "social/apology message")
    if any(signal in lower for signal in _FACT_CHALLENGE_SIGNALS):
        return ReplyDecision("context", "FACT_CHALLENGE", 1.0, "customer challenged a fact")
    if any(signal in lower for signal in _RECOMMENDATION_SIGNALS):
        return ReplyDecision("exact", "RECOMMENDATION", 0.9, "course recommendation request")
    if course is not None and any(
        phrase in lower for phrase in ("course i am looking for", "i mean", "this is the course")
    ):
        return ReplyDecision("exact", "COURSE_SELECTION", 1.0, "explicit course selection")
    if lower in {"yes", "yes please", "sure"} and "trainer" in recent:
        return ReplyDecision("context", "TRAINER_FOLLOWUP", 0.95, "short trainer followup")
    if any(signal in lower for signal in CONTEXT_REPAIR_SIGNALS):
        return ReplyDecision("context", "CONTEXT_REPAIR", 0.95, "conversation repair")
    if catalog and re.search(r"\b(?:trainer|trainers|instructor|facilitator)\b", lower):
        return ReplyDecision(
            "exact",
            "TRAINER_CATALOG",
            1.0,
            "cross-course trainer profile request",
        )
    if catalog:
        return ReplyDecision("exact", "CATALOG", 1.0, "catalog request")
    descriptive = any(signal in lower for signal in _DESCRIPTIVE_SIGNALS)
    fields = requested_fields(message)
    if {"FEES", "COURSE_CONTENT"}.issubset(fields):
        if course is None:
            return ReplyDecision("clarify", "COURSE_CONTENT_WITH_FEES", 1.0, "course required")
        return ReplyDecision(
            "mixed",
            "COURSE_CONTENT_WITH_FEES",
            1.0,
            "multi-field plan: course content + fees",
        )
    matched = _profile_intent(message)
    if matched:
        profile, score = matched
        if profile.course_required and course is None:
            return ReplyDecision("clarify", profile.intent, min(score / 10, 1.0), "course required")
        # COURSE_INTRO with a selected course → RAG gives best overview answer.
        # Without course → exact gives Timmins company intro.
        if profile.intent == "COURSE_INTRO" and course is not None:
            return ReplyDecision(
                "rag", profile.intent, min(score / 10, 1.0), "course intro → RAG overview"
            )
        if profile.prefer_rag_when_descriptive and descriptive and course is not None:
            return ReplyDecision(
                "rag", profile.intent, min(score / 10, 1.0), "descriptive course question"
            )
        return ReplyDecision(
            "exact",
            profile.intent,
            min(score / 10, 1.0),
            "intent profile match",
        )
    if descriptive:
        if course is None:
            return ReplyDecision("clarify", "COURSE_CONTENT", 1.0, "course required")
        return ReplyDecision("rag", "COURSE_CONTENT", 0.9, "descriptive course question")
    # Unknown questions — try RAG; only hard-block true non-questions in safe mode.
    # WhatsApp users often omit "?", so question words must count as questions.
    if safe_mode and not _looks_like_question(message):
        # Never stonewall someone who already has a course in context — route to RAG
        # so the bot can answer whatever they meant rather than returning a dead clarification.
        if course is not None:
            return ReplyDecision(
                "rag", "COURSE_CONTENT", 0.4, "unclassified with course context — route to RAG"
            )
        return ReplyDecision(
            "clarify", "UNKNOWN", 0.0, "safe mode blocks unclassified non-question"
        )
    return ReplyDecision("rag", "UNKNOWN", 0.35, "unclassified — route to RAG")
