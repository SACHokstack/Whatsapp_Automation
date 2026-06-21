Yes. Build only the **minimal webhook server** now. This server will let Meta verify your callback URL and receive WhatsApp replies.

## Step 1: Create project

```bash
mkdir timmins-whatsapp-webhook
cd timmins-whatsapp-webhook
```

Create files:

```bash
touch main.py requirements.txt .env
```

## Step 2: Add `requirements.txt`

```txt
fastapi
uvicorn
python-dotenv
```

## Step 3: Add `.env`

```env
VERIFY_TOKEN=timmins_whatsapp_verify_2026
```

You can change this token, but whatever you put here must also be entered in Meta’s **Verify token** field.

## Step 4: Add `main.py`

```python
import os
from fastapi import FastAPI, Request, Query, HTTPException
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")


@app.get("/")
def home():
    return {
        "status": "running",
        "service": "Timmins WhatsApp Webhook"
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

    return {"status": "received"}
```

## Step 5: Test locally

Run:

```bash
uvicorn main:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

You should see:

```json
{
  "status": "running",
  "service": "Timmins WhatsApp Webhook"
}
```

Test Meta-style verification locally:

```bash
curl "http://127.0.0.1:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=timmins_whatsapp_verify_2026&hub.challenge=12345"
```

Expected response:

```text
12345
```

## Step 6: Deploy on Render

Push to GitHub first:

```bash
git init
git add .
git commit -m "Initial WhatsApp webhook server"
git branch -M main
git remote add origin YOUR_GITHUB_REPO_URL
git push -u origin main
```

Then go to Render:

```text
Render → New → Web Service → Connect GitHub repo
```

Use these settings:

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Add environment variable in Render:

```text
VERIFY_TOKEN = timmins_whatsapp_verify_2026
```

Deploy.

## Step 7: Use this in Meta

After Render gives you a URL like:

```text
https://timmins-whatsapp-webhook.onrender.com
```

Use this in Meta:

```text
Callback URL:
https://timmins-whatsapp-webhook.onrender.com/webhook/whatsapp

Verify token:
timmins_whatsapp_verify_2026
```

Then click:

```text
Verify and save
```

## Step 8: Subscribe to messages

After verification succeeds, subscribe to:

```text
messages
```

That’s it. At this stage, the server only does two things:

```text
1. Lets Meta verify your webhook
2. Prints incoming WhatsApp events/replies in server logs
```

Once this is verified, we build the next part: **Google Sheet sender + WhatsApp template sending**.
