# Timmins WhatsApp Webhook

FastAPI app for Meta WhatsApp verification, inbound message capture, Google Sheets outreach, and basic admin controls.

## Local Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload --port 8000
```

## Environment

```text
VERIFY_TOKEN=timmins_whatsapp_verify_2026
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_API_VERSION=v25.0
GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
GOOGLE_SHEET_ID=...
GOOGLE_WORKSHEET_NAME=Sheet1
WHATSAPP_SQLITE_PATH=whatsapp_bot.db
WHATSAPP_TEMPLATE_NAME=july_2026_intake_followup
WHATSAPP_TEMPLATE_LANGUAGE=en_US
```

## Render Settings

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Environment variable:

```text
VERIFY_TOKEN=timmins_whatsapp_verify_2026
```

## Meta Settings

Callback URL:

```text
https://YOUR_RENDER_SERVICE.onrender.com/webhook/whatsapp
```

Verify token:

```text
timmins_whatsapp_verify_2026
```

## Admin Endpoints

```text
POST /send-test
POST /run-outreach
GET /stats
```
