import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request


load_dotenv()

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")


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

    return {"status": "received"}
