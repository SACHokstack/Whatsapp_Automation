# Deploying to Railway (with OpenRouter)

This replaces the Render deployment (`render.yaml`). Build/start/health are defined in
`railway.json`; Postgres and secrets are configured in the Railway dashboard.

## 1. Create the service
1. Railway → **New Project → Deploy from GitHub repo** → pick this repo.
2. Railway reads `railway.json`:
   - **Build**: installs `requirements.txt` and pre-downloads the FastEmbed model
     (`BAAI/bge-small-en-v1.5`) so the first reply isn't blocked on the model fetch.
   - **Start**: `uvicorn main:app --host 0.0.0.0 --port $PORT` (Railway injects `$PORT`).
   - **Health check**: `/health/ready`.

## 2. Add Postgres (persistent data)
1. In the project → **New → Database → PostgreSQL**.
2. On the web service → **Variables → Add Reference** → set `DATABASE_URL` to
   `${{Postgres.DATABASE_URL}}`.
   - The app uses Postgres when `DATABASE_URL` is set (leads, conversations, durable queue).
   - **Why this matters:** Railway's app filesystem is ephemeral — SQLite would be wiped on
     every redeploy. The RAG vector index is fine ephemeral (it rebuilds on startup).

## 3. Environment variables
Set these on the web service (**Variables**). Values marked *secret* come from your accounts.

| Variable | Value | Notes |
|---|---|---|
| `VERIFY_TOKEN` | *secret* | WhatsApp webhook verify token |
| `WHATSAPP_ACCESS_TOKEN` | *secret* | Meta Cloud API token |
| `WHATSAPP_PHONE_NUMBER_ID` | *secret* | Meta phone number id |
| `USE_INTERPRETER` | `true` | |
| `INTERPRETER_PROVIDER` | `openrouter` | switched from `bedrock` |
| `INTERPRETER_MODEL` | `openai/gpt-oss-120b` | OpenRouter model id (note: **not** the `openai.gpt-oss-120b-1:0` Bedrock id) |
| `RAG_GENERATION_PROVIDER` | `openrouter` | |
| `RAG_GENERATION_MODEL` | `openai/gpt-oss-120b` | |
| `OPENROUTER_API_KEY` | *secret* | your purchased OpenRouter key |
| `OPENROUTER_APP_NAME` | `Timmins WhatsApp Assistant` | optional (OpenRouter attribution) |
| `BOT_SAFE_MODE` | `true` | |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | reference, from step 2 |

Bedrock vars (`AWS_*`, `BEDROCK_REGION`) are **not needed** on OpenRouter — leave them unset.

### Lead pipeline (ads → sheet → Postgres → first contact)
On each tick the app pulls new leads in and sends them their course's approved template; the
webhook takes over from their reply onwards. Nothing here needs a human.

| Variable | Value | Notes |
|---|---|---|
| `LEAD_SYNC_INTERVAL_MINUTES` | e.g. `15` | drives the whole pipeline. Unset/`0` = no automation at all |
| `AUTO_OUTREACH_ENABLED` | `true` | **sends real WhatsApp messages.** Off by default |
| `AUTO_OUTREACH_MAX_PER_RUN` | `25` | cap per tick — blast radius if the sheet gets a big import |
| `AUTO_OUTREACH_HOURS` | `9-19` | send window; outside it the tick syncs but doesn't message |
| `AUTO_OUTREACH_TZ` | `Asia/Kuala_Lumpur` | timezone the window is measured in |
| `AUTO_OUTREACH_DELAY_SECONDS` | `3` | pause between sends |
| `LEAD_SYNC_TOKEN` | *secret* | enables `POST /admin/sync-leads` to run a tick immediately |
| `LEAD_SYNC_EXCEL_PATH` | *(unset)* | only for an .xlsx shipped with the deploy; normally the sheet is the source |
| `GOOGLE_SHEET_URL` | *your sheet link* | source workbook when no Excel path is set |
| `LEAD_SYNC_WORKSHEET` | e.g. `Sheet1` | tab holding the **raw Meta export** (all courses in one sheet, course read from `ad_name`). Leave unset for one-tab-per-course workbooks |
| `LEAD_SYNC_CONTACT_CUTOFF` | e.g. `2026-08-15T00:00:00+00:00` | leads whose `created_time` predates this import as `PRE_LAUNCH` and are **never auto-contacted** — they were reached out to by hand. Set this to your go-live moment |
| `ENABLE_GOOGLE_SHEETS` | `true` | also write `lead_status=CONTACTED` back into the source sheet |
| `GOOGLE_CREDENTIALS_JSON` | *secret* | service-account JSON, one line — share the sheet with its `client_email` |

Safety properties: the sync only inserts phones not already in `leads`, and outreach only messages
leads still at `CREATED`, flipping them to `CONTACTED` on success — so a lead is contacted once no
matter how often the tick runs. A 4xx (bad number, rejected template) parks the lead at
`OUTREACH_FAILED` instead of retrying forever; a 429/network error leaves it pending for the next
tick. `GET /health/ready` reports `lead_pipeline.sync_interval_minutes` and `auto_outreach`.

**Recommended rollout:** deploy with `AUTO_OUTREACH_ENABLED` unset, confirm `/health/ready` and one
sync tick, then run `python outreach.py --course <slug> --limit 1` to watch a single real send land
before flipping the flag on.

## 4. Point WhatsApp at the new URL
Once deployed, Railway gives a public domain (`https://<app>.up.railway.app`). In the Meta
app dashboard, set the webhook callback URL to `https://<app>.up.railway.app/webhook/whatsapp`
and the verify token to `VERIFY_TOKEN`.

## 5. Verify
- `GET /health/ready` → `{"status":"ready", "database":"postgres", ...}`.
- Send a WhatsApp message; check logs for `event=rag_v2_index_built ... generation_provider=openrouter`.

## Provider coverage (all three LLM call sites use OpenRouter)
- Interpreter (`services/interpret.py`) — ✅ openrouter branch.
- RAG generation (`rag_v2/runtime.py`) — ✅ `OpenRouterGroundedGenerator`.
- Course recommender (`services/recommend.py`) — ✅ openrouter branch (added for this migration).

## Rollback
Set `INTERPRETER_PROVIDER` / `RAG_GENERATION_PROVIDER` back to `bedrock` and restore the
`AWS_*` / `BEDROCK_REGION` vars — no code change needed.
