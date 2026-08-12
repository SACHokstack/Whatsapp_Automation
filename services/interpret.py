"""Single authoritative understanding step for the reply brain.

`understand()` reads meaning + conversation state once per turn and emits a structured,
multi-part, slot-aware `TurnPlan` that EVERY downstream layer obeys (the lifecycle machine,
the router, and generation) — so nothing re-parses the raw message with its own keyword logic.

Returns None when it cannot run (disabled, no API key, provider error) → callers fall back to the
legacy `decide_reply` path, so behaviour degrades to today's, never worse.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from rag_v2.query_expansion import normalize_query, requested_day
from services.bedrock import bedrock_model, bedrock_region, converse_json
from services.conversation_signals import (
    is_fact_challenge,
    is_topic_repair,
    is_trust_question,
)
from services.course_loader import get_active_courses

logger = logging.getLogger(__name__)

# Commercial facts structured_facts.exact_answer() can serve verbatim (mode="exact").
EXACT_INTENTS = {
    "FEES",
    "SCHEDULE",
    "VENUE",
    "DURATION",
    "HRDC",
    "PAYMENT",
    "QUOTATION",
    "CANCELLATION",
    "CERTIFICATION",
    "BATCH_SIZE",
    "PLACEMENT",
    "REQUIREMENTS",
    "CONTACT",
    "COMPANY",
    "TRAINER",
    "TRAINER_CATALOG",
    "ONLINE",
    "DISCOUNT",
    "PARTICIPANT_REPLACEMENT",
    "HRDC_DOCUMENTS",
    "BOT_IDENTITY",
    "FRUSTRATION",
    "REPETITION_REPAIR",
    "FREE_TUTORING",
    "COURSE_INTRO",
    "COURSE_CONFIRMATION",
    "BEGINNER_FIT",
    "COURSE_VALUE",
    "COURSE_COMPARISON",
}
# Intents that answer from the whole catalog (course=None) rather than one course.
CATALOG_INTENTS = {"CATALOG", "RECOMMENDATION", "TRAINER_CATALOG"}

# Skill vocabulary grouped by the course domain it belongs to. Used only to tell a
# single-domain background ("i know selenium") from one that spans several ("i know c, cpp
# and selenium"), where no single course is the obvious answer.
_SKILL_DOMAINS = {
    "testing": r"\b(?:selenium|playwright|cypress|appium|junit|testng|jmeter|postman|"
    r"qa|test automation|manual testing|automation testing)\b",
    "systems": r"\b(?:c|c\+\+|cpp|embedded|firmware|rtos|microcontroller|mcu|"
    r"device driver|drivers?|kernel|yocto|bitbake|u-boot|uboot|linux)\b",
    "scripting": r"\b(?:python|bash|shell scripting|perl|ruby)\b",
    "web": r"\b(?:javascript|typescript|react|angular|node|php|html|css)\b",
    "data": r"\b(?:sql|pandas|numpy|machine learning|data science|tableau|power bi)\b",
}
VALID_INTENTS = (
    EXACT_INTENTS
    | CATALOG_INTENTS
    | {
        "COURSE_CONTENT",
        "OPERATIONS",
        "BOUNDARY",
        "GREETING",
        "SMALLTALK",
        "CONTEXT_REPAIR",
        "UNKNOWN",
    }
)
_MODES = {"exact", "retrieve"}

_PROFILE_FIELDS = (
    ("name", "Name"),
    ("company_name", "Company"),
    ("job_title", "Role"),
    ("who_will_pay", "Who pays"),
    ("experience_years", "Experience (yrs)"),
    ("technologies", "Technologies"),
    ("motivation", "Motivation"),
    ("learning_goals", "Goals"),
    ("budget", "Budget"),
    ("email", "Email"),
)


@dataclass(frozen=True)
class Request:
    intent: str
    course_slug: str | None
    mode: str  # "exact" | "retrieve"
    query: str = ""  # standalone rephrasing of THIS ask (used for retrieval)


@dataclass(frozen=True)
class TurnPlan:
    control: str  # "none" | "human" | "stop"
    answers_pending_slot: bool
    requests: tuple[Request, ...]
    confidence: float
    reason: str


def _resolved_slug(message: str, courses, current_slug: str | None) -> str | None:
    lower = message.lower()
    for item in courses:
        if item.slug.lower() in lower or item.name.lower() in lower:
            return item.slug
    # These names are unambiguous in the current catalog and common in WhatsApp chat.
    for item in courses:
        distinctive = (
            "yocto"
            if "yocto" in item.name.lower()
            else "software testing"
            if "software testing" in item.name.lower()
            else "embedded c"
            if "embedded c" in item.name.lower()
            else "kernel programming"
            if "kernel programming" in item.name.lower()
            else "system internals"
            if "system internals" in item.name.lower()
            else ""
        )
        if distinctive and distinctive in lower:
            return item.slug
    known_slugs = {item.slug for item in courses}
    return current_slug if current_slug in known_slugs else None


# Distinctive, unambiguous course keywords that signal an explicit course switch in
# free-text WhatsApp messages (mirrors the terms used in _resolved_slug).
_DISTINCTIVE_TERMS = (
    "yocto",
    "software testing",
    "embedded c",
    "kernel programming",
    "system internals",
)


def _message_references_slug(message: str, slug: str, courses) -> bool:
    """True if the message explicitly names this course — by slug, full name, or a
    distinctive keyword. Used to gate LLM-proposed course switches so the model cannot
    silently move the conversation to a course the customer never mentioned."""
    lower = message.lower()
    item = next((c for c in courses if c.slug == slug), None)
    if item is None:
        return False
    name = item.name.lower()
    if item.slug.lower() in lower or name in lower:
        return True
    return any(term in name and term in lower for term in _DISTINCTIVE_TERMS)


def deterministic_plan(
    message: str,
    *,
    courses=None,
    current_slug: str | None = None,
) -> TurnPlan | None:
    """Route high-confidence chat patterns without spending an interpreter API call."""
    normalized = normalize_query(message)
    if not normalized:
        return None
    courses = list(courses if courses is not None else get_active_courses())
    slug = _resolved_slug(normalized, courses, current_slug)

    stop = bool(
        re.fullmatch(
            r"(?:exit|quit|stop|pause|unsubscribe|stop messaging|stop messages)[ .!?]*", normalized
        )
    )
    human = bool(
        re.search(
            r"\b(?:speak|talk|chat)\s+(?:to|with)\s+(?:a\s+)?(?:human|person|someone|consultant|agent)\b|"
            r"\b(?:human|live)\s+(?:agent|support)\b|\bcall\s*back\b|\bconsultant\b|"
            r"\b(?:delete|remove|erase)\s+(?:all\s+)?my\s+(?:data|details|info|information|records?)\b",
            normalized,
        )
    )
    control = "stop" if stop else "human" if human else "none"

    if re.fullmatch(r"(?:hi|hello|hey|good (?:morning|afternoon|evening))[ .!?]*", normalized):
        return TurnPlan(
            control, False, (Request("GREETING", slug, "retrieve"),), 0.99, "rule:greeting"
        )
    if re.fullmatch(r"(?:thanks|thank you|okay|ok|got it|noted)[ .!?]*", normalized):
        return TurnPlan(
            control, False, (Request("SMALLTALK", slug, "retrieve"),), 0.99, "rule:smalltalk"
        )

    # Repair statements must be understood as whole utterances. Looking only at
    # "price" in this sentence would repeat the very fact the customer complained
    # about, which makes the assistant appear unable to follow the conversation.
    if re.search(
        r"\b(?:already (?:asked|told)|stop repeating|keep repeating|repeated that|"
        r"why (?:do )?you repeat|why repeating|said that already)\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("REPETITION_REPAIR", None, "exact"),),
            0.99,
            "rule:repetition-repair",
        )

    if re.search(
        r"\b(?:are you|you are|you're)\s+(?:an?\s+)?(?:robot|bot|automated|ai)\b|"
        r"\b(?:real person|actual person|human or (?:a )?bot)\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("BOT_IDENTITY", None, "exact"),),
            0.99,
            "rule:bot-identity",
        )

    if re.search(
        r"\b(?:this is useless|this (?:isn't|is not) helpful|not helpful at all|"
        r"you(?:'re| are) no help|waste of time|nothing (?:works|is working))\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("FRUSTRATION", None, "exact"),),
            0.99,
            "rule:frustration",
        )

    if re.search(
        r"\bteach\s+me\b.*\bfor\s+free\b|\bfree\b.*\b(?:lesson|course|training|tutoring)\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("FREE_TUTORING", slug, "exact"),),
            0.99,
            "rule:free-tutoring",
        )

    if re.search(
        r"\b(?:are you|you are|you're)\s+(?:a\s+)?(?:dummy|stupid|idiot|fat|useless)\b|"
        r"\b(?:shut up|useless bot)\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("BOUNDARY", None, "exact"),),
            0.99,
            "rule:boundary",
        )

    # Company-level questions — must NOT be mistaken for a single course's venue/HRDC
    # (which would then ask "which course?"). These are about Timmins itself.
    if re.search(
        r"\bwhere\s+(?:are|is)\s+(?:you|timmins)\b|\bwhere are you (?:based|located)\b|"
        r"\byour (?:office|address|head ?office)\b|\bare you (?:based|located)\b",
        normalized,
    ):
        return TurnPlan(
            control, False,
            (Request("COMPANY", None, "exact", "Timmins office location and contact details"),),
            0.99, "rule:company-location",
        )
    if re.search(
        r"\b(?:are you|is timmins|is your company)\s+(?:hrdc|hrd corp)\b|\bhrdc[- ]registered\b|"
        r"\bregistered (?:training )?provider\b",
        normalized,
    ):
        return TurnPlan(
            control, False,
            (Request("COMPANY", None, "retrieve", "Timmins HRDC registration and credentials"),),
            0.99, "rule:company-hrdc",
        )

    # Recommendation / "which is best|cheapest|for me" — never a single-course fact.
    # Price SUPERLATIVES compare across the catalogue ("which is the most expensive"), unlike
    # "how much is it", which is a FEES question about one course and is handled separately.
    if re.search(
        r"\b(?:recommend|suggest)\b|\bwhat should i (?:take|choose|do|learn)\b|"
        r"\bwhich (?:course|one|training|program)\b.{0,30}\b(?:best|suit|cheapest|right|for me|should)\b|"
        r"\bbest (?:course|option|one) for me\b|\bcheapest\b|\bpriciest\b|"
        r"\b(?:most|least)\s+(?:expensive|affordable|costly|pricey)\b|\bbest value\b",
        normalized,
    ):
        return TurnPlan(
            control, False,
            (Request("RECOMMENDATION", None, "exact", normalized),),
            0.99, "rule:recommendation",
        )

    # "i know c, cpp and selenium" — a background spanning MORE THAN ONE domain has no single
    # obvious course, so it is a recommendation question even though nothing was asked outright.
    # A single-domain background ("i know selenium") is left alone: it may be leading somewhere
    # specific, and the interpreter can read the intent better than a keyword rule can.
    if re.search(
        r"\bi\s+(?:know|use[d]?|have\s+used|work(?:ed)?\s+(?:with|on|in)|"
        r"am\s+familiar\s+with|have\s+experience\s+(?:in|with))\b|"
        r"\bmy\s+background\s+(?:is|in)\b",
        normalized,
    ) and not re.search(
        # A background that also asks something outright ("...and what is the fee") is a
        # compound turn: leave it to the interpreter, which can answer both parts, rather
        # than short-circuiting to a recommendation and dropping the question.
        r"\bdo you (?:have|offer|provide|teach|run|conduct)\b|"
        r"\b(?:fees?|price|cost|how much|when|dates?|schedule|where|venue|"
        r"how long|duration|trainer|hrdc|claimable|syllabus|curriculum)\b",
        normalized,
    ):
        domains = {
            domain
            for domain, pattern in _SKILL_DOMAINS.items()
            if re.search(pattern, normalized)
        }
        if len(domains) >= 2:
            return TurnPlan(
                control, False,
                (Request("RECOMMENDATION", None, "exact", normalized),),
                0.99, "rule:mixed-background",
            )

    # Interest in a topic/category with no specific course selected → show the catalog
    # (rather than falling to the LLM and intermittently abstaining, as the demo did).
    if re.search(
        r"\b(?:interested in|want to learn|keen (?:on|to learn)|looking (?:for|to learn)|"
        r"thinking (?:of|about) (?:learning|doing)|want to (?:do|study))\b.{0,30}"
        r"\b(?:embedded|linux|yocto|kernel|python|software testing|testing|automation|"
        r"firmware|c programming|systems?|debugging|driver)\b",
        normalized,
    ):
        if slug is not None:
            return TurnPlan(
                control, False,
                (Request("COURSE_CONTENT", slug, "retrieve",
                         "course overview, who it is for, and what it covers"),),
                0.99, "rule:course-interest",
            )
        return TurnPlan(
            control, False,
            (Request("CATALOG", None, "exact", "active course catalog"),),
            0.99, "rule:category-interest",
        )

    contradiction_question = bool(
        re.search(
            r"\b(?:which is it|so is it|which one|so which|do?n'?t lie|stop lying|"
            r"you (?:said|told me)|flyer says|actually says|are you sure)\b",
            normalized,
        )
    )
    if is_topic_repair(normalized) or is_fact_challenge(normalized) or contradiction_question:
        return TurnPlan(
            control,
            False,
            (Request("CONTEXT_REPAIR", slug, "retrieve", normalized),),
            0.99,
            "rule:topic-repair",
        )

    # Natural course-context checks are not syllabus searches. Answer which
    # conversation/course this is first, especially for hesitant prospects.
    if re.search(
        r"\b(?:is|was)\s+(?:this|it)\s+(?:the\s+)?(?:c|linux|python|testing|yocto)\b"
        r".*\bor\b|\bwhich\s+one\b.*\b(?:forgot|again)\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("COURSE_CONFIRMATION", slug, "exact"),),
            0.99,
            "rule:course-confirmation",
        )

    if re.search(
        r"\b(?:ask|know|hear)\s+about\b.*\b(?:computer programming|programming course|course)\b",
        normalized,
    ) and not re.search(
        r"\b(?:fee|price|cost|date|schedule|venue|where|syllabus|outline|trainer|hrdc)\b",
        normalized,
    ):
        return TurnPlan(
            control,
            False,
            (Request("COURSE_INTRO", slug, "exact"),),
            0.99,
            "rule:friendly-course-opening",
        )

    # Detect every independent ask first. Resolution below applies scope and absorbs
    # related fields into a single coherent request where appropriate.
    requests: list[Request] = []
    if is_trust_question(normalized):
        requests.append(
            Request(
                "COMPANY",
                None,
                "retrieve",
                "Timmins company credentials, HRDC registration, clients, and contact details",
            )
        )

    identity_question = bool(
        re.search(
            r"\bwho\s+(?:is\s+this|are\s+you)\b|"
            r"\bwhat\s+is\s+(?:this\s+)?timmins\b",
            normalized,
        )
    )
    if identity_question:
        if not any(item.intent == "COMPANY" for item in requests):
            requests.append(Request("COMPANY", None, "exact", "Timmins company introduction"))

    company_question = bool(
        re.search(
            r"\b(?:who is|what is|about|where is)\s+timmins\b|"
            r"\bwhat (?:industries|industry) does timmins\b|"
            r"\bwho are (?:some of )?timmins'?\s+clients\b|"
            r"\btimmins\b.*\b(?:clients?|industries|hrdc registered|vendor registration)\b|"
            r"\bcan timmins support vendor registration\b",
            normalized,
        )
    )
    if company_question and not any(item.intent == "COMPANY" for item in requests):
        requests.append(Request("COMPANY", None, "retrieve", normalized))

    participant_replacement = bool(
        re.search(
            r"\b(?:replace|substitute|swap|change)\b.{0,35}\bparticipants?\b|"
            r"\bparticipants?\b.{0,35}\b(?:replace|replacement|substitute|swap|change)\b",
            normalized,
        )
    )
    hrdc_documents = bool(
        re.search(
            r"\b(?:what|which)\s+documents?\b.{0,45}\b(?:hrdc|hrdf|grant)\b|"
            r"\bdocuments?\s+(?:are\s+)?needed\b.{0,30}\b(?:hrdc|hrdf|grant)\b|"
            r"\b(?:hrdc|hrdf|grant)\b.{0,35}\b(?:documents?|paperwork)\b",
            normalized,
        )
    )
    operations_question = bool(
        re.search(
            r"\bwhat happens after (?:i |we )?regist(?:er|ration)\b|"
            r"\bhow do (?:i|we) register\b|"
            r"\bwhen (?:will|is).*calendar invite\b|"
            r"\bhow (?:long does|do).*hrdc approval\b|"
            r"\bwhen should.*hrdc applications?\b",
            normalized,
        )
    )
    if participant_replacement:
        requests.append(
            Request("PARTICIPANT_REPLACEMENT", None, "exact", "participant replacement policy")
        )
    if hrdc_documents:
        requests.append(Request("HRDC_DOCUMENTS", slug, "exact", "HRDC application documents"))
    if operations_question:
        requests.append(Request("OPERATIONS", None, "retrieve", normalized))

    beginner_fit = bool(
        re.search(
            r"\b(?:beginner|new to|no (?:prior )?experience|from scratch|"
            r"too old|old people|age limit|hard|difficult)\b",
            normalized,
        )
    )
    if beginner_fit:
        requests.append(
            Request(
                "BEGINNER_FIT",
                slug,
                "exact",
                "beginner suitability, prerequisites, and any named tool explained simply",
            )
        )

    discount_question = bool(
        re.search(
            r"\b(?:discount|group rate|group price|group booking|team rate|bulk rate|special pricing)\b|"
            r"\bteam\s+of\s+\d+\b|\b\d+\s+(?:people|participants|pax)\b.*\b(?:join|register)\b",
            normalized,
        )
    )
    if discount_question:
        requests.append(Request("DISCOUNT", slug, "exact", "documented group pricing"))

    value_question = bool(
        re.search(
            r"\bwhy should (?:i|we) (?:take|join|choose)\b|"
            r"\bworth\s+(?:it|rm\s*)?\d*\b|\bwhy\s+(?:is\s+)?(?:it|this|the course|the fee|fee)?\s*"
            r"(?:so\s+)?(?:expensive|costly)\b|\binstead of\b.*\b(?:youtube|videos?|self.study)\b",
            normalized,
        )
    )
    if value_question:
        requests.append(Request("COURSE_VALUE", slug, "exact", "course value and practical outcomes"))

    comparison_question = bool(
        re.search(
            r"\b(?:how is|what makes|what is)\b.*\b(?:different|difference)\b.*\b(?:course|embedded|linux|c|yocto|python|testing)\b|"
            r"\b(?:compare|versus|vs\.?|which is better)\b",
            normalized,
        )
    )
    if comparison_question:
        current = current_slug if current_slug in {item.slug for item in courses} else slug
        requests.append(
            Request("COURSE_COMPARISON", current, "exact", "compare the two named courses")
        )
    is_day_breakdown = bool(
        re.search(r"\bday[ -]by[ -]day\b|\bdaily breakdown\b|\beach day\b", normalized)
    )
    day = requested_day(normalized)
    content = bool(
        is_day_breakdown
        or day is not None
        or re.search(
            r"\b(?:syllabus|curriculum|outline|topics?|modules?|sessions?|covered|cover|"
            r"content|agenda|breakdown|course structure)\b",
            normalized,
        )
        # "learn" only counts when it is about THIS course. A bare "learn" made
        # "i want to learn mobile app development" return the current course's syllabus
        # instead of admitting we don't teach it.
        or re.search(
            r"\bwhat\s+(?:will|do|would|can)\s+(?:i|we)\s+learn\b|"
            r"\blearn\b.{0,25}\b(?:in|from|on)\s+(?:this|the)\s+"
            r"(?:course|training|programme|program|class)\b",
            normalized,
        )
        or re.search(r"\btell me about\b", normalized)
    )
    _cn = r"(?:courses?|trainings?|programmes?|programs?|classes?)"
    # A question about a specific fact is not a request for the catalogue, even when it says
    # "course": "what is the fee for the yocto course" matched the category+"course" pattern
    # below and was answered with the whole course list instead of the Yocto fee.
    specific_fact = bool(
        re.search(
            r"\b(?:fees?|price[sd]?|pricing|cost[s]?|how much|rm\d|"
            r"when|dates?|schedule|timing|venue|where|located|location|"
            r"how long|how many days|duration|trainer|instructor|"
            r"hrdc|claimable|certificate|certification)\b",
            normalized,
        )
    )
    # Phrasings that explicitly ask what is on offer. These stay catalogue questions even when
    # they also mention a fact ("what other courses are available and how much do they cost").
    catalog_explicit = bool(
        re.search(
            rf"\b(?:any|which|what)\s+(?:other\s+)?{_cn}\b|"
            rf"\b(?:any|other|available|all)\s+(?:\w+\s+){{0,3}}{_cn}\b|"
            rf"\b{_cn}\s+(?:available|catalog|list)\b|"
            rf"\b(?:tell me|know more|learn more|more info|more information|show me|list|"
            rf"give me|details|interested|looking for)\b.{{0,60}}\b{_cn}\b|"
            rf"\bwhat\s+(?:do you |can you )?(?:have|offer|provide)\b|"
            rf"\bdo you (?:have|offer|provide|teach|run|conduct)\b.{{0,30}}\b{_cn}\b",
            normalized,
        )
    )
    # A bare "<category> course" phrase, e.g. "embedded linux courses". This one also matches a
    # course NAMED inside a fact question ("the yocto course"), so it must not claim the turn
    # when a specific fact is being asked — that is FEES/SCHEDULE/... about that course.
    catalog_category = bool(
        re.search(
            rf"\b(?:embedded linux|embedded|software testing|python|linux kernel|yocto|kernel)"
            rf"\s+{_cn}\b",
            normalized,
        )
    )
    # "which course is beginner friendly" matches the generic "which ... course" catalogue
    # pattern too, which appended the whole course list after the fit answer. A fit question
    # is asking which course SUITS them, not for a browse.
    catalog = (catalog_explicit or (catalog_category and not specific_fact)) and not beginner_fit
    trainer_question = bool(
        re.search(
            r"\b(?:trainer|trainers|instructor|facilitator)\b|\bwho (?:teaches|is teaching)\b",
            normalized,
        )
    )
    trainer_catalog_scope = catalog or bool(
        trainer_question
        and re.search(r"\b(?:other|another)\s+(?:one|option|training|programme|program)\b", normalized)
    )
    trainer_catalog = trainer_catalog_scope and trainer_question
    if trainer_catalog:
        requests.append(
            Request(
                "TRAINER_CATALOG",
                None,
                "exact",
                "active courses with verified trainer profiles and requested course facts",
            )
        )
    elif catalog:
        requests.append(Request("CATALOG", None, "exact", "active course catalog"))
    elif content and not comparison_question:
        if day is not None:
            query = f"day {day} course agenda and covered topics"
        elif is_day_breakdown:
            query = "day-by-day course agenda and curriculum"
        else:
            query = "course syllabus, curriculum, and covered topics"
        requests.append(Request("COURSE_CONTENT", slug, "retrieve", query))

    exact_patterns: tuple[tuple[str, str, str], ...] = (
        ("FEES", r"\b(?:fee|fees|price|pricing|cost|how much|investment)\b|(?<!feel )\bfree\b", "course fee"),
        ("SCHEDULE", r"\b(?:schedule|dates?|when|next batch)\b", "course schedule and dates"),
        (
            "VENUE",
            r"\b(?:venue|location)\b|\bwhere\s+(?:is|are|will|would|does|do)\b|"
            r"\bwhere can (?:i|we) (?:attend|join|go|take)\b|\b(?:held|take place)\b",
            "course venue",
        ),
        ("HRDC", r"\b(?:hrdc|hrdf|claimable|grant)\b", "HRDC claimability"),
        ("TRAINER", r"\b(?:trainer|trainers|instructor|facilitator)\b", "course trainer"),
        ("PAYMENT", r"\b(?:payment|pay|bank transfer|invoice)\b", "payment options"),
        ("QUOTATION", r"\b(?:quotation|quote)\b", "quotation"),
        ("CANCELLATION", r"\b(?:cancel|cancellation|refund|reschedule)\b", "cancellation policy"),
        ("CERTIFICATION", r"\b(?:certificate|certification|certified)\b", "course certification"),
        ("BATCH_SIZE", r"\b(?:batch size|class size|participants|pax)\b", "batch size"),
        ("PLACEMENT", r"\b(?:placement|career support|job support)\b", "career support"),
        (
            "REQUIREMENTS",
            r"\b(?:prerequisite|prerequisites|requirements|eligible)\b",
            "prerequisites",
        ),
        ("ONLINE", r"\b(?:online|remote|virtual)\b", "online availability"),
    )
    # Facilities at the venue (parking, wifi, meals, prayer room...) are not the venue fact.
    # Answering "is there parking at the venue" with the address is wrong — these have to go
    # to the knowledge base, and be escalated to the consultant when unconfirmed.
    facility_question = bool(
        re.search(
            r"\b(?:parking|park|wifi|wi-fi|internet|surau|prayer room|"
            r"food|meals?|lunch|snacks?|refreshments?|drinks?|halal|"
            r"accommodation|hotel room|stay|transport|shuttle|"
            r"wheelchair|accessib\w*|dress code)\b",
            normalized,
        )
    )
    for intent, pattern, query in exact_patterns:
        if intent == "VENUE" and facility_question:
            continue
        if trainer_catalog and intent in {"FEES", "SCHEDULE", "VENUE", "HRDC", "TRAINER"}:
            continue
        if catalog and intent in {"FEES", "SCHEDULE", "VENUE", "HRDC", "TRAINER"}:
            continue
        if (
            intent == "HRDC"
            and any(item.intent == "COMPANY" for item in requests)
            and "timmins" in normalized
        ):
            continue
        if intent == "HRDC" and operations_question and "claimable" not in normalized:
            continue
        if intent == "HRDC" and hrdc_documents and "claimable" not in normalized:
            continue
        if intent == "FEES" and (discount_question or value_question):
            continue
        if intent == "BATCH_SIZE" and participant_replacement:
            continue
        if intent == "SCHEDULE" and operations_question and not re.search(
            r"\b(?:course schedule|course dates?|next batch)\b|"
            r"\bwhen\s+(?:is|does|will)\s+(?:(?:the|this)\s+)?course\b",
            normalized,
        ):
            continue
        if re.search(pattern, normalized) and not any(item.intent == intent for item in requests):
            requests.append(Request(intent, slug, "exact", query))

    # "day-by-day" describes content; it must never be collapsed to course duration.
    if (
        not is_day_breakdown
        and day is None
        and not catalog
        and re.search(r"\b(?:duration|how many days|how long)\b", normalized)
    ):
        requests.append(Request("DURATION", slug, "exact", "course duration"))

    if re.search(r"\b(?:contact details?|phone number|email address)\b", normalized):
        requests.append(Request("CONTACT", None, "exact", "company contact details"))

    if control != "none" and not requests:
        requests.append(Request("UNKNOWN", slug, "retrieve"))
    if not requests:
        return None
    return TurnPlan(
        control=control,
        answers_pending_slot=False,
        requests=tuple(requests),
        confidence=0.99,
        reason="rule:composed-high-confidence",
    )


def _enabled() -> bool:
    return os.getenv("USE_INTERPRETER", "true").lower() in {"1", "true", "yes", "on"}


def _profile_text(lead: dict | None) -> str:
    if not lead:
        return "(none)"
    rows = [
        f"{label}: {str(lead.get(key)).strip()}"
        for key, label in _PROFILE_FIELDS
        if str(lead.get(key) or "").strip()
    ]
    return "\n".join(rows) if rows else "(none)"


def _history_text(history: list[dict] | None, current: str) -> str:
    rows = []
    for item in (history or [])[-6:]:
        body = str(item.get("body") or "").strip()
        if not body or body == current:
            continue
        role = "Assistant" if item.get("direction") == "outbound" else "Customer"
        rows.append(f"{role}: {body}")
    return "\n".join(rows) if rows else "(none)"


def _catalog_text(courses) -> str:
    return "\n".join(f"{c.slug}: {c.name} ({c.dates})" for c in courses)


_SYSTEM_PROMPT = (
    "You are the single understanding step for a WhatsApp course-assistance bot (Timmins Training). "
    "For the LATEST MESSAGE, using recent conversation, the customer profile, the pending question "
    "(if the bot is running an intake form), and the active course catalog, output ONE JSON object:\n"
    '{"control": "none|human|stop", "answers_pending_slot": bool, '
    '"requests": [{"intent": <INTENT>, "course_slug": <slug or null>, "mode": "exact|retrieve", '
    '"query": <standalone rephrasing of just this one ask>}], '
    '"confidence": 0..1, "reason": <short string>}\n\n'
    "INTENT is one of: FEES, SCHEDULE, VENUE, DURATION, HRDC, PAYMENT, QUOTATION, CANCELLATION, "
    "CERTIFICATION, BATCH_SIZE, PLACEMENT, REQUIREMENTS, CONTACT, COMPANY, TRAINER, "
    "TRAINER_CATALOG, ONLINE, DISCOUNT, PARTICIPANT_REPLACEMENT, HRDC_DOCUMENTS, "
    "BOT_IDENTITY, FRUSTRATION, REPETITION_REPAIR, FREE_TUTORING, COURSE_INTRO, "
    "COURSE_CONFIRMATION, BEGINNER_FIT, COURSE_VALUE, COURSE_COMPARISON, CATALOG, "
    "RECOMMENDATION, COURSE_CONTENT, OPERATIONS, BOUNDARY, GREETING, SMALLTALK, "
    "CONTEXT_REPAIR, UNKNOWN.\n\n"
    "Rules:\n"
    "- control: judge from the LATEST MESSAGE ONLY. 'human' only if THIS message explicitly asks "
    "for a person/consultant/callback; 'stop' if THIS message wants to exit/quit/pause/"
    "unsubscribe; else 'none'. A handoff that already happened earlier does NOT make control 'human' "
    "now — if the latest message asks a normal question (fees, certification, dates...), control is 'none'.\n"
    "- COMPANY questions about Timmins itself ('who is Timmins', 'what does Timmins do', 'is Timmins "
    "HRDC registered', 'clients', 'industries', 'vendor registration') -> intent COMPANY, mode retrieve. "
    "General process questions such as what happens after registration, HRDC application documents, "
    "participant replacement, or calendar invitations -> intent OPERATIONS, mode retrieve. "
    "Questions about whether Timmins is legitimate or a scam -> COMPANY, mode retrieve. Rude or "
    "teasing messages that do not request a person -> BOUNDARY, mode exact, control none. "
    "'Are you a robot?' -> BOT_IDENTITY; frustration such as 'this is useless' -> FRUSTRATION; "
    "complaints about repeated information -> REPETITION_REPAIR. These are mode exact and must "
    "not be treated as requests for the fact mentioned inside the complaint. "
    "GREETING/SMALLTALK is ONLY for a bare greeting/thanks/acknowledgement, never for a question.\n"
    "- Discounts, group rates, a team of N, or special pricing -> DISCOUNT, mode exact, never "
    "control human. Participant replacement -> PARTICIPANT_REPLACEMENT, not VENUE or BATCH_SIZE. "
    "HRDC grant document/paperwork questions -> HRDC_DOCUMENTS, mode exact.\n"
    "- VENUE is ONLY the venue's name/address ('where is it held', 'which hotel', 'is it in Penang'). "
    "Questions about FACILITIES or amenities at or around the venue — parking, wifi, prayer room, "
    "meals/snacks/refreshments, halal food, accessibility, accommodation, transport, dress code — are "
    "NOT VENUE: use OPERATIONS, mode retrieve. Answering these with the venue address is wrong; they "
    "must be grounded in the knowledge base or escalated to the consultant.\n"
    "- CATALOG is ONLY for asking which courses exist, with NO specific course named ('what courses "
    "are there', 'list your trainings'). If the message names a course or refers to the current one "
    "and asks a fact about it, use that fact's intent with that course_slug: 'what is the fee for the "
    "yocto course' -> {FEES, exact, yocto slug}, NOT CATALOG. The word 'course' alone never means "
    "CATALOG.\n"
    "- Buyer questions about value, worth, price justification, or YouTube/self-study -> "
    "COURSE_VALUE, mode exact. Questions comparing the current course to another named course -> "
    "COURSE_COMPARISON, mode exact; the named course is the comparison target, not a course switch.\n"
    "- Beginner/difficulty/from-scratch/age concerns -> BEGINNER_FIT, mode exact. 'Teach me X for "
    "free' -> FREE_TUTORING, not TRAINER. Friendly unspecific openings about the current course -> "
    "COURSE_INTRO; checks such as 'is this the C one or Linux one?' -> COURSE_CONFIRMATION.\n"
    "- refund / cancellation / reschedule -> intent CANCELLATION, mode exact.\n"
    "- answers_pending_slot: only relevant when a PENDING QUESTION is shown. true iff the message is a "
    "plausible ANSWER to it (e.g. pending='years of experience' + '3' -> true), false if it is instead "
    "a question or request (e.g. 'what is the fee?' -> false).\n"
    "- requests: DECOMPOSE the message into one entry PER distinct ask. 'what is the syllabus and the "
    "price' -> two requests: {COURSE_CONTENT,retrieve,query='course syllabus / curriculum'} and "
    "{FEES,exact,query='course fee'}.\n"
    "- query: a short SELF-CONTAINED version of that single ask, so it can be answered on its own "
    "(do not leave in the other parts of a compound message).\n"
    "- mode: 'exact' for a single verbatim fact — how much (FEES), when (SCHEDULE), where (VENUE), how "
    "many days (DURATION), is it HRDC claimable (HRDC), who is the trainer (TRAINER), which other "
    "courses have trainer profiles (TRAINER_CATALOG), what courses exist (CATALOG). 'retrieve' for "
    "descriptive/why/how/curriculum asks. Examples that are "
    "RETRIEVE: 'day by day breakdown', 'what is covered', 'what will I learn', 'course outline' -> "
    "{COURSE_CONTENT,retrieve}.\n"
    "- course_slug: the course each request is about; if the user switched courses use the new slug; "
    "if none is named keep the current active course; use only slugs from the catalog, else null.\n"
    "- Greetings/thanks/smalltalk -> a single request with intent GREETING or SMALLTALK.\n"
    "Output JSON only, no prose."
)

_INTERPRETER_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "control": {"type": "string", "enum": ["none", "human", "stop"]},
        "answers_pending_slot": {"type": "boolean"},
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "intent": {"type": "string", "enum": sorted(VALID_INTENTS)},
                    "course_slug": {"type": ["string", "null"]},
                    "mode": {"type": "string", "enum": sorted(_MODES)},
                    "query": {"type": "string"},
                },
                "required": ["intent", "course_slug", "mode", "query"],
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": [
        "control",
        "answers_pending_slot",
        "requests",
        "confidence",
        "reason",
    ],
}


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        if not re.match(r"^\{\s*\{", cleaned):
            raise
        value = json.loads(cleaned[1:].lstrip())
    if not isinstance(value, dict):
        raise ValueError("interpreter output is not a JSON object")
    return value


def _interpreter_settings() -> tuple[str, str, str]:
    provider = os.getenv("INTERPRETER_PROVIDER", "groq").strip().lower()
    configured_model = os.getenv("INTERPRETER_MODEL", "").strip()
    if provider == "openrouter":
        api_key = (
            os.getenv("OPEN_ROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        ).strip()
        model = configured_model or "openai/gpt-oss-120b"
        return provider, api_key, model
    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        return provider, api_key, configured_model or "llama-3.1-8b-instant"
    if provider == "bedrock":
        return provider, f"aws:{bedrock_region()}", bedrock_model(configured_model)
    logger.warning("event=understand_invalid_provider provider=%s", provider)
    return provider, "", configured_model


def understand(
    message: str,
    *,
    course=None,
    lead: dict | None = None,
    history: list[dict] | None = None,
    pending_slot: str | None = None,
) -> TurnPlan | None:
    """Return the authoritative structured plan for this turn, or None (legacy fallback)."""
    if not _enabled():
        return None
    if not message.strip():
        return None

    courses = get_active_courses()
    known_slugs = {c.slug for c in courses}
    current_slug = course.slug if course is not None else (lead or {}).get("course") or None
    rule_plan = deterministic_plan(message, courses=courses, current_slug=current_slug)
    if rule_plan is not None:
        return rule_plan

    provider, api_key, model = _interpreter_settings()
    if not api_key:
        return None

    user_prompt = (
        f"ACTIVE COURSES:\n{_catalog_text(courses)}\n\n"
        f"CURRENT ACTIVE COURSE: {current_slug or '(none)'}\n\n"
        f"PENDING QUESTION (bot is waiting for this answer): {pending_slot or '(none — not in a form)'}\n\n"
        f"CUSTOMER PROFILE:\n{_profile_text(lead)}\n\n"
        f"RECENT CONVERSATION:\n{_history_text(history, message)}\n\n"
        f"LATEST MESSAGE:\n{message.strip()}"
    )

    try:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        if provider == "bedrock":
            content = converse_json(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=_INTERPRETER_RESPONSE_SCHEMA,
                schema_name="turn_plan",
                model=model,
                region=bedrock_region(),
                # Includes hidden GPT-OSS reasoning tokens on Bedrock.
                max_tokens=2400,
                timeout_seconds=20.0,
            )
            payload = _extract_json(content)
        elif provider == "openrouter":
            import requests

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            site_url = os.getenv("OPENROUTER_SITE_URL", "").strip()
            app_name = os.getenv("OPENROUTER_APP_NAME", "Timmins WhatsApp Assistant").strip()
            if site_url:
                headers["HTTP-Referer"] = site_url
            if app_name:
                headers["X-Title"] = app_name
            response = requests.post(
                os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
                + "/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 800,
                    "reasoning": {"effort": "low", "exclude": True},
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                },
                timeout=20.0,
            )
            if response.status_code >= 400:
                logger.warning(
                    "event=understand_failed provider=openrouter model=%s status=%d",
                    model,
                    response.status_code,
                )
                return None
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("OpenRouter response content must be a string")
            payload = _extract_json(content)
        else:
            from groq import Groq

            client = Groq(api_key=api_key, max_retries=1, timeout=8.0)
            response = client.chat.completions.create(
                model=model,
                temperature=0,
                max_tokens=400,
                response_format={"type": "json_object"},
                messages=messages,
            )
            payload = _extract_json(response.choices[0].message.content or "")
    except Exception as error:  # noqa: BLE001 — never fail the reply path
        logger.warning(
            "event=understand_failed provider=%s model=%s error=%s",
            provider,
            model,
            type(error).__name__,
        )
        return None

    # The course this message actually references (or the current course when it names
    # none) — the safe anchor when the model proposes an unwarranted switch.
    message_slug = _resolved_slug(message, courses, current_slug)

    def _slug(raw) -> str | None:
        s = str(raw).strip() if raw else ""
        if s not in known_slugs:
            return message_slug
        # Honour an LLM-proposed course only when it is the current course or the message
        # explicitly references it. Otherwise anchor to message_slug. This stops the model
        # from silently switching courses on a course-less question like "what is the
        # syllabus", which it may tag with a prominent slug (e.g. Software Testing).
        if s == current_slug or _message_references_slug(message, s, courses):
            return s
        return message_slug

    control = str(payload.get("control") or "none").strip().lower()
    if control not in {"none", "human", "stop"}:
        control = "none"
    answers_pending_slot = bool(payload.get("answers_pending_slot"))

    requests: list[Request] = []
    raw_requests = payload.get("requests")
    if isinstance(raw_requests, list):
        for item in raw_requests:
            if not isinstance(item, dict):
                continue
            intent = str(item.get("intent") or "UNKNOWN").strip().upper()
            if intent not in VALID_INTENTS:
                intent = "UNKNOWN"
            mode = str(item.get("mode") or "").strip().lower()
            if mode not in _MODES:
                mode = "exact" if intent in EXACT_INTENTS else "retrieve"
            requests.append(
                Request(
                    intent=intent,
                    course_slug=_slug(item.get("course_slug")),
                    mode=mode,
                    query=str(item.get("query") or "").strip(),
                )
            )
    if not requests:
        requests.append(Request(intent="UNKNOWN", course_slug=_slug(None), mode="retrieve"))

    try:
        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    return TurnPlan(
        control=control,
        answers_pending_slot=answers_pending_slot,
        requests=tuple(requests),
        confidence=confidence,
        reason=str(payload.get("reason") or "")[:200],
    )
