import os
import requests

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request


load_dotenv()

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN")
print("TOKEN PREFIX:", ACCESS_TOKEN[:20] if ACCESS_TOKEN else "")


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
        msg = value["messages"][0]["text"]["body"]
        sender = value["messages"][0]["from"]

        print("MESSAGE:", msg)

        response = requests.post(
            f"https://graph.facebook.com/v25.0/{PHONE_NUMBER_ID}/messages",
            headers={
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": sender,
                "type": "text",
                "text": {
                    "body": f"You said: {msg}"
                }
            }
        )
        print("STATUS:", response.status_code)
        print("RESPONSE:", response.text)
    except Exception as e:
        print(e)

    return {"status": "received"}
