# Timmins WhatsApp Lead Automation

A FastAPI service that receives WhatsApp Cloud API webhooks, answers course questions,
qualifies leads, stores durable conversation state in PostgreSQL (SQLite locally), and optionally synchronizes lead data
with Google Sheets.

## Main components

- `main.py` — HTTP endpoints, webhook orchestration, qualification state machine and scoring.
- `services/whatsapp.py` — WhatsApp Cloud API client.
- `services/sqlite_store.py` — local lead and message persistence.
- `services/postgres_store.py` and `services/persistence.py` — production persistence backend.
- `services/durable_queue.py` — idempotent webhook inbox, leases and retries.
- `services/conversation_controller.py` — exact/RAG/clarification routing.
- `services/structured_facts.py` — authoritative commercial and policy answers.
- `services/response_guard.py` — pre-send grounding and safety validation.
- `services/google_sheets.py` — Google Sheets lookup and batched updates.
- `services/ai_reply.py` and `services/knowledge_base.py` — grounded FAQ answers.
- `courses/` — one configuration and overview directory per course.
- `outreach.py` — template-based bulk outreach runner.
- `import_leads.py` — Meta Lead Ads spreadsheet importer.

See [Architecture](docs/ARCHITECTURE.md) and [Operations](docs/OPERATIONS.md) for more detail.

## Local setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
uvicorn main:app --reload
```

Never commit `.env`, `credentials.json`, customer spreadsheets, or local databases.

## Required configuration

- `VERIFY_TOKEN` — Meta webhook verification token.
- `WHATSAPP_ACCESS_TOKEN` — WhatsApp Cloud API access token.
- `WHATSAPP_PHONE_NUMBER_ID` — sending phone-number ID.
- AWS credentials with Bedrock model access: configure `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY`, `AWS_BEARER_TOKEN_BEDROCK`, an AWS profile locally, or an IAM role.
- `BEDROCK_REGION` — defaults to `ap-south-1` (Mumbai).
- `RAG_GENERATION_PROVIDER` and `INTERPRETER_PROVIDER` — set to `bedrock` for GPT-OSS.

Optional integrations and tuning are documented in `.env.example`.

Production requires `DATABASE_URL` pointing to PostgreSQL and should keep `BOT_SAFE_MODE=true`
during rollout. The included Render Blueprint declares a paid web service and managed database;
syncing that Blueprint can incur hosting charges.

## Verification

```bash
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
```

CI runs linting and the unit suite for pushes and pull requests.

## Common commands

```bash
python scripts/local_chat.py --course embedded-c-july-2026
python import_leads.py --file leads.xlsx --dry-run
python outreach.py --course embedded-linux-yocto-aug-2026 --dry-run
python setup_sheet.py --dry-run
```

The local chat command keeps conversation memory in the current terminal and uses the same
routing and response guard as the webhook. It never sends messages to WhatsApp. Use `/courses`
to list course slugs, `/course <slug>` to switch, `/state` to inspect memory, and `/quit` to exit.

Remove `--dry-run` only after checking the selected workbook, worksheet, course, and lead count.

## Adding or updating a course

Create `courses/<slug>/config.yaml` and `courses/<slug>/overview.md`. Existing process caches
automatically invalidate when either file changes. Set expired courses to `active: false`.

## License

No license has been selected. Copyright remains with the repository owner; choose and add a
`LICENSE` file before distributing the software to third parties.
