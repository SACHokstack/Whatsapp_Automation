# Timmins WhatsApp Webhook

Minimal FastAPI webhook for Meta WhatsApp Cloud API verification and incoming message events.

## Local Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload --port 8000
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
