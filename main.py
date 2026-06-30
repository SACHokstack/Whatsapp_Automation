import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request

from services.ai_reply import ai_reply
from services.course_loader import detect_course, get_course
from services.knowledge_base import topic_for_message
from services.reply_cache import get as cache_get
from services.reply_cache import set as cache_set
from services.google_sheets import update_lead, update_lead_in, append_hot_lead
from services.whatsapp import send_text
from services.sqlite_store import (
    add_message,
    get_conversation_history,
    get_dashboard_summary,
    get_lead,
    init_db,
    upsert_lead,
)

load_dotenv()

app = FastAPI()
seen_status_events: set[tuple[str, str]] = set()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ENABLE_GOOGLE_SHEETS = os.getenv("ENABLE_GOOGLE_SHEETS", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
init_db()


def _faq_reply(msg: str, course=None) -> str:
    cache_key = f"{getattr(course, 'slug', '')}:{msg}"
    cached = cache_get(cache_key)
    if cached:
        print("CACHE HIT:", msg[:40])
        return cached
    reply = ai_reply(msg, course=course)
    cache_set(cache_key, reply)
    return reply


def _resolve_course(lead: dict, msg: str):
    slug = lead.get("course") or ""
    if slug:
        return get_course(slug)
    course = detect_course(msg)
    return course


_SHEET_STATUS_VALUES = {"HOT", "WARM", "COLD", "CONTACTED", "ENGAGED", "NEEDS_HUMAN"}

def _update_sheet_if_enabled(phone: str, worksheet_name: str | None = None, **kwargs) -> bool:
    if not ENABLE_GOOGLE_SHEETS:
        print("SHEET SKIPPED: disabled by ENABLE_GOOGLE_SHEETS")
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

    try:
        if worksheet_name:
            return update_lead_in(phone, worksheet_name, **sheet_kwargs)
        return update_lead(phone, **sheet_kwargs)
    except Exception as sheet_error:
        print("SHEET ERROR:", sheet_error)
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()



def _extract_int(text: str) -> int | None:
    match = re.search(r"(\d+)", text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_availability_days(text: str) -> int | None:
    text_lower = (text or "").strip().lower()
    if not text_lower:
        return None
    if text_lower in {"immediately", "now", "today", "asap"} or "immediate" in text_lower:
        return 0
    return _extract_int(text_lower)


def _lead_score(data: dict[str, str]) -> int:
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

    # Relevant technologies mentioned
    tech = (data.get("technologies") or "").lower()
    if any(t in tech for t in ("selenium", "testing", "qa", "test", "playwright", "jmeter", "postman", "cypress", "appium")):
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
    if job_title and company:
        return (
            f"Great! As a {job_title} at {company}, how many years of experience do you have?\n\n"
            f"e.g. 2 years, 5 years, less than 1 year"
        )
    if job_title:
        return (
            f"Great! As a {job_title}, how many years of experience do you have?\n\n"
            f"e.g. 2 years, 5 years, less than 1 year"
        )
    return (
        "Great! How many years of experience do you have in your current role?\n\n"
        "e.g. 2 years, 5 years, less than 1 year"
    )


def _technologies_prompt(lead: dict) -> str:
    job_title = (lead.get("job_title") or "").strip()
    if job_title:
        return (
            f"Which tools or technologies do you use as a {job_title}?\n\n"
            f"e.g. Selenium, Jira, Postman, Python — just share whatever you use day to day"
        )
    return (
        "Which tools or technologies do you use in your work?\n\n"
        "e.g. Selenium, Jira, Postman, Python — just share whatever you use day to day"
    )


def _motivation_prompt() -> str:
    return (
        "What prompted you to look into this training?\n\n"
        "e.g. Want to upskill, company asked me to, exploring options, heard from a colleague — anything works!"
    )


def _learning_goals_prompt() -> str:
    return (
        "What are you hoping to learn from this training?\n\n"
        "e.g. Learn Playwright, move into automation, improve my testing skills, get certified"
    )


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
    "hi", "hello", "hey", "hiya", "helo", "hai",
    "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "ok", "okay", "noted",
}

def _is_greeting(msg: str) -> bool:
    return msg.strip().lower() in _GREETING_WORDS


def _first_contact_greeting(lead: dict, course) -> str:
    first_name = (lead.get("name") or "").strip().split()[0] if lead.get("name") else ""
    greeting = f"Hi {first_name}! " if first_name else "Hi! "
    course_name = getattr(course, "name", "our upcoming training program") if course else "our upcoming training program"
    return (
        f"{greeting}Thanks for getting back to us.\n\n"
        f"I'm Timmins' assistant for *{course_name}*.\n\n"
        f"I can help you with course details, fees, schedule, HRDC, and more — just ask me anything.\n\n"
        f"Or reply *Interested* and I'll ask you a few quick questions so our consultant can follow up with the right information for you."
    )


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
    return (
        lead.get("conversation_state")
        or lead.get("qualification_step")
        or ""
    ).strip().upper()


def _state_update(state: str, **extra) -> dict:
    return {
        "status": state,
        "conversation_state": state,
        "qualification_step": state,
        **extra,
    }


def _process_conversation(message: str, lead: dict[str, str], course=None) -> tuple[str | None, dict[str, str | int] | None]:
    msg_lower = message.lower().strip()
    state = _get_state(lead)
    lead_status = (lead.get("status") or "").upper()

    # First reply after outreach template — send warm greeting with CTA
    if state not in _ACTIVE_STATES and lead_status == "CONTACTED" and not ("interested" in msg_lower or "yes" in msg_lower):
        return _first_contact_greeting(lead, course), {"status": "ENGAGED"}

    # Pure greeting when not in active qualification — also greet
    if _is_greeting(message) and state not in _ACTIVE_STATES:
        return _first_contact_greeting(lead, course), None

    # Trigger: "interested" or "yes" starts qualification
    if ("interested" in msg_lower or msg_lower == "yes") and state not in _ACTIVE_STATES:
        return _experience_years_prompt(lead), _state_update("ASKING_EXPERIENCE_YEARS")

    if state not in _ACTIVE_STATES:
        return None, None

    if state == "ASKING_EXPERIENCE_YEARS":
        years = message.strip()
        if not years:
            return "Could you share how many years of experience you have?", None
        return _technologies_prompt(lead), _state_update("ASKING_TECHNOLOGIES", experience_years=years)

    if state == "ASKING_TECHNOLOGIES":
        tech = message.strip()
        if not tech:
            return "Which tools or technologies do you currently use?", None
        return _motivation_prompt(), _state_update("ASKING_MOTIVATION", technologies=tech)

    if state == "ASKING_MOTIVATION":
        motivation = message.strip()
        if not motivation:
            return "What brought you to us?", None
        return _learning_goals_prompt(), _state_update("ASKING_LEARNING_GOALS", motivation=motivation)

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
        return _availability_prompt(), _state_update("ASKING_AVAILABILITY", funding_path=funding_path)

    if state == "ASKING_AVAILABILITY":
        availability = message.strip()
        if not availability:
            return "When are you available to start?", None
        lead_after = {**lead, "availability": availability}
        score = _lead_score(lead_after)
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
    print(f"LOCAL HUMAN QUEUE UPDATED ({source}):", local_updated)
    updated = _update_sheet_if_enabled(sender, **queue_updates)
    print(f"SHEET HUMAN QUEUE UPDATED ({source}):", updated)


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
    triggers = {
        "discount": "Requested discount",
        "special pricing": "Requested special pricing",
        "group booking": "Requested group booking",
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


@app.get("/stats")
def stats():
    return get_dashboard_summary()


@app.get("/conversation/{phone}")
def conversation(phone: str):
    return {
        "phone": phone,
        "history": get_conversation_history(phone),
    }


@app.get("/lead/{phone}")
def lead_detail(phone: str):
    return {
        "lead": get_lead(phone),
        "history": get_conversation_history(phone),
    }


@app.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


def _handle_webhook_body(body: dict) -> None:
    try:
        value = body["entry"][0]["changes"][0]["value"]

        if "messages" in value:
            msg = value["messages"][0].get("text", {}).get("body", "")
            sender = value["messages"][0].get("from", "")

            if msg and sender:
                print("MESSAGE:", msg)
                add_message(
                    sender,
                    direction="inbound",
                    body=msg,
                    message_id=value["messages"][0].get("id"),
                )
                lead = get_lead(sender) or {}
                course = _resolve_course(lead, msg)
                if course and not lead.get("course"):
                    upsert_lead(sender, course=course.slug)

                human_reason = _human_escalation_reason(msg.lower())
                if human_reason:
                    _queue_human_handoff(sender, lead, reason=human_reason, source="keyword")

                    customer_reply = (
                        "Thanks. A consultant will contact you shortly regarding that request."
                    )
                    response = send_text(sender, customer_reply)
                    print("AUTO REPLY STATUS:", response.status_code)
                    print("AUTO REPLY RESPONSE:", response.text)
                    add_message(
                        sender,
                        direction="outbound",
                        body=customer_reply,
                        message_id=None,
                    )
                    return

                worksheet = getattr(course, "worksheet_name", None)
                reply_text, state_updates = _process_conversation(msg, lead, course=course)
                if reply_text is None:
                    reply_text = _faq_reply(msg, course=course)
                    state_updates = None
                topic_label = str(topic_for_message(msg) or "GENERAL").upper()
                topic_reason = "groq_rag"

                if state_updates:
                    local_updated = upsert_lead(sender, **state_updates)
                    print("LOCAL STATE UPDATED:", local_updated)
                    updated = _update_sheet_if_enabled(sender, worksheet_name=worksheet, **state_updates)
                    print("SHEET STATE UPDATED:", updated)

                    if str(state_updates.get("status") or "") == "HOT":
                        _queue_human_handoff(
                            sender,
                            lead,
                            reason=f"HOT lead score {state_updates.get('lead_score')}",
                            source="hot_lead",
                            intent="HOT_LEAD",
                        )
                        if ENABLE_GOOGLE_SHEETS:
                            appended = append_hot_lead(sender, lead, state_updates)
                            print("HOT LEAD → SHEET:", appended)

                response = send_text(sender, reply_text)
                print("AUTO REPLY STATUS:", response.status_code)
                print("AUTO REPLY RESPONSE:", response.text)

                local_updated = upsert_lead(
                    sender,
                    last_intent=topic_label,
                    last_intent_reason=topic_reason,
                    last_message=reply_text,
                    last_reply=msg,
                )
                print("LOCAL UPDATED AFTER REPLY:", local_updated)
                updated = _update_sheet_if_enabled(
                    sender,
                    last_intent=topic_label,
                    last_intent_reason=topic_reason,
                    last_message=reply_text,
                    last_reply=msg,
                )
                print("SHEET UPDATED AFTER REPLY:", updated)

                try:
                    reply_id = response.json().get("messages", [{}])[0].get("id")
                except Exception:
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

                seen_status_events.add(key)
                print(
                    "STATUS:",
                    status_id,
                    status_name,
                    "recipient=",
                    status.get("recipient_id", ""),
                    "timestamp=",
                    status.get("timestamp", ""),
                )

    except Exception as e:
        print(e)


@app.post("/webhook/whatsapp")
async def receive_whatsapp_event(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    background_tasks.add_task(_handle_webhook_body, body)
    return {"status": "received"}
