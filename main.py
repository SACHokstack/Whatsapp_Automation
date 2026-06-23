import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request

from services.google_sheets import update_lead
from services.whatsapp import send_text
from services.sqlite_store import add_message, init_db, upsert_lead

load_dotenv()

app = FastAPI()
seen_status_events: set[tuple[str, str]] = set()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
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
                local_updated = upsert_lead(
                    sender,
                    status="REPLIED",
                    last_reply=msg,
                )
                updated = update_lead(
                    sender,
                    status="REPLIED",
                    last_reply=msg,
                )
                print("LOCAL UPDATED:", local_updated)
                print("SHEET UPDATED:", updated)

                reply_text = _faq_reply(msg)
                response = send_text(sender, reply_text)
                print("AUTO REPLY STATUS:", response.status_code)
                print("AUTO REPLY RESPONSE:", response.text)

                local_updated = upsert_lead(
                    sender,
                    status="REPLIED",
                    last_message=reply_text,
                    last_reply=msg,
                )
                updated = update_lead(
                    sender,
                    status="REPLIED",
                    last_message=reply_text,
                    last_reply=msg,
                )
                print("LOCAL UPDATED AFTER REPLY:", local_updated)
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
