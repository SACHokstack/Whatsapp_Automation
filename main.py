import os
import re

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request

from services.google_sheets import update_lead
from services.whatsapp import send_text
from services.sqlite_store import add_message, get_lead, init_db, upsert_lead

load_dotenv()

app = FastAPI()
seen_status_events: set[tuple[str, str]] = set()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
RAJ_PHONE = os.getenv("RAJ_PHONE", "").strip()
ENABLE_GOOGLE_SHEETS = os.getenv("ENABLE_GOOGLE_SHEETS", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
print("TOKEN PREFIX:", ACCESS_TOKEN[:20] if ACCESS_TOKEN else "")
init_db()


def _faq_reply(msg: str) -> str:
    msg_lower = msg.lower()

    faq_kb = {
        "fees": "The course fee is RM XXXX. Would you like the full brochure?",
        "fee": "The course fee is RM XXXX. Would you like the full brochure?",
        "duration": "The program runs for X weeks.",
        "placement": "We can share placement support details after a quick qualification.",
        "schedule": "Classes start in July.",
        "requirements": "The main requirement is interest in software testing and commitment to attend.",
    }

    if msg_lower in {"hi", "hello", "hey"}:
        return "Hi, this is Timmins Training. How can we help you today?"

    for keyword, reply in faq_kb.items():
        if keyword in msg_lower:
            return reply

    return "Thank you for your interest. A consultant will contact you shortly."


def _update_sheet_if_enabled(phone: str, **kwargs) -> bool:
    if not ENABLE_GOOGLE_SHEETS:
        print("SHEET SKIPPED: disabled by ENABLE_GOOGLE_SHEETS")
        return False

    try:
        return update_lead(phone, **kwargs)
    except Exception as sheet_error:
        print("SHEET ERROR:", sheet_error)
        return False


def _is_yes(msg_lower: str) -> bool:
    return msg_lower in {"yes", "y", "yeah", "yep", "sure", "ok", "okay"}


def _is_no(msg_lower: str) -> bool:
    return msg_lower in {"no", "n", "nope", "not really"}


def _lead_score(data: dict[str, str]) -> int:
    score = 0
    occupation = (data.get("occupation") or "").strip().lower()
    experience = (data.get("experience") or "").strip().lower()
    budget = (data.get("budget") or "").strip().lower()
    availability = (data.get("availability") or "").strip().lower()

    if occupation == "graduate":
        score += 3

    if experience == "yes":
        score += 2

    budget_value = _extract_int(budget)
    if budget_value is not None and budget_value >= 4000:
        score += 4

    availability_days = _extract_availability_days(availability)
    if availability_days is not None and availability_days <= 30:
        score += 2
    return score


def _lead_status_from_score(score: int) -> str:
    if score >= 8:
        return "HOT"
    if score >= 5:
        return "WARM"
    return "COLD"


def _qualification_prompt() -> str:
    return "Great. Are you:\n1. Student\n2. Graduate\n3. Working professional"


def _experience_prompt() -> str:
    return "Do you have software testing experience? Reply Yes or No."


def _budget_prompt() -> str:
    return "What is your budget for the course? Reply with an amount like RM4000."


def _availability_prompt() -> str:
    return "When are you available to start? Reply with days, for example 14 days or immediately."


def _final_qualification_reply() -> str:
    return "Thanks. A consultant will contact you shortly."


def _parse_occupation(msg: str) -> str | None:
    msg_lower = msg.lower().strip()
    if msg_lower in {"1", "student"}:
        return "Student"
    if msg_lower in {"2", "graduate"}:
        return "Graduate"
    if msg_lower in {"3", "working professional", "professional", "working"}:
        return "Working professional"
    return None


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


def _process_conversation(message: str, lead: dict[str, str]) -> tuple[str | None, dict[str, str | int] | None]:
    msg_lower = message.lower().strip()
    state = (
        lead.get("conversation_state")
        or lead.get("qualification_step")
        or lead.get("status")
        or ""
    ).strip().upper()

    if "interested" in msg_lower:
        return _qualification_prompt(), {
            "status": "ASKING_OCCUPATION",
            "conversation_state": "ASKING_OCCUPATION",
            "qualification_step": "ASKING_OCCUPATION",
        }

    if not state:
        return None, None

    if state == "ASKING_OCCUPATION":
        occupation = _parse_occupation(message)
        if not occupation:
            return "Please reply with 1, 2, or 3.", None
        return _experience_prompt(), {
            "status": "ASKING_EXPERIENCE",
            "conversation_state": "ASKING_EXPERIENCE",
            "qualification_step": "ASKING_EXPERIENCE",
            "occupation": occupation,
        }

    if state == "ASKING_EXPERIENCE":
        if not (_is_yes(msg_lower) or _is_no(msg_lower)):
            return "Please reply Yes or No.", None
        experience = "Yes" if _is_yes(msg_lower) else "No"
        return _budget_prompt(), {
            "status": "ASKING_BUDGET",
            "conversation_state": "ASKING_BUDGET",
            "qualification_step": "ASKING_BUDGET",
            "experience": experience,
        }

    if state == "ASKING_BUDGET":
        budget = message.strip()
        if not budget:
            return "Please share your budget.", None
        return _availability_prompt(), {
            "status": "ASKING_AVAILABILITY",
            "conversation_state": "ASKING_AVAILABILITY",
            "qualification_step": "ASKING_AVAILABILITY",
            "budget": budget,
        }

    if state == "ASKING_AVAILABILITY":
        availability = message.strip()
        if not availability:
            return "Please share your availability.", None
        lead_after = dict(lead)
        lead_after["availability"] = availability
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


def _hot_lead_summary(sender: str, lead: dict[str, str], updates: dict[str, str | int]) -> str:
    name = (lead.get("name") or "").strip() or "Unknown"
    occupation = str(updates.get("occupation") or lead.get("occupation") or "")
    experience = str(updates.get("experience") or lead.get("experience") or "")
    budget = str(updates.get("budget") or lead.get("budget") or "")
    availability = str(updates.get("availability") or lead.get("availability") or "")
    score = str(updates.get("lead_score") or lead.get("lead_score") or "")

    return (
        "🔥 HOT LEAD\n\n"
        f"Name: {name}\n"
        f"Phone: {sender}\n"
        f"Occupation: {occupation}\n"
        f"Experience: {experience}\n"
        f"Budget: RM {budget}\n"
        f"Availability: {availability}\n\n"
        "Recommendation: Call within 24 hours.\n"
        f"Score: {score}"
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


def _human_handoff_summary(sender: str, lead: dict[str, str], reason: str) -> str:
    name = (lead.get("name") or "").strip() or "Unknown"
    return (
        "🚨 Human Attention Required\n\n"
        f"Lead: {name}\n"
        f"Phone: {sender}\n\n"
        f"Reason:\n{reason}"
    )


@app.get("/")
def home():
    return {
        "status": "running",
        "service": "Timmins WhatsApp Webhook",
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


@app.post("/webhook/whatsapp")
async def receive_whatsapp_event(request: Request):
    body = await request.json()

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

                human_reason = _human_escalation_reason(msg.lower())
                if human_reason:
                    local_updated = upsert_lead(
                        sender,
                        status="NEEDS_HUMAN",
                        conversation_state="NEEDS_HUMAN",
                        assigned_to="Raj",
                    )
                    print("LOCAL HUMAN ESCALATION UPDATED:", local_updated)
                    updated = _update_sheet_if_enabled(
                        sender,
                        status="NEEDS_HUMAN",
                        conversation_state="NEEDS_HUMAN",
                        assigned_to="Raj",
                    )
                    print("SHEET HUMAN ESCALATION UPDATED:", updated)

                    human_summary = _human_handoff_summary(sender, lead, human_reason)
                    if RAJ_PHONE:
                        raj_response = send_text(RAJ_PHONE, human_summary)
                        print("RAJ HUMAN ALERT STATUS:", raj_response.status_code)
                        print("RAJ HUMAN ALERT RESPONSE:", raj_response.text)
                        add_message(
                            RAJ_PHONE,
                            direction="outbound",
                            body=human_summary,
                            message_id=None,
                        )
                    else:
                        print("RAJ HUMAN ALERT SKIPPED: RAJ_PHONE not set")

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
                    return {"status": "received"}

                reply_text, state_updates = _process_conversation(msg, lead)
                if reply_text is None:
                    reply_text = _faq_reply(msg)
                    state_updates = None

                if state_updates:
                    local_updated = upsert_lead(sender, **state_updates)
                    print("LOCAL STATE UPDATED:", local_updated)
                    updated = _update_sheet_if_enabled(sender, **state_updates)
                    print("SHEET STATE UPDATED:", updated)

                    if str(state_updates.get("status") or "") == "HOT":
                        hot_summary = _hot_lead_summary(sender, lead, state_updates)
                        if RAJ_PHONE:
                            raj_response = send_text(RAJ_PHONE, hot_summary)
                            print("RAJ NOTIFY STATUS:", raj_response.status_code)
                            print("RAJ NOTIFY RESPONSE:", raj_response.text)
                            add_message(
                                RAJ_PHONE,
                                direction="outbound",
                                body=hot_summary,
                                message_id=None,
                            )
                        else:
                            print("RAJ NOTIFY SKIPPED: RAJ_PHONE not set")

                response = send_text(sender, reply_text)
                print("AUTO REPLY STATUS:", response.status_code)
                print("AUTO REPLY RESPONSE:", response.text)

                local_updated = upsert_lead(sender, last_message=reply_text, last_reply=msg)
                print("LOCAL UPDATED AFTER REPLY:", local_updated)
                updated = _update_sheet_if_enabled(sender, last_message=reply_text, last_reply=msg)
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

    return {"status": "received"}
