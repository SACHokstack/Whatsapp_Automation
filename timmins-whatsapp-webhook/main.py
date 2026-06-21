from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.outreach import run_outreach
from services.storage import get_stats, init_db, save_message
from services.whatsapp import send_message
from services.webhook import process_webhook_payload


load_dotenv()

app = FastAPI(title="Timmins WhatsApp Webhook")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")


class SendTestRequest(BaseModel):
    phone: str = Field(..., description="Recipient phone number in international format without +")
    text: str = Field(..., description="Text to send")


class OutreachRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1)
    dry_run: bool = False


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home():
    return {
        "status": "running",
        "service": "Timmins WhatsApp Webhook",
    }


@app.get("/stats")
def stats():
    return {
        "status": "ok",
        "database": get_stats(),
    }


@app.post("/send-test")
def send_test(payload: SendTestRequest):
    response = send_message(payload.phone, payload.text)
    response_body = {}
    try:
        response_body = response.json() if response.content else {}
    except Exception:
        response_body = {"raw": response.text}

    if response.ok:
        save_message(
            phone=payload.phone,
            direction="outbound",
            whatsapp_message_id=None,
            message_type="text",
            message_status="accepted",
            body=payload.text,
            raw_json=response_body,
        )

    return {
        "ok": response.ok,
        "status_code": response.status_code,
        "response": response_body if response_body else response.text,
    }


@app.post("/run-outreach")
def trigger_outreach(payload: OutreachRequest):
    return run_outreach(limit=payload.limit, dry_run=payload.dry_run)


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
    print("Incoming WhatsApp webhook:")
    print(body)

    try:
        msg = (
            body["entry"][0]["changes"][0]["value"]
            ["messages"][0]["text"]["body"]
        )
        print("MESSAGE:", msg)
    except (KeyError, IndexError, TypeError):
        pass

    processed = process_webhook_payload(body)
    return {"status": "received", "processed": processed}
