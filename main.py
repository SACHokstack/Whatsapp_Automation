import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

load_dotenv()

from rag_v2.query_expansion import normalize_query
from rag_v2.runtime import answer as rag_answer
from rag_v2.runtime import health as rag_health
from rag_v2.runtime import warmup as warmup_rag
from services.ai_reply import _deterministic_reply
from services.auto_outreach import dispatch_outreach
from services.auto_outreach import enabled as auto_outreach_enabled
from services.conversation_controller import decide_reply
from services.course_loader import (
    detect_course,
    detect_explicit_course,
    get_active_courses,
    get_course,
    load_courses,
    refresh_courses,
)
from services.durable_queue import (
    claim_event,
    complete_event,
    enqueue_webhook_body,
    fail_event,
    init_queue,
    queue_stats,
)
from services.fallbacks import warm_fallback
from services.google_sheets import (
    append_hot_lead,
    find_phone_in_workbook,
    get_rows_from,
    update_lead_in,
)
from services.interpret import CATALOG_INTENTS, understand
from services.knowledge_base import topic_for_message
from services.lead_sync import sync_leads
from services.logging_config import configure_logging, subject_id
from services.persistence import (
    add_message,
    get_conversation_history,
    get_dashboard_summary,
    get_lead,
    init_db,
    list_leads,
    upsert_lead,
)
from services.persistence import backend_name as persistence_backend_name
from services.response_guard import validate_reply
from services.structured_facts import exact_answer, load_policies
from services.whatsapp import _reply_delay, mark_read, send_template, send_text

configure_logging()

app = FastAPI()
logger = logging.getLogger(__name__)
seen_status_events: set[tuple[str, str]] = set()
seen_message_ids: set[str] = set()  # dedup incoming message events by wamid
_seen_status_order: deque[tuple[str, str]] = deque()
_seen_message_order: deque[str] = deque()
_MAX_SEEN_EVENTS = 10_000
_sender_locks: dict[str, threading.Lock] = {}
_sender_locks_guard = threading.Lock()


@app.exception_handler(Exception)
async def _json_unhandled_exception(request: Request, error: Exception):
    """Keep API failures machine-readable and attach a searchable server-side ID."""
    error_id = uuid.uuid4().hex[:12]
    logger.error(
        "event=unhandled_api_exception error_id=%s path=%s method=%s",
        error_id,
        request.url.path,
        request.method,
        exc_info=(type(error), error, error.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "The bot hit an internal error. Please retry after checking the server logs.",
            "error_id": error_id,
        },
    )


from web.pages import (  # inline page markup, kept out of this file
    _ADMIN_COURSES_BODY,
    _ADMIN_COURSES_SCRIPT,
    _ADMIN_HOME_BODY,
    _ADMIN_HOME_SCRIPT,
    _ADMIN_KNOWLEDGE_BODY,
    _ADMIN_KNOWLEDGE_SCRIPT,
    _ADMIN_LEADS_BODY,
    _ADMIN_LEADS_SCRIPT,
    _ADMIN_LOGIN_HTML,
    _RAG_TEST_CHAT_HTML,
    _WA_SIMULATOR_HTML,
    _admin_page,
)


def _remember_seen(value, seen: set, order: deque) -> None:
    if len(order) >= _MAX_SEEN_EVENTS:
        seen.discard(order.popleft())
    seen.add(value)
    order.append(value)


def _rag_test_db_path() -> str:
    return os.getenv("RAG_TEST_DB_PATH", "var/rag_test_chat.sqlite")


def _rag_test_connection():
    path = _rag_test_db_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_test_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS rag_test_messages_session_idx ON rag_test_messages(session_id, id)"
    )
    connection.commit()
    return connection


def _rag_test_history(session_id: str) -> list[dict[str, str]]:
    if not session_id:
        return []
    with _rag_test_connection() as connection:
        rows = connection.execute(
            """
            SELECT role, body
            FROM rag_test_messages
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
    return [{"role": row["role"], "body": row["body"]} for row in rows]


def _rag_test_history_for_runtime(session_id: str) -> list[dict[str, str]]:
    return [
        {
            "direction": "outbound" if item["role"] == "assistant" else "inbound",
            "body": item["body"],
        }
        for item in _rag_test_history(session_id)
    ]


def _rag_test_add_message(session_id: str, role: str, body: str) -> None:
    if role not in {"user", "assistant"}:
        raise ValueError("invalid rag test message role")
    with _rag_test_connection() as connection:
        connection.execute(
            "INSERT INTO rag_test_messages(session_id, role, body) VALUES (?, ?, ?)",
            (session_id, role, body),
        )
        connection.commit()


def _rag_test_reset(session_id: str) -> None:
    if not session_id:
        return
    with _rag_test_connection() as connection:
        connection.execute("DELETE FROM rag_test_messages WHERE session_id = ?", (session_id,))
        connection.commit()


def _lock_for_sender(sender: str) -> threading.Lock:
    with _sender_locks_guard:
        return _sender_locks.setdefault(sender, threading.Lock())


VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ENABLE_GOOGLE_SHEETS = os.getenv("ENABLE_GOOGLE_SHEETS", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
init_db()
init_queue()
_queue_wakeup = threading.Event()
_queue_shutdown = threading.Event()
_queue_worker: threading.Thread | None = None
_lead_sync_shutdown = threading.Event()
_lead_sync_worker: threading.Thread | None = None
_ingest_wakeup = threading.Event()
_ingest_shutdown = threading.Event()
_ingest_worker: threading.Thread | None = None


_AUTO_REPLY_SIGNALS = (
    "auto system",
    "auto reply",
    "auto-reply",
    "automatic reply",
    "not here right now",
    "out of office",
    "away message",
    "will respond as soon as",
    "i am using whatsapp",
    "i am currently unavailable",
    "this is an automated",
)


def _is_auto_reply(msg: str) -> bool:
    lower = msg.lower()
    return any(signal in lower for signal in _AUTO_REPLY_SIGNALS)


_FORM_FILL_RE = re.compile(r"i (?:filled?|fill)[^\n]*form", re.IGNORECASE)


_NULL_FORM_VALUES = {"no", "n/a", "na", "none", "nil", "-", "n.a", "n.a.", "not applicable"}


def _parse_form_fill(msg: str) -> dict:
    """Extract structured fields from Meta Lead Ad auto-message."""
    data: dict[str, str] = {}
    for field, pattern in (
        ("name", r"full[\s_]?name[:\s]+([^\n]+)"),
        ("email", r"e-?mail[:\s]+([^\n]+)"),
        ("company_name", r"company[\s_]?name[:\s]+([^\n]+)"),
        ("job_title", r"job[\s_]?title[:\s]+([^\n]+)"),
        ("who_will_pay", r"who will pay\??[:\s]+([^\n]+)"),
    ):
        m = re.search(pattern, msg, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val.lower() not in _NULL_FORM_VALUES:
                data[field] = val
    return data


def _detect_course_from_referral(referral: dict):
    """Detect course from Meta Lead Ad referral metadata (headline/body from the ad)."""
    if not referral:
        return None
    referral_values = [str(value) for value in referral.values() if value is not None]
    text = " ".join(
        [
            referral.get("headline", ""),
            referral.get("body", ""),
            referral.get("source_url", ""),
        ]
    ).lower()
    from services.course_loader import load_courses

    all_courses = load_courses()
    normalized_ids = {re.sub(r"\D", "", value) for value in referral_values}
    for course in all_courses.values():
        ad_ids = course.outreach.get("ad_ids") or []
        if any(re.sub(r"\D", "", str(ad_id)) in normalized_ids for ad_id in ad_ids):
            return course
    if not text.strip():
        return None
    matched = detect_course(text)
    if matched:
        return matched
    if "yoct" in text or "y0ct" in text:
        for slug, c in all_courses.items():
            if "yocto" in slug:
                return c
    if "elsi" in text or "system internals" in text or "internals" in text:
        for slug, c in all_courses.items():
            if "internals" in slug:
                return c
    return None


def _is_course_catalog_question(message: str) -> bool:
    # Use the same chat normalization as the authoritative interpreter. Without
    # this, a typo such as "courses availablel" bypasses catalog routing and the
    # legacy intent scorer mistakes "available" for a schedule question.
    lower = normalize_query(message)
    catalog_phrases = (
        "any course",
        "any embedded course",
        "which courses",
        "what courses",
        "embedded courses",
        "other embedded course",
        "other course",
        "other courses",
        "same duration",
        "this duration",
        "list of courses",
        "course list",
        "course names",
        "embedded linux course",
        "course coming up",
        "courses coming up",
        "upcoming courses",
        "training calendar",
    )
    return any(phrase in lower for phrase in catalog_phrases) or bool(
        re.search(
            r"\b(?:any|which|what)\s+(?:other\s+)?courses?\b|"
            r"\b(?:any|other|available|all)\s+(?:\w+\s+){0,3}courses?\b|"
            r"\bcourses?\s+(?:available|catalog|list)\b|"
            r"\b(?:tell me|know more|learn more|more info|more information|"
            r"details|interested)\b.{0,60}\bcourses\b|"
            r"\b(?:embedded linux|embedded|software testing|python|linux kernel)\s+courses\b",
            lower,
        )
    )


def _history_mentions_multiple_courses(history: list[dict] | None) -> bool:
    if not history:
        return False
    outbound = next(
        (
            str(item.get("body") or "").lower()
            for item in reversed(history)
            if item.get("direction") == "outbound"
        ),
        "",
    )
    matches = sum(1 for course in load_courses().values() if course.name.lower() in outbound)
    return matches >= 2


def _is_ambiguous_catalog_followup(message: str) -> bool:
    return message.lower().replace("’", "'").strip().rstrip("?") in {
        "this course",
        "that course",
        "this one",
        "that one",
        "how much for this course",
        "how much is this course",
    }


_PLAN_UNSET = object()
_GLOBAL_KB_INTENTS = CATALOG_INTENTS | {
    "COMPANY",
    "CONTACT",
    "OPERATIONS",
    "PARTICIPANT_REPLACEMENT",
}

# Pending qualification question per state — lets the interpreter judge whether a
# message is the ANSWER to it or an information request (see _process_conversation).
_SLOT_QUESTION = {
    "ASKING_EXPERIENCE_YEARS": "how many years of experience they have",
    "ASKING_TECHNOLOGIES": "which tools/technologies they use",
    "ASKING_MOTIVATION": "what prompted them to look into this training",
    "ASKING_LEARNING_GOALS": "what they hope to learn",
    "ASKING_FUNDING_PATH": "how they will fund it (company/HRDC or self-pay)",
    "ASKING_AVAILABILITY": "their availability / preferred dates",
}


def _pending_slot(lead: dict | None) -> str | None:
    state = _get_state(lead or {})
    if state.startswith(_COURSE_SELECT_STATE):
        return "which course they are asking about"
    return _SLOT_QUESTION.get(state)


def _plan_wants_human(plan) -> bool:
    """Trust interpreter escalation only when the LATEST turn has no concrete answerable
    ask. Guards against history-anchoring, where control=human bleeds from an earlier
    handoff onto a later real question (e.g. "is this certified" -> [CERTIFICATION]).
    A genuine handoff request carries no content ask, so it still escalates."""
    if plan is None or plan.control != "human":
        return False
    # CONTACT ("have someone ring me") is a vehicle for reaching a human, not a
    # substantive course question — so it permits escalation. FEES/CERTIFICATION/etc.
    # are real answers, so a control=human sitting beside them is anchoring noise.
    return not any(
        req.intent not in {"GREETING", "SMALLTALK", "UNKNOWN", "CONTACT"} for req in plan.requests
    )


def _plan_requests_quotation(plan) -> bool:
    """True when the turn asks for an official quotation/invoice — which a consultant must
    prepare. It flags the lead for a human (banner + notify) but, unlike an explicit
    'talk to a person' handoff, does NOT silence the bot: the customer can keep asking
    other questions while the quote is prepared."""
    return plan is not None and any(req.intent == "QUOTATION" for req in plan.requests)


def _plan_is_greeting_only(plan) -> bool:
    """True when the turn is purely a greeting/acknowledgement (no real ask) — the only
    case where the first-contact welcome should fire instead of answering."""
    return (
        plan is not None
        and plan.control == "none"
        and all(req.intent in {"GREETING", "SMALLTALK"} for req in plan.requests)
    )


_REASSERT_CUES = (
    (
        "VENUE",
        r"\b(?:venue|location|where|held|address|penang|kuala lumpur|\bkl\b|petaling|pjcc|ibis|hotel|centre|center|city)\b",
    ),
    ("FEES", r"\b(?:fee|fees|price|cost|ringgit|rm\s*\d|expensive|how much|free)\b"),
    ("SCHEDULE", r"\b(?:date|dates|when|schedule|start|august|july|month)\b"),
    ("DURATION", r"\b(?:how long|duration|days|hours)\b"),
    ("HRDC", r"\b(?:hrdc|hrdf|claimable|grant)\b"),
)


def _reassert_fact(msg: str, course) -> str | None:
    """When a fact-challenge names a verifiable topic (e.g. 'so is it KL or Penang'),
    RE-STATE the verified fact instead of abandoning it under pressure, and offer a
    consultant if they've seen otherwise. Returns None if no clear fact is challenged."""
    if course is None:
        return None
    lower = msg.lower()
    for intent, pattern in _REASSERT_CUES:
        if re.search(pattern, lower):
            fact = exact_answer(intent, course, message=msg)
            if fact:
                return (
                    f"Our verified information: {fact} "
                    "If you've seen something different, I can have a consultant confirm it for you."
                )
    return None


def _reply_from_plan(
    msg: str,
    course,
    plan,
    *,
    lead: dict | None = None,
    history: list[dict] | None = None,
    catalog: bool = False,
    trace_label: str | None = None,
) -> str:
    """Answer a turn from its authoritative TurnPlan: decompose requests, serve each
    exact fact deterministically or retrieve, then join — one plan, no re-parsing."""
    lead = lead or {}
    phone = lead.get("phone")

    # Honour course switches (first request's course), and keep the active course sticky.
    first_slug = next((r.course_slug for r in plan.requests if r.course_slug), None)
    resolved = get_course(first_slug) if first_slug else course
    if resolved is not None and phone and lead.get("course") != resolved.slug:
        upsert_lead(phone, course=resolved.slug)

    # Control signals (defensive: also handled upstream in _process_conversation).
    if _plan_wants_human(plan):
        if phone:
            upsert_lead(phone, **_human_handoff_update("requested a human/consultant"))
        return _human_handoff_reply(lead)

    # Pure greeting / smalltalk turns.
    if all(r.intent in {"GREETING", "SMALLTALK", "BOUNDARY"} for r in plan.requests):
        if any(r.intent == "BOUNDARY" for r in plan.requests):
            return (
                "I'm an automated course assistant. I may get things wrong, but I can help "
                "with courses, fees, schedules, HRDC, and registration."
            )
        if any(r.intent == "GREETING" for r in plan.requests):
            return (
                "Hi! I can help with course details, fees, schedule, HRDC, and more — "
                "what would you like to know?"
            )
        return "Happy to help — ask me anything about the courses, fees, schedule, or HRDC."

    # Topic repair / confusion recovery — route to deterministic handler that
    # references the recent conversation and clarifies rather than inventing.
    if any(r.intent == "CONTEXT_REPAIR" for r in plan.requests):
        reassert = _reassert_fact(msg, resolved)
        if reassert:
            return reassert
        return _deterministic_reply(msg, resolved, history=history, catalog=catalog)

    # Decompose: serve exact facts deterministically; answer each distinct descriptive
    # sub-question via RAG. For a SINGLE ask use the raw message (best retrieval
    # fidelity); only for a COMPOUND turn ("syllabus and price") fall back to the
    # interpreter's per-request sub-query so the parts don't confuse each other.
    content_reqs = [r for r in plan.requests if r.intent not in {"GREETING", "SMALLTALK"}]
    compound = len(content_reqs) > 1
    exact_parts: list[str] = []
    # (question shown to generation, standalone query used by retrieval, course)
    rag_queries: list[tuple[str, str, object | None]] = []
    for req in content_reqs:
        req_course = (
            None
            if req.intent in _GLOBAL_KB_INTENTS
            else get_course(req.course_slug)
            if req.course_slug
            else resolved
        )
        standalone_query = req.query or msg
        generation_question = (
            standalone_query if compound or req.intent in _GLOBAL_KB_INTENTS else msg
        )
        if req.mode == "exact":
            exact_course = None if req.intent in _GLOBAL_KB_INTENTS else req_course
            answer = exact_answer(req.intent, exact_course, message=msg)
            if answer:
                exact_parts.append(answer)
            else:
                rag_queries.append((generation_question, standalone_query, req_course))
        else:
            rag_queries.append((generation_question, standalone_query, req_course))

    parts = list(dict.fromkeys(p for p in exact_parts if p and p.strip()))
    unique_rag_queries = list(
        {
            (question, retrieval_query, getattr(query_course, "slug", None)): (
                question,
                retrieval_query,
                query_course,
            )
            for question, retrieval_query, query_course in rag_queries
        }.values()
    )
    for question, retrieval_query, query_course in unique_rag_queries[:2]:
        rag_part = rag_answer(
            question,
            retrieval_query=retrieval_query,
            course=query_course,
            lead=lead,
            history=history,
            trace_label=trace_label,
        )
        if not rag_part or not rag_part.strip() or rag_part in parts:
            continue
        # Don't tack a bare abstention onto parts we already answered well.
        if parts and "don't have enough verified information" in rag_part.lower():
            continue
        parts.append(rag_part)
    if not parts:
        parts.append(
            rag_answer(msg, course=resolved, lead=lead, history=history, trace_label=trace_label)
        )
    reply = "\n\n".join(parts)

    plan_has_catalog_scope = any(req.intent in CATALOG_INTENTS for req in plan.requests)
    validation = validate_reply(reply, course=resolved, catalog=catalog or plan_has_catalog_scope)
    if not validation.valid:
        logger.error("event=response_rejected reason=%s route=plan", validation.reason)
        return warm_fallback(resolved)
    return reply


def _faq_reply(
    msg: str,
    course=None,
    *,
    lead: dict | None = None,
    history: list[dict] | None = None,
    catalog: bool = False,
    trace_label: str | None = None,
    plan=_PLAN_UNSET,
) -> str:
    """Answer with structured facts first; reserve RAG for descriptive content."""
    # Preferred path: the authoritative TurnPlan drives routing. `plan` is normally
    # supplied by the caller (computed once for the whole turn); compute it here only
    # when it was not passed at all.
    if plan is _PLAN_UNSET:
        plan = understand(
            msg, course=course, lead=lead, history=history, pending_slot=_pending_slot(lead)
        )
    if plan is not None:
        return _reply_from_plan(
            msg, course, plan, lead=lead, history=history, catalog=catalog, trace_label=trace_label
        )

    # Legacy fallback (interpreter disabled/unavailable): original keyword routing.
    safe_mode = os.getenv("BOT_SAFE_MODE", "true").lower() in {"1", "true", "yes", "on"}
    decision = decide_reply(
        msg,
        course=course,
        catalog=catalog,
        history=history,
        safe_mode=safe_mode,
    )
    if decision.intent == "COURSE_CONTENT_WITH_FEES":
        reply = _deterministic_reply(msg, course, history=history, catalog=catalog)
    elif decision.route == "social_ack":
        reply = "No worries! Let me know if there's anything I can help you with."
    elif decision.route == "exact":
        reply = exact_answer(decision.intent, course, message=msg)
        if reply is None:
            reply = _deterministic_reply(msg, course, history=history, catalog=catalog)
    elif decision.route == "clarify":
        if decision.reason == "course required":
            reply = (
                "I can help with that, but I don't want to give you details for the wrong course. "
                "Which course are you interested in?"
            )
        else:
            reply = (
                "I want to make sure I answer the right question. Are you asking about a course's "
                "fees, schedule, curriculum, HRDC status, or something else?"
            )
    elif decision.route == "context":
        reply = _deterministic_reply(msg, course, history=history, catalog=False)
    else:
        reply = rag_answer(
            msg,
            course=course,
            lead=lead,
            history=history,
            trace_label=trace_label,
        )
    validation = validate_reply(reply, course=course, catalog=catalog)
    if not validation.valid:
        logger.error("event=response_rejected reason=%s", validation.reason)
        return warm_fallback(course)
    return reply


def _resolve_course(lead: dict, msg: str):
    # In "how is this different from Embedded C?", the named course is the
    # comparison target, not a request to switch away from the current course.
    # Preserve the sticky course so the comparison has both sides.
    if re.search(
        r"\b(?:compare|comparison|different|difference|versus|vs\.?|which is better)\b",
        msg,
        re.IGNORECASE,
    ):
        current = get_course(lead.get("course") or "")
        if current is not None:
            return current
    explicit = detect_explicit_course(msg)
    if explicit:
        return explicit
    slug = lead.get("course") or ""
    if slug:
        return get_course(slug)
    # During active qualification the lead's answers (e.g. "python") would
    # incorrectly match a different course keyword — skip detection.
    state = (lead.get("conversation_state") or lead.get("qualification_step") or "").strip().upper()
    if state in _ACTIVE_STATES:
        return None
    course = detect_course(msg)
    return course


_SHEET_STATUS_VALUES = {
    "HOT",
    "WARM",
    "COLD",
    "CONTACTED",
    "ENGAGED",
    "NEEDS_HUMAN",
    "NOT_INTERESTED",
    "BOT_PAUSED",
}


def _update_sheet_if_enabled(
    phone: str, worksheet_name: str | None = None, workbook_name: str | None = None, **kwargs
) -> bool:
    if not ENABLE_GOOGLE_SHEETS:
        logger.debug("event=sheet_write_skipped reason=disabled")
        return False

    # Map internal 'status' key to sheet's 'lead_status' column.
    # Only write meaningful status values — skip conversation state names.
    sheet_kwargs = {}
    for k, v in kwargs.items():
        if k == "status":
            if str(v).upper() in _SHEET_STATUS_VALUES:
                sheet_kwargs["lead_status"] = v
        elif k not in ("conversation_state", "qualification_step"):
            sheet_kwargs[k] = v

    if not sheet_kwargs:
        return False

    if not worksheet_name:
        logger.warning(
            "event=sheet_write_skipped reason=no_worksheet subject=%s", subject_id(phone)
        )
        return False

    try:
        result = update_lead_in(phone, worksheet_name, workbook_name, **sheet_kwargs)
        logger.info(
            "event=sheet_write workbook=%s worksheet=%s subject=%s fields=%s result=%s",
            workbook_name or "default",
            worksheet_name,
            subject_id(phone),
            ",".join(sheet_kwargs),
            result,
        )
        return result
    except Exception:
        logger.exception("event=sheet_write_failed worksheet=%s", worksheet_name)
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_int(text: str) -> int | None:
    match = re.search(r"(\d[\d,]*)", text or "")
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _extract_availability_days(text: str) -> int | None:
    text_lower = (text or "").strip().lower()
    if not text_lower:
        return None
    if text_lower in {"immediately", "now", "today", "asap"} or "immediate" in text_lower:
        return 0
    return _extract_int(text_lower)


def _lead_score(data: dict[str, str], course=None) -> int:
    score = 0

    # Years of experience
    years = _extract_int(data.get("experience_years") or "")
    if years is not None:
        if years >= 3:
            score += 3
        elif years >= 1:
            score += 1

    # Funding path — company/HRDC shows commitment
    funding = (data.get("funding_path") or data.get("who_will_pay") or "").strip().upper()
    if any(f in funding for f in ("COMPANY", "EMPLOYER", "HRDC", "SPONSOR")):
        score += 3
    elif any(f in funding for f in ("SELF", "OWN", "PERSONAL")):
        score += 1

    # Technologies relevant to the selected course. A useful free-text answer
    # earns one point; matching course terminology earns a second point.
    tech = (data.get("technologies") or "").lower()
    if tech.strip():
        score += 1
        course_keywords = getattr(course, "keywords", ()) if course else ()
        if any(keyword in tech for keyword in course_keywords if len(keyword) >= 3):
            score += 1

    # Respect each course's configured budget threshold when budget data exists.
    budget = _extract_int(data.get("budget") or "")
    budget_threshold = getattr(course, "hot_budget_threshold", None) if course else None
    if budget is not None and budget_threshold and budget >= budget_threshold:
        score += 2

    # Availability
    days = _extract_availability_days(data.get("availability") or "")
    if days is not None and days <= 30:
        score += 2

    return score


def _lead_status_from_score(score: int) -> str:
    if score >= 7:
        return "HOT"
    if score >= 4:
        return "WARM"
    return "COLD"


# --- Qualification question builders ---


def _experience_years_prompt(lead: dict) -> str:
    job_title = (lead.get("job_title") or "").strip()
    company = (lead.get("company_name") or "").strip()
    article = "an" if job_title[:1].lower() in "aeiou" else "a"
    if job_title and company:
        return (
            f"Great! As {article} {job_title} at {company}, how many years of experience do you have?\n\n"
            f"e.g. 2 years, 5 years, less than 1 year"
        )
    if job_title:
        return (
            f"Great! As {article} {job_title}, how many years of experience do you have?\n\n"
            f"e.g. 2 years, 5 years, less than 1 year"
        )
    return (
        "Great! How many years of experience do you have in your current role?\n\n"
        "e.g. 2 years, 5 years, less than 1 year"
    )


def _course_prompt_examples(course) -> tuple[str, str]:
    slug = getattr(course, "slug", "")
    if "yocto" in slug:
        return "Yocto, BitBake, C, Linux", "build custom images, recipes, layers, or BSPs"
    if "embedded-c" in slug:
        return "C, GDB, JTAG, microcontrollers", "debug firmware and write safer embedded C"
    if "linux-debugging" in slug:
        return "GDB, perf, ftrace, Valgrind", "diagnose crashes and performance problems"
    if "linux-internals" in slug:
        return "C, Linux, kernel modules, Buildroot", "understand boot flow and Linux internals"
    if "linux-kernel" in slug:
        return "C, kernel modules, device drivers", "write and debug Linux kernel modules"
    if "python" in slug:
        return "Python, pandas, APIs, Excel", "automate engineering and reporting tasks"
    return "Selenium, Playwright, Postman, Jira", "move into automation and improve testing skills"


def _technologies_prompt(lead: dict, course=None) -> str:
    job_title = (lead.get("job_title") or "").strip()
    technology_examples, _ = _course_prompt_examples(course)
    article = "an" if job_title[:1].lower() in "aeiou" else "a"
    if job_title:
        return (
            f"Which tools or technologies do you use as {article} {job_title}?\n\n"
            f"For example: {technology_examples}. Just share whatever you use day to day."
        )
    return (
        "Which tools or technologies do you use in your work?\n\n"
        f"For example: {technology_examples}. Just share whatever you use day to day."
    )


def _motivation_prompt() -> str:
    return (
        "What prompted you to look into this training?\n\n"
        "e.g. Want to upskill, company asked me to, exploring options, heard from a colleague — anything works!"
    )


def _learning_goals_prompt(course=None) -> str:
    _, goal_examples = _course_prompt_examples(course)
    return f"What are you hoping to learn from this training?\n\nFor example: {goal_examples}."


def _funding_prompt() -> str:
    return (
        "How are you planning to fund this training?\n\n"
        "1. Company / HRDC — my employer is sponsoring\n"
        "2. Self-pay — I am paying myself\n\n"
        "Just reply 1 or 2, or type it out."
    )


def _availability_prompt() -> str:
    return (
        "Last one — when are you looking to start?\n\n"
        "e.g. Immediately, next month, after Raya, in 2 weeks — whatever suits you!"
    )


def _final_qualification_reply() -> str:
    return (
        "Thank you for sharing that! Our consultant will review your profile and reach out to you shortly.\n\n"
        "In the meantime, feel free to ask me anything about the course, fees, or schedule."
    )


_GREETING_WORDS = {
    "hi",
    "hello",
    "hey",
    "hiya",
    "helo",
    "hai",
    "good morning",
    "good afternoon",
    "good evening",
}
# Short acks that feel like greetings but shouldn't re-trigger the welcome
_PHATIC_ACKS = {"thanks", "thank you", "ok", "okay", "noted", "alright", "sure", "got it", "k"}

_OPT_OUT_SIGNALS = (
    "not interested",
    "no thanks",
    "no thank you",
    "stop messaging",
    "stop message",
    "unsubscribe",
    "do not contact",
    "don't contact",
)

_STOP_CONVERSATION_SIGNALS = (
    "let's stop",
    "lets stop",
    "stop it",
    "stop this",
    "end this chat",
    "end the conversation",
    "no more replies",
    "don't reply",
    "do not reply",
)

# Canonical one-word stop commands, matched as the whole message (deterministic
# fast-path); the interpreter's `stop` flag handles paraphrases beyond these.
_STOP_COMMANDS = {"exit", "quit", "stop", "leave", "unsubscribe", "cancel"}

_RESUME_CONVERSATION_SIGNALS = {
    "resume",
    "resume chat",
    "start again",
    "continue chat",
}

_FAREWELL_SIGNALS = {"bye", "goodbye", "see you", "talk later", "bye bye"}

_INFO_REQUEST_SIGNALS = (
    "fee",
    "cost",
    "price",
    "schedule",
    "date",
    "when",
    "venue",
    "where",
    "hrdc",
    "claimable",
    "curriculum",
    "breakdown",
    "details",
    "learn",
    "syllabus",
    "course content",
    "prerequisite",
    "requirement",
    "trainer",
    "email",
    "phone",
    "contact",
    "procedure",
    "process",
)


def _is_greeting(msg: str) -> bool:
    return msg.strip().lower() in (_GREETING_WORDS | _PHATIC_ACKS)


def _first_contact_greeting(lead: dict, course) -> str:
    first_name = (lead.get("name") or "").strip().split()[0] if lead.get("name") else ""
    greeting = f"Hi {first_name}! " if first_name else "Hi! "
    course_name = (
        getattr(course, "name", "our upcoming training program")
        if course
        else "our upcoming training program"
    )
    return (
        f"{greeting}Thanks for getting back to us.\n\n"
        f"I'm Timmins' assistant for *{course_name}*.\n\n"
        f"I can help you with course details, fees, schedule, HRDC, and more — just ask me anything.\n\n"
        f"Or reply *Interested* and I'll ask you a few quick questions so our consultant can follow up with the right information for you."
    )


def _form_fill_greeting(lead: dict, course) -> str:
    """Personalised reply for a Meta Lead Form auto-message that already contains the lead's profile."""
    name = (lead.get("name") or "").strip()
    first_name = name.split()[0] if name else ""
    job_title = (lead.get("job_title") or "").strip()
    company = (lead.get("company_name") or "").strip()
    who_pays = (lead.get("who_will_pay") or "").strip().lower()
    course_name = getattr(course, "name", None) if course else None

    greeting = f"Hi {first_name}! " if first_name else "Hi! "
    lines = [greeting + "Thanks for reaching out to Timmins."]

    if job_title and company:
        article = "an" if job_title[:1].lower() in "aeiou" else "a"
        lines.append(f"I can see you're {article} {job_title} at {company}.")
    elif job_title:
        article = "an" if job_title[:1].lower() in "aeiou" else "a"
        lines.append(f"I can see you're {article} {job_title}.")
    elif company:
        lines.append(f"I can see you're from {company}.")

    if course_name:
        lines.append(f"You've shown interest in our *{course_name}* program.")

    if any(w in who_pays for w in ("company", "hrdc", "employer", "sponsor")):
        lines.append(
            "Good news — since your company is sponsoring, this training is HRDC claimable and we'll help with the grant application."
        )
    elif any(w in who_pays for w in ("self", "own", "personal")):
        lines.append("We have flexible self-pay options available too.")

    lines.append(
        "What would you like to know? Feel free to ask about the schedule, fees, curriculum, or HRDC process."
    )

    return "\n\n".join(lines)


# --- State helpers ---

_ACTIVE_STATES = {
    "ASKING_EXPERIENCE_YEARS",
    "ASKING_TECHNOLOGIES",
    "ASKING_MOTIVATION",
    "ASKING_LEARNING_GOALS",
    "ASKING_FUNDING_PATH",
    "ASKING_AVAILABILITY",
}


def _get_state(lead: dict) -> str:
    return (lead.get("conversation_state") or lead.get("qualification_step") or "").strip().upper()


_COURSE_SELECT_STATE = "ASKING_COURSE_SELECT"

# Intents whose answer differs per course, so they cannot be answered until we know which one.
_COURSE_SCOPED_INTENTS = {
    "FEES",
    "SCHEDULE",
    "VENUE",
    "DURATION",
    "HRDC",
    "CERTIFICATION",
    "BATCH_SIZE",
    "PLACEMENT",
    "REQUIREMENTS",
    "TRAINER",
    "ONLINE",
    "COURSE_CONTENT",
}


def _course_picker(intent_hint: str | None = None) -> str:
    """A numbered list to choose from, instead of asking "which course?" over and over."""
    courses = get_active_courses()
    lines = [f"{index}. {item.name}" for index, item in enumerate(courses, start=1)]
    subject = {
        "FEES": "the fee",
        "SCHEDULE": "the dates",
        "DURATION": "the duration",
        "VENUE": "the venue",
        "CERTIFICATION": "certification",
        "TRAINER": "the trainer",
        "REQUIREMENTS": "the prerequisites",
        "BATCH_SIZE": "the class size",
        "COURSE_CONTENT": "what it covers",
    }.get(intent_hint or "", "the details")
    return (
        f"Happy to give you {subject} — which course did you mean?\n\n"
        + "\n".join(lines)
        + "\n\nJust reply with the number or the course name."
    )


def _course_from_selection(message: str):
    """Resolve a picker reply: a number, or a course named in words."""
    courses = get_active_courses()
    picked = re.fullmatch(r"\s*(\d{1,2})\s*[.)]?\s*", message or "")
    if picked:
        index = int(picked.group(1))
        if 1 <= index <= len(courses):
            return courses[index - 1]
        return None
    return detect_explicit_course(message) or detect_course(message)


def _plan_is_new_question(plan, message: str) -> bool:
    """Is this a fresh ask rather than an attempt to pick a course from the picker?

    Mirrors how the qualification machine defers to the plan before consuming a message
    as a slot answer: understanding is authoritative, so a state never eats a real question.
    """
    if plan is not None:
        substantive = [
            request.intent
            for request in plan.requests
            if request.intent not in {"GREETING", "SMALLTALK", "UNKNOWN", "COURSE_CONFIRMATION"}
        ]
        if plan.control in {"human", "stop"}:
            return True
        return bool(substantive)
    # No interpreter: treat anything question-shaped as a new ask, but leave a bare word or
    # two ("softwre testng", "the linux one") as a mis-typed selection worth re-asking.
    text = (message or "").strip()
    return bool(text) and ("?" in text or len(text.split()) > 3)


def _course_select_pending(state: str) -> str | None:
    """The intent we were about to answer, parked in the state string as
    "ASKING_COURSE_SELECT:FEES" so it needs no extra database column."""
    if not state.startswith(_COURSE_SELECT_STATE):
        return None
    _, _, intent = state.partition(":")
    return intent or "UNKNOWN"


_FUNDING_STATEMENTS = (
    (
        "Company/HRDC",
        r"\b(?:my|our)\s+(?:company|employer|organisation|organization)\b|"
        r"\b(?:company|employer|organisation|organization)\s+(?:will|is|would|can)\b|"
        r"\bhrdc\s+(?:will\s+)?(?:cover|claim|fund|sponsor)|\bcompany[- ]sponsored\b|"
        r"\bsponsored\s+by\s+(?:my|our|the)\s+(?:company|employer)\b",
    ),
    (
        "Self-pay",
        r"\bself[\s-]?(?:pay|paying|paid|funded|sponsored|sponsor)\b|"
        r"\bpaying\s+(?:for\s+)?(?:it\s+)?myself\b|\bmy\s+own\s+(?:pocket|expense|money)\b|"
        r"\bi\s+(?:will|'ll|am|am going to)\s+(?:be\s+)?pay(?:ing)?\b",
    ),
)


def _funding_from_statement(message: str) -> str | None:
    """Funding volunteered as a STATEMENT, e.g. "my company will pay for it".

    Questions are excluded: "is it HRDC claimable?" asks for a fact and must still be
    answered, not silently recorded as this lead's funding path.
    """
    text = (message or "").strip().lower()
    if not text or "?" in text:
        return None
    if re.match(r"^(?:is|are|can|could|do|does|did|will|would|what|how|when|who|why)\b", text):
        return None
    for value, pattern in _FUNDING_STATEMENTS:
        if re.search(pattern, text):
            return value
    return None


def _reprompt_for_state(state: str, lead: dict, course) -> str | None:
    """Re-ask the question the lead is currently on."""
    if state == "ASKING_EXPERIENCE_YEARS":
        return _experience_years_prompt(lead)
    if state == "ASKING_TECHNOLOGIES":
        return _technologies_prompt(lead, course)
    if state == "ASKING_MOTIVATION":
        return _motivation_prompt()
    if state == "ASKING_LEARNING_GOALS":
        return _learning_goals_prompt(course)
    if state == "ASKING_AVAILABILITY":
        return _availability_prompt()
    return None


def _state_update(state: str, **extra) -> dict:
    return {
        "status": state,
        "conversation_state": state,
        "qualification_step": state,
        **extra,
    }


_EXPLICIT_INTEREST_REPLIES = {
    "interested",
    "i am interested",
    "i'm interested",
    "im interested",
    "yes interested",
    "yes, interested",
    "yes i am interested",
    "yes i'm interested",
    "yes im interested",
    "sign me up",
    "i want to join",
    "i want to register",
    "i want to enroll",
    "i want to enrol",
}

_QUESTION_STARTERS = (
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
)


def _looks_like_information_request(message: str) -> bool:
    lower = message.lower().replace("’", "'").strip()
    if not lower:
        return False
    if "?" in lower:
        return True
    words = set(re.findall(r"[a-z]+", lower))
    if words.intersection(_QUESTION_STARTERS):
        return True
    return any(signal in lower for signal in _INFO_REQUEST_SIGNALS)


def _is_explicit_interest_reply(message: str) -> bool:
    lower = message.lower().replace("’", "'").strip().strip(".!")
    if not lower or _looks_like_information_request(lower):
        return False
    if lower in _EXPLICIT_INTEREST_REPLIES:
        return True
    return bool(
        re.fullmatch(
            r"(yes[, ]+)?(i\s+)?(want|would like|wish)\s+to\s+"
            r"(join|register|enroll|enrol|sign\s+up)(\s+(for|for this|for the course|this course))?",
            lower,
        )
    )


def _human_handoff_reply(lead: dict) -> str:
    first_name = (lead.get("name") or "").strip().split()[0] if lead.get("name") else ""
    name_part = f", {first_name}" if first_name else ""
    company = load_policies()["company"]
    phone = company["phone"]
    email = company["email"]
    return (
        f"Of course{name_part}. I'll flag this to our consultant right away and someone "
        f"will reach out to you shortly. You can also call or WhatsApp us directly at {phone}, "
        f"or email {email}."
    )


def _human_handoff_update(reason: str) -> dict[str, str]:
    return {
        "needs_human": "YES",
        "human_reason": reason,
        "human_status": "OPEN",
        "human_updated_at": _utc_now(),
    }


def _handoff_notify_phone() -> str:
    raw = (
        os.getenv("HANDOFF_NOTIFY_PHONE")
        or os.getenv("SUPPORT_NOTIFY_PHONE")
        or os.getenv("CONSULTANT_NOTIFY_PHONE")
        or ""
    )
    return re.sub(r"\D", "", raw)


def _format_handoff_notification(
    sender: str,
    lead: dict[str, str],
    *,
    reason: str,
    source: str,
    intent: str = "",
    message: str = "",
    worksheet_name: str | None = None,
    workbook_name: str | None = None,
) -> str:
    def value(key: str, default: str = "-") -> str:
        return str(lead.get(key) or default).strip() or default

    lines = [
        "New WhatsApp handoff needed",
        "",
        f"Reason: {reason}",
        f"Source: {source}",
        f"Name: {value('name', 'Unknown')}",
        f"Customer phone: {sender}",
        f"Course: {value('course', 'Unknown')}",
    ]
    if message.strip():
        lines.append(f"Last message: {message.strip()[:400]}")
    if intent:
        lines.append(f"Intent: {intent}")
    if value("company_name") != "-":
        lines.append(f"Company: {value('company_name')}")
    if value("job_title") != "-":
        lines.append(f"Job title: {value('job_title')}")
    if value("funding_path") != "-" or value("who_will_pay") != "-":
        lines.append(f"Funding: {value('funding_path', value('who_will_pay'))}")
    if worksheet_name:
        sheet_ref = worksheet_name if not workbook_name else f"{workbook_name} / {worksheet_name}"
        lines.append(f"Sheet: {sheet_ref}")
    lines.append("")
    lines.append("Action: Please reply/call the customer and close the handoff after follow-up.")
    return "\n".join(lines)


def _notify_handoff_owner(
    sender: str,
    lead: dict[str, str],
    *,
    reason: str,
    source: str,
    intent: str = "",
    message: str = "",
    worksheet_name: str | None = None,
    workbook_name: str | None = None,
) -> bool:
    if sender.startswith("sim-") and os.getenv("HANDOFF_NOTIFY_SIMULATOR", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.info("event=handoff_notify_skipped reason=simulator subject=%s", subject_id(sender))
        return False

    notify_phone = _handoff_notify_phone()
    if not notify_phone:
        logger.info(
            "event=handoff_notify_skipped reason=no_notify_phone subject=%s", subject_id(sender)
        )
        return False
    if notify_phone == re.sub(r"\D", "", sender):
        logger.warning(
            "event=handoff_notify_skipped reason=notify_phone_is_customer subject=%s",
            subject_id(sender),
        )
        return False

    def _lead_value(key: str, default: str = "-") -> str:
        return str(lead.get(key) or default).strip() or default

    template_name = os.getenv("HANDOFF_NOTIFY_TEMPLATE", "timmins_handoff_alert")
    customer_name = _lead_value("name", "Unknown")
    course_name = _lead_value("course", "Unknown")
    last_msg = message.strip()[:200] or "-"
    named_variables = {
        "customer_name": customer_name,
        "customer_phone": sender,
        "course_name": course_name,
        "reason": reason,
        "last_message": last_msg,
    }

    try:
        response = send_template(
            notify_phone, template_name, language_code="en", named_variables=named_variables
        )
    except requests.RequestException:
        logger.exception(
            "event=handoff_notify_failed reason=request_exception subject=%s notify_subject=%s",
            subject_id(sender),
            subject_id(notify_phone),
        )
        return False

    logger.info(
        "event=handoff_notify status_code=%s ok=%s subject=%s notify_subject=%s",
        response.status_code,
        response.ok,
        subject_id(sender),
        subject_id(notify_phone),
    )
    return bool(response.ok)


def _process_conversation(
    message: str, lead: dict[str, str], course=None, plan=None
) -> tuple[str | None, dict[str, str | int] | None]:
    msg_lower = message.lower().strip()
    state = _get_state(lead)
    lead_status = (lead.get("status") or "").upper()

    is_interested = _is_explicit_interest_reply(message)
    is_opt_out = any(signal in msg_lower for signal in _OPT_OUT_SIGNALS)

    # Lifecycle control signals: keyword fast-path first, then the interpreter catches
    # paraphrases the keyword lists miss (e.g. "i want to call and speak to someone",
    # "exit") — crucial mid-qualification, where messages are otherwise eaten as answers.
    human_reason = _human_escalation_reason(msg_lower)
    if not human_reason and _plan_wants_human(plan):
        human_reason = "Requested human agent"
    if human_reason:
        return _human_handoff_reply(lead), _human_handoff_update(human_reason)

    # A quotation/invoice request needs a consultant to prepare pricing. Flag the lead for
    # a human (banner + owner notification via the state_updates path) but return no reply,
    # so the FAQ path still gives the customer the quotation acknowledgement + "flagged to a
    # consultant" message. The reason does NOT start with "Requested", so _human_handoff_open
    # stays False and the bot keeps answering other questions while the quote is prepared.
    # Skip if a handoff is already open, to avoid re-notifying on a repeated ask.
    handoff_open = (lead.get("needs_human") or "").upper() == "YES" and (
        lead.get("human_status") or ""
    ).upper() == "OPEN"
    if _plan_requests_quotation(plan) and not handoff_open:
        return None, {
            **_human_handoff_update("Quotation/invoice requested — consultant to prepare"),
            "last_intent": "QUOTATION",
        }

    if (
        msg_lower.strip(" .!?") in _STOP_COMMANDS
        or any(signal in msg_lower for signal in _STOP_CONVERSATION_SIGNALS)
        or (plan is not None and plan.control == "stop")
    ):
        return (
            "Understood. I'll stop the automated replies here.",
            {
                "status": "BOT_PAUSED",
                "conversation_state": "BOT_PAUSED",
                "qualification_step": "",
            },
        )

    if is_opt_out:
        return (
            "No problem — I’ve noted that you’re not interested, and we won’t continue the follow-up. Take care!",
            {
                "status": "NOT_INTERESTED",
                "conversation_state": "",
                "qualification_step": "",
            },
        )

    if msg_lower in _FAREWELL_SIGNALS:
        return "Thanks for chatting with us. Have a great day!", None

    if any(
        phrase in msg_lower
        for phrase in (
            "did not ask that",
            "didn't ask that",
            "that is not what i asked",
            "that's not what i asked",
            "you misunderstood",
        )
    ):
        return (
            "You're right — I misunderstood your question. Sorry about that. "
            "Please repeat the question and I'll answer only what you asked.",
            None,
        )

    if msg_lower.strip(" .!?") in {"what", "huh", "sorry what", "what do you mean"}:
        return (
            "Sorry for the confusion. Which part would you like me to clarify?",
            None,
        )

    if re.fullmatch(r"(?:are|r)\s+(?:you|u)\s+(?:working|online|there)[ .!?]*", msg_lower):
        return (
            "Yes, I'm working. I can help with Timmins, course content, fees, schedules, "
            "HRDC, and registration.",
            None,
        )

    if re.search(
        r"\bwho\s+(?:is\s+this|are\s+you)\b|"
        r"\bwhat\s+is\s+(?:this\s+)?timmins\b",
        msg_lower,
    ):
        return exact_answer("COMPANY", None, message=message), None

    course_specific_signals = (
        "fee",
        "cost",
        "price",
        "schedule",
        "course date",
        "when is the class",
        "trainer",
        "syllabus",
        "curriculum",
        "course outline",
        "tell me about the course",
    )
    plan_requires_course = plan is not None and any(
        request.course_slug is None and request.intent in _COURSE_SCOPED_INTENTS
        for request in plan.requests
    )
    if (
        course is None
        and (plan_requires_course or any(signal in msg_lower for signal in course_specific_signals))
        and not _is_course_catalog_question(message)
    ):
        # Some asks have a company-level answer that needs no course at all — HRDC
        # claimability, who Timmins is. Try that FIRST; asking "which course?" for a question
        # we can already answer is the dead-end a general enquirer kept hitting.
        if plan is not None and any(
            exact_answer(request.intent, None, message=message) for request in plan.requests
        ):
            return None, None

        # Otherwise ask — but with a numbered list, and remember what they were asking so the
        # answer can be delivered once they choose. Never while a qualification slot is open,
        # or "3" would be ambiguous between a course number and years of experience.
        if state not in _ACTIVE_STATES:
            pending_intent = (
                next(
                    (r.intent for r in plan.requests if r.intent in _COURSE_SCOPED_INTENTS),
                    "UNKNOWN",
                )
                if plan is not None
                else "UNKNOWN"
            )
            return (
                _course_picker(pending_intent),
                {
                    "conversation_state": f"{_COURSE_SELECT_STATE}:{pending_intent}",
                    "qualification_step": f"{_COURSE_SELECT_STATE}:{pending_intent}",
                },
            )
        return (
            "I can help with that, but I don't want to give you details for the wrong course. Which course are you interested in?",
            None,
        )

    # Waiting on a course choice from the picker. "software testing" or "2" is a selection,
    # not a new question — resolve it, then answer what they originally asked.
    pending_course_intent = _course_select_pending(state)
    if pending_course_intent is not None:
        chosen = _course_from_selection(message)
        if chosen is None:
            # The picker must not hold the conversation hostage. A customer who asks about
            # fees, sees the list, then pivots to "how do we pay?" is asking something new —
            # and PAYMENT/CANCELLATION are global anyway, so re-showing the course list both
            # fails to answer them and traps them until they name a course they never wanted
            # to name. Only re-ask when the message really was a failed pick.
            if _plan_is_new_question(plan, message):
                # If the new ask still needs a course, re-offer the picker for THAT intent
                # rather than the stale one; otherwise let it route and be answered globally.
                new_scoped = next(
                    (
                        r.intent
                        for r in (plan.requests if plan is not None else ())
                        if r.intent in _COURSE_SCOPED_INTENTS and r.course_slug is None
                    ),
                    None,
                )
                if new_scoped and new_scoped != pending_course_intent:
                    return (
                        _course_picker(new_scoped),
                        {
                            "conversation_state": f"{_COURSE_SELECT_STATE}:{new_scoped}",
                            "qualification_step": f"{_COURSE_SELECT_STATE}:{new_scoped}",
                        },
                    )
                if new_scoped:
                    return _course_picker(new_scoped), None
                return None, {"conversation_state": "", "qualification_step": ""}
            return (
                "Sorry, I didn't catch which course that was.\n\n"
                + _course_picker(pending_course_intent),
                None,
            )
        cleared = {"conversation_state": "", "qualification_step": "", "course": chosen.slug}
        # Answer here rather than deferring: the caller applies these updates only AFTER the
        # reply is generated, so handing the turn onward would answer with no course again.
        answer = exact_answer(pending_course_intent, chosen, message=message)
        if not answer and pending_course_intent == "COURSE_CONTENT":
            answer = _deterministic_reply(message, chosen, history=None, catalog=False)
        if not answer:
            answer = (
                f"Great — {chosen.name}. What would you like to know: the fee, the dates, "
                "or what it covers?"
            )
        return answer, cleared

    if state in _ACTIVE_STATES and msg_lower in {"no", "nope", "nah"}:
        return (
            "No problem. Would you like to continue with course questions, or speak with a consultant?",
            None,
        )

    if state not in _ACTIVE_STATES and msg_lower in {
        "no",
        "nope",
        "nah",
        "not at all",
        "not really",
    }:
        return (
            "Understood. I won't assume or continue that topic. If you want, tell me the exact "
            "course or question you'd like help with.",
            None,
        )

    # Answer factual questions without consuming them as qualification answers.
    # The current qualification state remains unchanged so the conversation can resume.
    # Answer vs consume-as-slot-answer: the plan (understanding) is authoritative here.
    # Only fall back to the keyword heuristic when the interpreter is unavailable.
    if state in _ACTIVE_STATES:
        # Funding volunteered out of order ("my company will pay for it" while we are asking
        # about tools) is an answer to a question we have not reached yet — not a request for
        # payment terms. Without this it fell through to the PAYMENT intent, which replied
        # "payment is by bank transfer", recorded nothing, and left the flow stuck on the
        # current slot forever.
        if state != "ASKING_FUNDING_PATH" and not (lead.get("funding_path") or "").strip():
            volunteered = _funding_from_statement(message)
            reprompt = _reprompt_for_state(state, lead, course) if volunteered else None
            if volunteered and reprompt:
                return (
                    f"Noted — {'your company/HRDC' if volunteered == 'Company/HRDC' else 'self-funded'}. "
                    f"{reprompt}",
                    {"funding_path": volunteered},
                )
        if plan is not None:
            if plan.control == "none" and not plan.answers_pending_slot:
                return None, None
        elif _looks_like_information_request(message):
            return None, None

    # Greet first-contact greetings, but let real questions fall through to be answered.
    # The plan (understanding) is authoritative for "is this actually just a greeting";
    # only fall back to the keyword heuristic when the interpreter is unavailable.
    first_contact_greeting = (
        _plan_is_greeting_only(plan)
        if plan is not None
        else (_is_greeting(message) or (not _looks_like_information_request(message)))
    )
    if (
        state not in _ACTIVE_STATES
        and lead_status in ("CONTACTED", "")
        and not is_interested
        and first_contact_greeting
    ):
        return _first_contact_greeting(lead, course), {"status": "ENGAGED"}

    # Pure greeting when not in active qualification
    if _is_greeting(message) and state not in _ACTIVE_STATES:
        # Once engaged, greetings and phatic acks must not restart the welcome.
        if lead_status not in ("CONTACTED", ""):
            return "Hi! I'm here. What would you like help with?", None
        return _first_contact_greeting(lead, course), None

    # Trigger: interest signals start qualification
    if is_interested and state not in _ACTIVE_STATES:
        return _experience_years_prompt(lead), _state_update("ASKING_EXPERIENCE_YEARS")

    if state not in _ACTIVE_STATES:
        return None, None

    if state == "ASKING_EXPERIENCE_YEARS":
        raw = message.strip()
        if not raw:
            return "Could you share how many years of experience you have?", None
        # "I am a QA engineer with 3 years experience" must store 3, not the whole sentence —
        # experience_years feeds the lead score. The sentence still has value as background,
        # so keep it in `experience` rather than throwing it away.
        parsed = _extract_int(raw)
        updates = _state_update(
            "ASKING_TECHNOLOGIES",
            experience_years=str(parsed) if parsed is not None else raw,
        )
        if parsed is not None and raw != str(parsed):
            updates["experience"] = raw
        return _technologies_prompt(lead, course), updates

    if state == "ASKING_TECHNOLOGIES":
        tech = message.strip()
        if not tech:
            return "Which tools or technologies do you currently use?", None
        return _motivation_prompt(), _state_update("ASKING_MOTIVATION", technologies=tech)

    if state == "ASKING_MOTIVATION":
        motivation = message.strip()
        if not motivation:
            return "What brought you to us?", None
        return _learning_goals_prompt(course), _state_update(
            "ASKING_LEARNING_GOALS", motivation=motivation
        )

    if state == "ASKING_LEARNING_GOALS":
        goals = message.strip()
        if not goals:
            return "What are you hoping to learn?", None
        # Skip funding question if Meta already told us
        who_pays = (lead.get("who_will_pay") or "").strip()
        if who_pays:
            funding_path = who_pays
            return _availability_prompt(), _state_update(
                "ASKING_AVAILABILITY",
                learning_goals=goals,
                funding_path=funding_path,
            )
        return _funding_prompt(), _state_update("ASKING_FUNDING_PATH", learning_goals=goals)

    if state == "ASKING_FUNDING_PATH":
        raw = message.strip()
        if not raw:
            return "Will your company sponsor this, or will you be self-paying?", None
        msg_up = raw.upper()
        if "1" in msg_up or any(w in msg_up for w in ("COMPANY", "HRDC", "EMPLOYER", "SPONSOR")):
            funding_path = "Company/HRDC"
        elif "2" in msg_up or any(w in msg_up for w in ("SELF", "OWN", "PERSONAL", "MYSELF")):
            funding_path = "Self-pay"
        else:
            funding_path = raw
        return _availability_prompt(), _state_update(
            "ASKING_AVAILABILITY", funding_path=funding_path
        )

    if state == "ASKING_AVAILABILITY":
        availability = message.strip()
        if not availability:
            return "When are you available to start?", None
        lead_after = {**lead, "availability": availability}
        score = _lead_score(lead_after, course=course)
        status = _lead_status_from_score(score)
        return _final_qualification_reply(), {
            "status": status,
            "conversation_state": "",
            "qualification_step": "",
            "availability": availability,
            "lead_score": score,
        }

    return None, None


def _queue_human_handoff(
    sender: str,
    lead: dict[str, str],
    *,
    reason: str,
    source: str,
    intent: str = "",
    message: str = "",
    worksheet_name: str | None = None,
    workbook_name: str | None = None,
) -> None:
    queue_updates = {
        "status": "NEEDS_HUMAN",
        "conversation_state": "NEEDS_HUMAN",
        "assigned_to": "Raj",
        "needs_human": "YES",
        "human_reason": reason,
        "human_status": "OPEN",
        "human_updated_at": _utc_now(),
    }
    if intent:
        queue_updates["last_intent"] = intent
    queue_updates["last_intent_reason"] = reason

    local_updated = upsert_lead(sender, **queue_updates)
    logger.info(
        "event=human_queue_local source=%s subject=%s result=%s",
        source,
        subject_id(sender),
        local_updated,
    )
    updated = _update_sheet_if_enabled(
        sender, worksheet_name=worksheet_name, workbook_name=workbook_name, **queue_updates
    )
    logger.info(
        "event=human_queue_sheet source=%s subject=%s result=%s",
        source,
        subject_id(sender),
        updated,
    )
    _notify_handoff_owner(
        sender,
        {**lead, **queue_updates},
        reason=reason,
        source=source,
        intent=intent,
        message=message,
        worksheet_name=worksheet_name,
        workbook_name=workbook_name,
    )


def _human_handoff_open(lead: dict[str, str]) -> bool:
    return (
        (lead.get("needs_human") or "").upper() == "YES"
        and (lead.get("human_status") or "").upper() == "OPEN"
        and (lead.get("human_reason") or "").upper().startswith("REQUESTED")
    )


def _automation_paused(lead: dict[str, str]) -> bool:
    return (lead.get("conversation_state") or "").upper() == "BOT_PAUSED" or (
        lead.get("status") or ""
    ).upper() == "BOT_PAUSED"


def _hot_lead_summary(sender: str, lead: dict[str, str], updates: dict[str, str | int]) -> str:
    def _v(key: str) -> str:
        return str(updates.get(key) or lead.get(key) or "").strip()

    return (
        "HOT LEAD\n\n"
        f"Name: {_v('name') or 'Unknown'}\n"
        f"Phone: {sender}\n"
        f"Job Title: {_v('job_title')}\n"
        f"Company: {_v('company_name')}\n"
        f"Experience: {_v('experience_years')} years\n"
        f"Technologies: {_v('technologies')}\n"
        f"Motivation: {_v('motivation')}\n"
        f"Learning Goals: {_v('learning_goals')}\n"
        f"Funding: {_v('funding_path') or _v('who_will_pay')}\n"
        f"Availability: {_v('availability')}\n\n"
        "Recommendation: Call within 24 hours.\n"
        f"Score: {_v('lead_score')}"
    )


def _human_escalation_reason(msg_lower: str) -> str | None:
    msg_lower = msg_lower.lower().replace("’", "'")
    overdue_followup = (
        "no one has talked to me",
        "no one contacted me",
        "nobody contacted me",
        "no one called me",
        "nobody called me",
        "still waiting for a call",
        "still waiting for someone",
    )
    if any(phrase in msg_lower for phrase in overdue_followup):
        return "Follow-up overdue"
    human_request = re.search(
        r"\b(?:talk|speak|chat)\s+(?:to|with)\s+"
        r"(?:(?:a|an)\s+)?(?:real\s+)?"
        r"(?:person|someone|somebody|human|agent|consultant)\b",
        msg_lower,
    )
    if human_request or re.search(
        r"\b(?:connect|transfer)\s+me\s+to\s+(?:a\s+)?"
        r"(?:person|someone|somebody|human|agent|consultant)\b",
        msg_lower,
    ):
        return "Requested human agent"

    if re.search(r"\b(?:can|could|may)\s+i\s+(?:call|phone|ring)\b", msg_lower):
        return "Requested callback"

    triggers = {
        # Human/agent requests — check these first (longer phrases before substrings)
        "talk to a person": "Requested human agent",
        "talk to someone": "Requested human agent",
        "talk to somebody": "Requested human agent",
        "speak to a person": "Requested human agent",
        "speak to someone": "Requested human agent",
        "speak to somebody": "Requested human agent",
        "speak with someone": "Requested human agent",
        "speak with a person": "Requested human agent",
        "want to speak": "Requested human agent",
        "want to talk": "Requested human agent",
        "real person": "Requested human agent",
        "human agent": "Requested human agent",
        "talk to consultant": "Requested consultant",
        "speak to consultant": "Requested consultant",
        "speak to a consultant": "Requested consultant",
        "speak with consultant": "Requested consultant",
        "speak with a consultant": "Requested consultant",
        "talk to an agent": "Requested human agent",
        "speak to an agent": "Requested human agent",
        "can somebody help me": "Requested human agent",
        "can someone help me": "Requested human agent",
        # Callback requests
        "call me": "Requested callback",
        "give me a call": "Requested callback",
        "please call": "Requested callback",
        "can i call": "Requested callback",
        "could i call": "Requested callback",
        "may i call": "Requested callback",
        # Requests that genuinely need person-to-person coordination. Documented
        # discounts and group rates are answered by the bot and do not belong here.
        "trainer discussion": "Requested trainer discussion",
        "in-house training": "Requested in-house training",
    }
    for keyword, reason in triggers.items():
        if keyword in msg_lower:
            return reason
    return None


@app.get("/")
def home():
    return {
        "status": "running",
        "service": "Timmins WhatsApp Webhook",
    }


@app.get("/health/ready")
def readiness():
    try:
        summary = get_dashboard_summary()
        events = queue_stats()
    except Exception as error:
        logger.exception("event=readiness_failed")
        raise HTTPException(status_code=503, detail="Storage unavailable") from error
    return {
        "status": "ready",
        "database": persistence_backend_name(),
        "leads": summary["total_leads"],
        "queue": events,
        "safe_mode": os.getenv("BOT_SAFE_MODE", "true").lower() in {"1", "true", "yes", "on"},
        "lead_pipeline": {
            "sync_interval_minutes": _lead_sync_interval_seconds() // 60,
            "auto_outreach": auto_outreach_enabled(),
        },
    }


@app.get("/rag-v2/health")
def rag_v2_health():
    return rag_health()


def require_auth(request: Request, *, api: bool = False) -> None:
    """Guard for admin pages and the PII endpoints. Raises redirect (page) or 401 (api/JSON)."""
    from services.admin_auth import is_authenticated

    if is_authenticated(request):
        return
    if api:
        raise HTTPException(status_code=401, detail="Authentication required")
    raise HTTPException(status_code=303, headers={"Location": "/admin/login"})


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page():
    return HTMLResponse(_ADMIN_LOGIN_HTML.replace("__ERROR__", ""))


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    from services.admin_auth import (
        COOKIE_NAME,
        SESSION_TTL_SECONDS,
        check_password,
        create_session_token,
    )

    if not check_password(password):
        return HTMLResponse(
            _ADMIN_LOGIN_HTML.replace("__ERROR__", "Incorrect password."), status_code=401
        )
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@app.post("/admin/logout")
def admin_logout():
    from services.admin_auth import COOKIE_NAME

    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Admin dashboard (P1 — read-only). Inline-HTML pages that fetch guarded JSON
# client-side, mirroring the _WA_SIMULATOR_HTML pattern. Every page and every
# /admin/api/* endpoint calls require_auth first.
# ---------------------------------------------------------------------------


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    require_auth(request)
    return HTMLResponse(_admin_page("home", "Overview", _ADMIN_HOME_BODY, _ADMIN_HOME_SCRIPT))


@app.get("/admin/leads", response_class=HTMLResponse)
def admin_leads_page(request: Request):
    require_auth(request)
    return HTMLResponse(_admin_page("leads", "Leads", _ADMIN_LEADS_BODY, _ADMIN_LEADS_SCRIPT))


@app.get("/admin/courses", response_class=HTMLResponse)
def admin_courses_page(request: Request):
    require_auth(request)
    return HTMLResponse(
        _admin_page("courses", "Courses", _ADMIN_COURSES_BODY, _ADMIN_COURSES_SCRIPT)
    )


@app.get("/admin/knowledge", response_class=HTMLResponse)
def admin_knowledge_page(request: Request):
    require_auth(request)
    return HTMLResponse(
        _admin_page("knowledge", "Knowledge", _ADMIN_KNOWLEDGE_BODY, _ADMIN_KNOWLEDGE_SCRIPT)
    )


# --- read-only JSON for the pages above ---


@app.get("/admin/api/summary")
def admin_api_summary(request: Request):
    require_auth(request, api=True)
    from services.course_loader import load_courses
    from services.knowledge_base import load_knowledge_base

    summary = get_dashboard_summary()
    courses = load_courses()
    summary["total_courses"] = len(courses)
    summary["active_courses"] = sum(1 for c in courses.values() if c.active)
    summary["knowledge_topics"] = len(load_knowledge_base())
    return summary


@app.get("/admin/api/meta")
def admin_api_meta(request: Request):
    require_auth(request, api=True)
    from services.course_loader import load_courses

    statuses = sorted(
        {
            str(r.get("status") or "").strip()
            for r in list_leads()
            if str(r.get("status") or "").strip()
        }
    )
    courses = [{"slug": s, "name": c.name} for s, c in load_courses().items()]
    return {"statuses": statuses, "courses": courses}


@app.get("/admin/api/leads")
def admin_api_leads(request: Request, status: str = "", course: str = ""):
    require_auth(request, api=True)
    statuses = (status.strip(),) if status.strip() else None
    leads = list_leads(course=course.strip() or None, statuses=statuses)
    return {"leads": leads}


@app.get("/admin/api/leads/{phone}")
def admin_api_lead_detail(phone: str, request: Request):
    require_auth(request, api=True)
    return {"lead": get_lead(phone), "history": get_conversation_history(phone)}


@app.get("/admin/api/courses")
def admin_api_courses(request: Request):
    require_auth(request, api=True)
    from services.content_store import list_documents
    from services.course_loader import load_courses

    editable = os.getenv("CONTENT_SOURCE", "files").strip().lower() == "db"
    doc_counts: dict[str, int] = {}
    if editable:
        for row in list_documents():
            slug = str(row.get("course_slug") or "")
            doc_counts[slug] = doc_counts.get(slug, 0) + 1

    return {
        "editable": editable,
        "courses": [
            {
                "slug": c.slug,
                "name": c.name,
                "active": c.active,
                "dates": c.dates,
                "venue": c.venue,
                "fees": c.fees,
                "hrdc_deadline": c.hrdc_deadline,
                "payment_deadline": c.payment_deadline,
                "keywords": c.keywords,
                "overview": c.overview,
                "document_count": doc_counts.get(c.slug, 0),
            }
            for c in load_courses().values()
        ],
    }


# --- course editing (P2) ---
#
# Content edits only reach the bot when it reads content from the database, so a mutation
# attempted under CONTENT_SOURCE=files is refused outright rather than being written somewhere
# nothing reads. Silently accepting it would show the admin a course the bot cannot see.

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _require_db_content_source() -> None:
    if os.getenv("CONTENT_SOURCE", "files").strip().lower() != "db":
        raise HTTPException(
            status_code=409,
            detail=(
                "The bot is currently reading its content from files, so dashboard edits would "
                "have no effect. Set CONTENT_SOURCE=db to enable editing."
            ),
        )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:80]


def _publish_content_change() -> None:
    """Make a content write visible to the readers immediately."""
    from services.content_store import bump_content_version

    bump_content_version()
    refresh_courses()


def _publish_knowledge_change() -> None:
    """Same, for company knowledge and policies.

    The readers cache on the content version, so the bump alone is what makes an edit visible;
    clearing the knowledge cache here just avoids waiting for the next version check.
    """
    from services.content_store import bump_content_version
    from services.knowledge_base import refresh_knowledge_base

    bump_content_version()
    refresh_knowledge_base()


@app.post("/admin/api/courses")
async def admin_api_course_save(request: Request):
    """Create or update a course. The slug is derived from the name on create and is then fixed."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import get_course_row, upsert_course

    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    slug = _slugify(payload.get("slug") or name)
    if not slug:
        raise HTTPException(status_code=400, detail="could not derive a slug from that name")

    existing = get_course_row(slug) or {}
    keywords = payload.get("keywords")
    if isinstance(keywords, str):
        keywords = [k.strip().lower() for k in keywords.split(",") if k.strip()]
    fees = payload.get("fees")
    if isinstance(fees, str):
        try:
            fees = json.loads(fees) if fees.strip() else {}
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail=f"fees must be valid JSON: {error}"
            ) from error

    upsert_course(
        slug,
        {
            "name": name,
            "active": bool(payload.get("active", existing.get("active", True))),
            "dates": payload.get("dates", existing.get("dates")),
            "venue": payload.get("venue", existing.get("venue")),
            "fees": fees if fees is not None else existing.get("fees_json"),
            "hrdc_deadline": payload.get("hrdc_deadline", existing.get("hrdc_deadline")),
            "payment_deadline": payload.get("payment_deadline", existing.get("payment_deadline")),
            "hot_budget_threshold": payload.get(
                "hot_budget_threshold", existing.get("hot_budget_threshold") or 4000
            ),
            "keywords": keywords if keywords is not None else existing.get("keywords_json"),
            "overview": payload.get("overview", existing.get("overview") or ""),
            "outreach": payload.get("outreach", existing.get("outreach_json")),
        },
    )
    _publish_content_change()
    logger.info("event=admin_course_saved slug=%s created=%s", slug, not existing)
    return {"slug": slug, "created": not existing}


@app.post("/admin/api/courses/extract")
async def admin_api_course_extract(request: Request, file: UploadFile = File(...)):
    """Read a brochure and return proposed course fields for the admin to review.

    Deliberately saves nothing. The values land in the editor so a human confirms them before
    the bot starts quoting them — an extracted fee is a claim about money, not a formatting
    detail. If no model is configured, this fails with a message pointing at manual entry.
    """
    require_auth(request, api=True)
    _require_db_content_source()
    from services.course_extract import ExtractionUnavailable, propose_course_fields
    from services.doc_extract import ExtractionError, extract_text

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"file is larger than {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB"
        )

    try:
        text, method = extract_text(raw, file.filename or "upload", file.content_type or "")
    except ExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        fields = propose_course_fields(text)
    except ExtractionUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=f"{error}. You can still fill the form in by hand.",
        ) from error

    logger.info(
        "event=admin_course_extracted filename=%s method=%s chars=%d name=%r",
        file.filename,
        method,
        len(text),
        fields.get("name", ""),
    )
    return {"fields": fields, "extraction_method": method, "characters": len(text)}


@app.post("/admin/api/courses/{slug}/active")
async def admin_api_course_set_active(slug: str, request: Request):
    """Archive (active=false) or reactivate a course.

    Archiving also prunes the course's chunks so it stops being retrievable straight away;
    its rows and extracted text survive, so reactivating re-indexes without a re-upload.
    """
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import get_course_row, set_course_active
    from services.course_ingest import prune_course, resync_course

    if not get_course_row(slug):
        raise HTTPException(status_code=404, detail="course not found")
    payload = await request.json()
    active = bool(payload.get("active"))
    set_course_active(slug, active)
    _publish_content_change()
    try:
        report = resync_course(slug) if active else {"pruned": prune_course(slug)}
    except Exception:
        logger.exception("event=admin_course_active_resync_failed slug=%s", slug)
        report = {"error": "the course was saved but its search index could not be updated"}
    logger.info("event=admin_course_active slug=%s active=%s", slug, active)
    return {"slug": slug, "active": active, **report}


@app.delete("/admin/api/courses/{slug}")
def admin_api_course_delete(slug: str, request: Request):
    """Permanently delete a course: its chunks, then its documents and row."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import delete_course, get_course_row
    from services.course_ingest import prune_course

    if not get_course_row(slug):
        raise HTTPException(status_code=404, detail="course not found")
    try:
        pruned = prune_course(slug)
    except Exception:
        logger.exception("event=admin_course_delete_prune_failed slug=%s", slug)
        pruned = 0
    delete_course(slug)
    _publish_content_change()
    logger.info("event=admin_course_deleted slug=%s pruned=%d", slug, pruned)
    return {"slug": slug, "deleted": True, "chunks_pruned": pruned}


@app.get("/admin/api/courses/{slug}/documents")
def admin_api_course_documents(slug: str, request: Request):
    """Document list with live ingest status — this is what the upload UI polls."""
    require_auth(request, api=True)
    from services.content_store import list_documents

    return {"slug": slug, "documents": list_documents(slug)}


@app.post("/admin/api/courses/{slug}/documents")
async def admin_api_course_upload(slug: str, request: Request, file: UploadFile = File(...)):
    """Accept a file and queue it. Extraction and embedding happen on the ingest worker."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import add_document, get_course_row

    if not get_course_row(slug):
        raise HTTPException(status_code=404, detail="course not found")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file is larger than {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB",
        )
    document_id = add_document(slug, file.filename or "upload", file.content_type or "", raw)
    _ingest_wakeup.set()
    logger.info(
        "event=admin_document_uploaded slug=%s id=%d filename=%s bytes=%d",
        slug,
        document_id,
        file.filename,
        len(raw),
    )
    return {"id": document_id, "filename": file.filename, "status": "pending"}


@app.delete("/admin/api/documents/{document_id}")
def admin_api_document_delete(document_id: int, request: Request):
    """Delete a document and re-sync its course so its chunks go with it."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import delete_document
    from services.course_ingest import resync_course

    slug = delete_document(document_id)
    if slug is None:
        raise HTTPException(status_code=404, detail="document not found")
    try:
        report = resync_course(slug)
    except Exception:
        logger.exception("event=admin_document_delete_resync_failed slug=%s", slug)
        report = {"error": "the document was deleted but the search index could not be updated"}
    logger.info("event=admin_document_deleted id=%d slug=%s", document_id, slug)
    return {"deleted": True, "slug": slug, **report}


@app.post("/admin/api/courses/{slug}/reindex")
def admin_api_course_reindex(slug: str, request: Request):
    """Re-chunk and re-embed a course from its stored text, without re-uploading anything."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.course_ingest import resync_course

    try:
        return {"slug": slug, **resync_course(slug)}
    except Exception as error:
        logger.exception("event=admin_course_reindex_failed slug=%s", slug)
        raise HTTPException(status_code=502, detail=f"reindex failed: {error}") from error


@app.get("/admin/api/knowledge")
def admin_api_knowledge(request: Request):
    require_auth(request, api=True)
    from services.knowledge_base import load_knowledge_base

    topics = [{"topic": t, "body": b} for t, b in sorted(load_knowledge_base().items())]
    return {
        "editable": os.getenv("CONTENT_SOURCE", "files").strip().lower() == "db",
        "topics": topics,
        "policies": load_policies(),
    }


# --- company knowledge editing (P3) ---
#
# Company knowledge is the permanent half of the content: policies, payment terms, what the
# company is. Unlike courses it is not seasonal, so there is no archive — just edit and delete.


@app.post("/admin/api/knowledge")
async def admin_api_knowledge_save(request: Request):
    """Create or update one knowledge topic (the old knowledge/<topic>.md)."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import upsert_knowledge

    payload = await request.json()
    topic = re.sub(r"[^a-z0-9_]+", "_", str(payload.get("topic") or "").strip().lower()).strip("_")
    if not topic:
        raise HTTPException(status_code=400, detail="topic is required")
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body cannot be empty")

    upsert_knowledge(topic, body)
    _publish_knowledge_change()
    logger.info("event=admin_knowledge_saved topic=%s chars=%d", topic, len(body))
    return {"topic": topic, "saved": True}


@app.delete("/admin/api/knowledge/{topic}")
def admin_api_knowledge_delete(topic: str, request: Request):
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import delete_knowledge, get_all_knowledge

    if topic not in get_all_knowledge():
        raise HTTPException(status_code=404, detail="topic not found")
    delete_knowledge(topic)
    _publish_knowledge_change()
    logger.info("event=admin_knowledge_deleted topic=%s", topic)
    return {"topic": topic, "deleted": True}


@app.post("/admin/api/policies")
async def admin_api_policies_save(request: Request):
    """Replace the policies blob (payment terms, cancellation tiers, certification, company)."""
    require_auth(request, api=True)
    _require_db_content_source()
    from services.content_store import set_policies

    payload = await request.json()
    data = payload.get("policies")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail=f"policies must be valid JSON: {error}"
            ) from error
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="policies must be a JSON object")

    set_policies(data)
    _publish_knowledge_change()
    logger.info("event=admin_policies_saved sections=%d", len(data))
    return {"saved": True, "sections": sorted(data)}


@app.get("/stats")
def stats(request: Request):
    require_auth(request, api=True)
    return get_dashboard_summary()


@app.post("/admin/sync-leads")
def sync_leads_now(request: Request):
    """Run the lead pipeline now — pull new leads in, then send first contact.

    The scheduler does this on its own; this endpoint is for a cron or a human who
    doesn't want to wait for the next tick. Guarded by LEAD_SYNC_TOKEN, since it both
    writes to the leads table and sends messages.
    """
    expected = os.getenv("LEAD_SYNC_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="LEAD_SYNC_TOKEN is not configured")
    supplied = request.headers.get("x-sync-token", "").strip()
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid sync token")
    try:
        return _run_lead_pipeline()
    except Exception as error:
        logger.exception("event=lead_pipeline_failed trigger=manual")
        raise HTTPException(status_code=502, detail="Lead pipeline failed") from error


@app.get("/conversation/{phone}")
def conversation(phone: str, request: Request):
    require_auth(request, api=True)
    return {
        "phone": phone,
        "history": get_conversation_history(phone),
    }


@app.get("/lead/{phone}")
def lead_detail(phone: str, request: Request):
    require_auth(request, api=True)
    return {
        "lead": get_lead(phone),
        "history": get_conversation_history(phone),
    }


@app.get("/rag-test", response_class=HTMLResponse)
def rag_test_chat():
    return HTMLResponse(_RAG_TEST_CHAT_HTML)


@app.get("/rag-test/history/{session_id}")
def rag_test_history(session_id: str):
    return {"session_id": session_id, "history": _rag_test_history(session_id)}


@app.post("/rag-test/reset")
async def rag_test_reset(request: Request):
    payload = await request.json()
    session_id = str(payload.get("session_id") or "").strip()
    _rag_test_reset(session_id)
    return {"session_id": session_id, "history": []}


@app.post("/rag-test/message")
async def rag_test_message(request: Request):
    payload = await request.json()
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    session_id = str(payload.get("session_id") or "").strip() or uuid.uuid4().hex
    history = _rag_test_history_for_runtime(session_id)
    reply = rag_answer(
        message,
        course=None,
        lead={"source": "rag_test_chat", "session_id": session_id},
        history=history,
        trace_label=f"rag-test-{session_id[:8]}",
    )
    _rag_test_add_message(session_id, "user", message)
    _rag_test_add_message(session_id, "assistant", reply)
    return {
        "session_id": session_id,
        "reply": reply,
        "history": _rag_test_history(session_id),
    }


@app.get("/simulate", response_class=HTMLResponse)
def wa_simulator():
    return HTMLResponse(_WA_SIMULATOR_HTML)


@app.get("/simulate/session/{session_id}")
def sim_session(session_id: str):
    sender = f"sim-{session_id}"
    lead = get_lead(sender) or {}
    history = get_conversation_history(sender, limit=100)
    return {
        "session_id": session_id,
        "lead": lead,
        "course": lead.get("course"),
        "history": [{"direction": h["direction"], "body": h["body"]} for h in history],
    }


@app.post("/simulate/message")
async def sim_message(request: Request):
    payload = await request.json()
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    session_id = str(payload.get("session_id") or "").strip() or uuid.uuid4().hex
    sender = f"sim-{session_id}"
    course_slug = str(payload.get("course") or "").strip() or None
    name = str(payload.get("name") or "Test Lead").strip()

    lead = get_lead(sender)
    if not lead:
        upsert_lead(sender, name=name, status="CONTACTED")
        if course_slug:
            upsert_lead(sender, course=course_slug)
        lead = get_lead(sender) or {}
    elif course_slug and lead.get("course") != course_slug:
        upsert_lead(sender, course=course_slug)
        lead = {**lead, "course": course_slug}

    add_message(sender, direction="inbound", body=message)

    # Re-resolve the course from THIS message so an explicit switch is honoured
    # (mirrors the webhook path); fall back to the session/lead course.
    course = _resolve_course(lead, message) or get_course(course_slug or lead.get("course") or "")

    # One understanding pass for the whole turn: the authoritative plan drives lifecycle
    # control (human/stop, even mid-qualification) and content routing — no second call.
    history_ctx = get_conversation_history(sender, limit=16)
    plan = understand(
        message, course=course, lead=lead, history=history_ctx, pending_slot=_pending_slot(lead)
    )

    reply_text, state_updates = _process_conversation(message, lead, course=course, plan=plan)
    if reply_text is None:
        catalog = _is_course_catalog_question(message) or (
            _is_ambiguous_catalog_followup(message)
            and _history_mentions_multiple_courses(history_ctx)
        )
        reply_text = _faq_reply(
            message,
            course=course,
            lead=lead,
            history=history_ctx,
            catalog=catalog,
            trace_label=f"sim-{session_id[:8]}",
            plan=plan,
        )

    if state_updates:
        upsert_lead(sender, **state_updates)
        if (
            str(state_updates.get("needs_human") or "").upper() == "YES"
            and str(state_updates.get("human_status") or "").upper() == "OPEN"
        ):
            _notify_handoff_owner(
                sender,
                {**lead, **state_updates},
                reason=str(state_updates.get("human_reason") or "Requested human agent"),
                source="simulator",
                intent=str(state_updates.get("last_intent") or ""),
                message=message,
            )

    add_message(sender, direction="outbound", body=reply_text)

    updated_lead = get_lead(sender) or {}
    return {
        "session_id": session_id,
        "reply": reply_text,
        "lead": updated_lead,
    }


@app.post("/simulate/reset")
async def sim_reset(request: Request):
    payload = await request.json()
    session_id = str(payload.get("session_id") or "").strip()
    if session_id:
        sender = f"sim-{session_id}"
        import services.sqlite_store as _ss

        with _ss.get_connection() as _c:
            _c.execute("DELETE FROM messages WHERE phone = ?", (sender,))
            _c.execute("DELETE FROM leads WHERE phone = ?", (sender,))
            _c.commit()
    return {"session_id": session_id, "status": "reset"}


@app.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


def _handle_webhook_value(value: dict) -> None:
    """Serialize inbound messages per phone so state and history cannot race."""
    messages = value.get("messages") or []
    sender = str(messages[0].get("from") or "") if messages else ""
    if sender:
        with _lock_for_sender(sender):
            _handle_webhook_value_unlocked(value)
        return
    _handle_webhook_value_unlocked(value)


def _handle_webhook_value_unlocked(value: dict) -> None:
    try:
        if "messages" in value:
            msg = value["messages"][0].get("text", {}).get("body", "")
            sender = value["messages"][0].get("from", "")

            # Ignore non-text messages (reactions, images, stickers, etc.)
            if value["messages"][0].get("type") not in ("text", None):
                return

            if msg and sender and not _is_auto_reply(msg):
                msg_id = value["messages"][0].get("id") or ""
                if msg_id and msg_id in seen_message_ids:
                    logger.info("event=webhook_duplicate message_id=%s", msg_id)
                    return
                if msg_id:
                    _remember_seen(msg_id, seen_message_ids, _seen_message_order)
                    mark_read(msg_id)  # show blue ticks immediately

                # Referral is present when message originates from a Meta ad click
                referral = value["messages"][0].get("referral") or {}

                logger.info(
                    "event=inbound_message subject=%s message_id=%s",
                    subject_id(sender),
                    msg_id or "unknown",
                )
                time.sleep(_reply_delay())  # pause before replying — feels human
                add_message(
                    sender,
                    direction="inbound",
                    body=msg,
                    message_id=msg_id or None,
                )
                lead = get_lead(sender) or {}

                if _automation_paused(lead):
                    if msg.lower().strip() in _RESUME_CONVERSATION_SIGNALS:
                        upsert_lead(
                            sender,
                            status="ENGAGED",
                            conversation_state="",
                            qualification_step="",
                        )
                        lead = {
                            **lead,
                            "status": "ENGAGED",
                            "conversation_state": "",
                            "qualification_step": "",
                        }
                    else:
                        logger.info(
                            "event=auto_reply_suppressed reason=bot_paused subject=%s",
                            subject_id(sender),
                        )
                        return

                # A customer who asked for a person must not be pulled back into
                # qualification or receive another automated welcome while the
                # handoff is open. Their inbound message is still retained above.
                if _human_handoff_open(lead):
                    logger.info(
                        "event=auto_reply_suppressed reason=human_handoff_open subject=%s",
                        subject_id(sender),
                    )
                    return

                # Parse Meta Lead Form auto-message and save lead data immediately
                is_form_fill = bool(_FORM_FILL_RE.search(msg))
                if is_form_fill:
                    form_data = _parse_form_fill(msg)
                    if form_data:
                        upsert_lead(sender, **{k: v for k, v in form_data.items() if v})
                        lead = {**lead, **form_data}
                        logger.info(
                            "event=form_fill_parsed subject=%s fields=%s",
                            subject_id(sender),
                            ",".join(form_data),
                        )

                course = _resolve_course(lead, msg)

                # Referral from Meta ad — for form-fill messages this is the ground truth.
                # It overrides any stale SQLite course from a previous outreach campaign.
                if referral and (is_form_fill or course is None):
                    referral_course = _detect_course_from_referral(referral)
                    if referral_course and (is_form_fill or course is None):
                        course = referral_course
                        logger.info(
                            "event=course_from_referral course=%s overridden=%s",
                            course.slug,
                            is_form_fill,
                        )
                        upsert_lead(sender, course=course.slug)
                        lead = {**lead, "course": course.slug}

                sheet_workbook: str | None = None  # tracks which workbook to write back to

                # If course still unknown, search Google Sheets across all workbooks.
                # Handles leads contacted locally whose course wasn't saved to this server's SQLite.
                if course is None and ENABLE_GOOGLE_SHEETS and not lead.get("course"):
                    from services.course_loader import load_courses as _lc
                    from services.google_sheets import _normalize_phone as _np

                    lookup = find_phone_in_workbook(sender)
                    if lookup:
                        found_workbook, found_tab = lookup
                        sheet_workbook = found_workbook
                        all_courses = _lc()
                        # Try matching tab name to a course's worksheet_name
                        for slug, c in all_courses.items():
                            if c.worksheet_name == found_tab:
                                course = c
                                break
                        # Tab name doesn't match a course — detect from ad_name in that row
                        if course is None:
                            try:
                                rows = get_rows_from(found_tab, found_workbook)
                                for row in rows:
                                    rp = _np(
                                        str(row.get("whatsapp_number") or row.get("phone", ""))
                                    )
                                    if rp == _np(sender):
                                        ad_name = str(
                                            row.get("ad_name", "")
                                            or row.get("adset_name", "")
                                            or row.get("campaign_name", "")
                                            or ""
                                        ).lower()
                                        for slug, c in all_courses.items():
                                            for kw in c.keywords or []:
                                                if kw.lower() in ad_name:
                                                    course = c
                                                    break
                                            if course:
                                                break
                                        break
                            except Exception:
                                logger.exception("event=extra_workbook_lookup_failed")

                if course and lead.get("course") != course.slug:
                    upsert_lead(sender, course=course.slug)
                    lead = {**lead, "course": course.slug}

                worksheet = getattr(course, "worksheet_name", None)
                logger.info(
                    "event=course_resolved course=%s worksheet=%s workbook=%s subject=%s",
                    getattr(course, "slug", "none"),
                    worksheet or "none",
                    sheet_workbook or "default",
                    subject_id(sender),
                )
                human_reason = _human_escalation_reason(msg.lower())
                if human_reason:
                    _queue_human_handoff(
                        sender,
                        lead,
                        reason=human_reason,
                        source="keyword",
                        message=msg,
                        worksheet_name=worksheet,
                        workbook_name=sheet_workbook,
                    )

                    customer_reply = _human_handoff_reply(lead)
                    response = send_text(sender, customer_reply)
                    logger.info(
                        "event=human_handoff_reply status_code=%s ok=%s subject=%s",
                        response.status_code,
                        response.ok,
                        subject_id(sender),
                    )
                    if response.ok:
                        try:
                            response_message_id = response.json().get("messages", [{}])[0].get("id")
                        except (ValueError, KeyError, IndexError):
                            response_message_id = None
                        add_message(
                            sender,
                            direction="outbound",
                            body=customer_reply,
                            message_id=response_message_id,
                        )
                    else:
                        logger.error(
                            "event=human_handoff_reply_failed subject=%s", subject_id(sender)
                        )
                    return

                # Form-fill: skip generic flow, reply with a personalised message using their submitted details
                if is_form_fill and _get_state(lead) not in _ACTIVE_STATES:
                    reply_text = _form_fill_greeting(lead, course)
                    state_updates = {"status": "ENGAGED"}
                else:
                    # One understanding pass for the whole turn (lifecycle control + routing).
                    history = get_conversation_history(sender, limit=16)
                    plan = understand(
                        msg,
                        course=course,
                        lead=lead,
                        history=history,
                        pending_slot=_pending_slot(lead),
                    )
                    reply_text, state_updates = _process_conversation(
                        msg, lead, course=course, plan=plan
                    )
                    if reply_text is None:
                        catalog = _is_course_catalog_question(msg) or (
                            _is_ambiguous_catalog_followup(msg)
                            and _history_mentions_multiple_courses(history)
                        )
                        reply_text = _faq_reply(
                            msg,
                            course=course,
                            lead=lead,
                            history=history,
                            catalog=catalog,
                            trace_label=subject_id(sender),
                            plan=plan,
                        )
                        state_updates = None
                detected_topic = topic_for_message(msg)
                topic_label = str(detected_topic or lead.get("last_intent") or "GENERAL").upper()
                topic_reason = "explicit_message" if detected_topic else "conversation_memory"

                response = send_text(sender, reply_text)
                logger.info(
                    "event=auto_reply status_code=%s ok=%s subject=%s",
                    response.status_code,
                    response.ok,
                    subject_id(sender),
                )
                if not response.ok:
                    logger.error("event=auto_reply_failed subject=%s", subject_id(sender))
                    return

                # Advance the conversation only after WhatsApp accepts the reply.
                if state_updates:
                    local_updated = upsert_lead(sender, **state_updates)
                    logger.info(
                        "event=local_state_updated subject=%s result=%s",
                        subject_id(sender),
                        local_updated,
                    )
                    updated = _update_sheet_if_enabled(
                        sender,
                        worksheet_name=worksheet,
                        workbook_name=sheet_workbook,
                        **state_updates,
                    )
                    logger.info(
                        "event=sheet_state_updated subject=%s result=%s",
                        subject_id(sender),
                        updated,
                    )
                    if (
                        str(state_updates.get("needs_human") or "").upper() == "YES"
                        and str(state_updates.get("human_status") or "").upper() == "OPEN"
                    ):
                        _notify_handoff_owner(
                            sender,
                            {**lead, **state_updates},
                            reason=str(
                                state_updates.get("human_reason") or "Requested human agent"
                            ),
                            source="state_update",
                            intent=str(state_updates.get("last_intent") or ""),
                            message=msg,
                            worksheet_name=worksheet,
                            workbook_name=sheet_workbook,
                        )

                    if str(state_updates.get("status") or "") == "HOT":
                        _queue_human_handoff(
                            sender,
                            lead,
                            reason=f"HOT lead score {state_updates.get('lead_score')}",
                            source="hot_lead",
                            intent="HOT_LEAD",
                            worksheet_name=worksheet,
                            workbook_name=sheet_workbook,
                        )
                        if ENABLE_GOOGLE_SHEETS:
                            appended = append_hot_lead(sender, lead, state_updates)
                            logger.info("event=hot_lead_sheet_append result=%s", appended)

                local_updated = upsert_lead(
                    sender,
                    last_intent=topic_label,
                    last_intent_reason=topic_reason,
                    last_message=msg,
                    last_reply=reply_text,
                )
                logger.info(
                    "event=local_reply_saved subject=%s result=%s",
                    subject_id(sender),
                    local_updated,
                )
                updated = _update_sheet_if_enabled(
                    sender,
                    worksheet_name=worksheet,
                    workbook_name=sheet_workbook,
                    last_intent=topic_label,
                    last_intent_reason=topic_reason,
                    last_message=msg,
                    last_reply=reply_text,
                )
                logger.info(
                    "event=sheet_reply_saved subject=%s result=%s", subject_id(sender), updated
                )

                try:
                    reply_id = response.json().get("messages", [{}])[0].get("id")
                except (ValueError, KeyError, IndexError):
                    reply_id = None

                add_message(
                    sender,
                    direction="outbound",
                    body=reply_text,
                    message_id=reply_id,
                )

        if "statuses" in value:
            for status in value["statuses"]:
                status_id = status.get("id", "")
                status_name = status.get("status", "")
                key = (status_id, status_name)

                if key in seen_status_events:
                    continue

                _remember_seen(key, seen_status_events, _seen_status_order)
                logger.info(
                    "event=whatsapp_status message_id=%s status=%s subject=%s timestamp=%s",
                    status_id,
                    status_name,
                    subject_id(status.get("recipient_id", "")),
                    status.get("timestamp", ""),
                )

    except Exception:
        logger.exception("event=webhook_processing_failed")
        raise


def _handle_webhook_body(body: dict) -> None:
    """Process every entry, change, message and status in a Meta payload."""
    entries = body.get("entry") or []
    for entry in entries:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            messages = value.get("messages") or []
            statuses = value.get("statuses") or []

            for message in messages:
                message_value = dict(value)
                message_value["messages"] = [message]
                message_value.pop("statuses", None)
                _handle_webhook_value(message_value)

            for status in statuses:
                status_value = dict(value)
                status_value["statuses"] = [status]
                status_value.pop("messages", None)
                _handle_webhook_value(status_value)

    if not entries:
        logger.warning("event=webhook_payload_ignored reason=no_entries")


def _release_process_dedup(payload: dict) -> None:
    for message in payload.get("messages") or []:
        message_id = str(message.get("id") or "")
        if message_id:
            seen_message_ids.discard(message_id)
    for status in payload.get("statuses") or []:
        key = (str(status.get("id") or ""), str(status.get("status") or ""))
        seen_status_events.discard(key)


def _drain_event_queue(max_events: int = 100) -> int:
    processed = 0
    for _ in range(max_events):
        event = claim_event()
        if event is None:
            break
        try:
            _handle_webhook_value(event["payload"])
            complete_event(str(event["event_id"]))
        except Exception as error:
            _release_process_dedup(event["payload"])
            fail_event(
                str(event["event_id"]),
                f"{type(error).__name__}: {error}",
                attempts=int(event.get("attempts") or 0) + 1,
            )
            logger.exception("event=durable_event_failed event_id=%s", event["event_id"])
        processed += 1
    return processed


def _event_worker_loop() -> None:
    while not _queue_shutdown.is_set():
        processed = _drain_event_queue()
        if processed == 0:
            _queue_wakeup.wait(timeout=2.0)
            _queue_wakeup.clear()


# --- Document ingestion: OCR + embedding off the request thread ---


def _ingest_worker_loop() -> None:
    """Drain uploaded documents one at a time.

    Extraction and embedding are slow (OCR especially), so an upload only enqueues; this thread
    does the work. It sleeps until woken by an upload, and re-checks periodically so documents
    stranded by a crash or redeploy get picked back up.
    """
    from services.content_store import requeue_stale_documents
    from services.course_ingest import process_next_document

    while not _ingest_shutdown.is_set():
        worked = False
        try:
            worked = process_next_document()
        except Exception:
            logger.exception("event=ingest_worker_loop_error")
        if not worked:
            _ingest_wakeup.wait(timeout=30.0)
            _ingest_wakeup.clear()
            try:
                requeued = requeue_stale_documents()
                if requeued:
                    logger.warning("event=ingest_requeued_stale count=%d", requeued)
            except Exception:
                logger.exception("event=ingest_requeue_failed")


# --- Lead sync: pick up new rows in the leads source without a manual import ---


def _lead_sync_interval_seconds() -> int:
    """Minutes between automatic lead syncs. 0 (the default) disables the poller."""
    try:
        minutes = int(os.getenv("LEAD_SYNC_INTERVAL_MINUTES", "0").strip() or 0)
    except ValueError:
        return 0
    return max(0, minutes) * 60


def _run_lead_sync() -> dict:
    """One pass over the leads source. Idempotent — only phones new to the DB are written."""
    excel = os.getenv("LEAD_SYNC_EXCEL_PATH", "").strip() or None
    return sync_leads(excel_path=excel).as_dict()


def _run_lead_pipeline() -> dict:
    """New leads in → first contact out. The webhook handles everything after they reply."""
    sync = _run_lead_sync()
    outreach = dispatch_outreach().as_dict()
    return {"sync": sync, "outreach": outreach}


def _lead_sync_loop() -> None:
    interval = _lead_sync_interval_seconds()
    # Let boot settle (RAG warmup, first webhooks) before the first sheet read
    if _lead_sync_shutdown.wait(min(60, interval)):
        return
    while not _lead_sync_shutdown.is_set():
        try:
            _run_lead_pipeline()
        except Exception:
            logger.exception("event=lead_pipeline_failed")
        _lead_sync_shutdown.wait(interval)


@app.on_event("startup")
def _start_event_worker() -> None:
    global _queue_worker, _lead_sync_worker
    if _queue_worker and _queue_worker.is_alive():
        return
    _queue_shutdown.clear()
    _queue_worker = threading.Thread(target=_event_worker_loop, daemon=True)
    _queue_worker.start()
    _queue_wakeup.set()
    # Seed the admin password from ADMIN_PASSWORD on first boot; no-op once one is set.
    try:
        from services.admin_auth import bootstrap_admin_password

        bootstrap_admin_password()
    except Exception:
        logger.exception("event=admin_bootstrap_failed")
    # Build & vectorise the knowledge index off the request path so the first
    # customer reply isn't blocked on embedding-model load + encoding.
    threading.Thread(target=_warmup_rag, daemon=True).start()

    global _ingest_worker
    if not (_ingest_worker and _ingest_worker.is_alive()):
        _ingest_shutdown.clear()
        _ingest_worker = threading.Thread(target=_ingest_worker_loop, daemon=True)
        _ingest_worker.start()

    interval = _lead_sync_interval_seconds()
    if interval and not (_lead_sync_worker and _lead_sync_worker.is_alive()):
        _lead_sync_shutdown.clear()
        _lead_sync_worker = threading.Thread(target=_lead_sync_loop, daemon=True)
        _lead_sync_worker.start()
        logger.info("event=lead_sync_scheduled interval_seconds=%d", interval)


def _warmup_rag() -> None:
    try:
        warmup_rag()
        logger.info("event=rag_v2_warmup_complete")
    except Exception:
        logger.exception("event=rag_v2_warmup_failed")


@app.on_event("shutdown")
def _stop_event_worker() -> None:
    _queue_shutdown.set()
    _queue_wakeup.set()
    _lead_sync_shutdown.set()
    _ingest_shutdown.set()
    _ingest_wakeup.set()
    if _queue_worker:
        _queue_worker.join(timeout=5)
    if _lead_sync_worker:
        _lead_sync_worker.join(timeout=5)
    if _ingest_worker:
        _ingest_worker.join(timeout=5)


@app.post("/webhook/whatsapp")
async def receive_whatsapp_event(request: Request):
    body = await request.json()
    queued = enqueue_webhook_body(body)
    _queue_wakeup.set()
    return {"status": "received", "queued": queued}
