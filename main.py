import os

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

    if msg_lower in {"hi", "hello", "hey"}:
        return "Hi, this is Timmins Training. How can we help you today?"
    if "fee" in msg_lower:
        return "The course fee is RM XXXX. Would you like the full brochure?"
    if "duration" in msg_lower:
        return "The program runs for X weeks."
    if "schedule" in msg_lower:
        return "Classes start in July."
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
    experience = (data.get("experience") or "").strip().lower()
    budget = (data.get("budget") or "").strip().lower()
    availability = (data.get("availability") or "").strip().lower()

    if experience == "yes":
        score += 3

    if availability in {"immediately", "now", "today", "asap"} or "immediate" in availability:
        score += 3

    if (
        budget == "yes"
        or budget in {"ok", "okay", "sure", "yes"}
        or any(ch.isdigit() for ch in budget)
    ):
        score += 4
    return score


def _lead_status_from_score(score: int) -> str:
    if score >= 7:
        return "HOT"
    if score >= 4:
        return "WARM"
    return "COLD"


def _qualification_prompt() -> str:
    return "Great. Are you:\n1. Student\n2. Graduate\n3. Working professional"


def _experience_prompt() -> str:
    return "Do you have software testing experience? Reply Yes or No."


def _budget_prompt() -> str:
    return "What is your budget for the course? Reply with an amount or Yes if your budget is ready."


def _availability_prompt() -> str:
    return "When are you available to start? Reply with your timeframe."


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


def _process_qualification(message: str, lead: dict[str, str]) -> tuple[str | None, dict[str, str | int] | None]:
    msg_lower = message.lower().strip()
    step = (lead.get("qualification_step") or "").strip().lower()

    if not step:
        if "interested" in msg_lower:
            return _qualification_prompt(), {
                "status": "QUALIFYING",
                "qualification_step": "occupation",
            }
        return None, None

    if step == "occupation":
        occupation = _parse_occupation(message)
        if not occupation:
            return "Please reply with 1, 2, or 3.", None
        return _experience_prompt(), {
            "status": "QUALIFYING",
            "qualification_step": "experience",
            "occupation": occupation,
        }

    if step == "experience":
        if not (_is_yes(msg_lower) or _is_no(msg_lower)):
            return "Please reply Yes or No.", None
        experience = "Yes" if _is_yes(msg_lower) else "No"
        return _budget_prompt(), {
            "status": "QUALIFYING",
            "qualification_step": "budget",
            "experience": experience,
        }

    if step == "budget":
        budget = message.strip()
        if not budget:
            return "Please share your budget.", None
        return _availability_prompt(), {
            "status": "QUALIFYING",
            "qualification_step": "availability",
            "budget": budget,
        }

    if step == "availability":
        availability = message.strip()
        if not availability:
            return "Please share your availability.", None
        lead_after = dict(lead)
        lead_after["experience"] = lead.get("experience", "")
        lead_after["budget"] = lead.get("budget", "")
        lead_after["availability"] = availability
        score = _lead_score(lead_after)
        status = _lead_status_from_score(score)
        return _final_qualification_reply(), {
            "status": status,
            "qualification_step": "",
            "availability": availability,
            "lead_score": score,
        }

    return None, None


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
                local_updated = upsert_lead(
                    sender,
                    status="REPLIED",
                    last_reply=msg,
                )
                print("LOCAL UPDATED:", local_updated)
                updated = _update_sheet_if_enabled(
                    sender,
                    status="REPLIED",
                    last_reply=msg,
                )
                print("SHEET UPDATED:", updated)

                reply_text, qualification_updates = _process_qualification(msg, lead)
                if reply_text is None:
                    reply_text = _faq_reply(msg)
                    qualification_updates = None

                final_status = (
                    str(qualification_updates.get("status"))
                    if qualification_updates and qualification_updates.get("status")
                    else str(lead.get("status") or "REPLIED")
                )

                if qualification_updates:
                    local_updated = upsert_lead(sender, **qualification_updates)
                    print("LOCAL QUALIFICATION UPDATED:", local_updated)
                    updated = _update_sheet_if_enabled(sender, **qualification_updates)
                    print("SHEET QUALIFICATION UPDATED:", updated)

                    qualification_status = str(qualification_updates.get("status") or "")
                    if qualification_status == "HOT":
                        hot_summary = (
                            "🔥 HOT LEAD\n\n"
                            f"Phone: {sender}\n"
                            f"Occupation: {qualification_updates.get('occupation') or lead.get('occupation', '')}\n"
                            f"Experience: {qualification_updates.get('experience') or lead.get('experience', '')}\n"
                            f"Budget: {qualification_updates.get('budget') or lead.get('budget', '')}\n"
                            f"Availability: {qualification_updates.get('availability') or lead.get('availability', '')}\n"
                            f"Score: {qualification_updates.get('lead_score') or ''}"
                        )
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

                local_updated = upsert_lead(
                    sender,
                    status=final_status,
                    last_message=reply_text,
                    last_reply=msg,
                )
                print("LOCAL UPDATED AFTER REPLY:", local_updated)
                updated = _update_sheet_if_enabled(
                    sender,
                    status=final_status,
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

    return {"status": "received"}
