# Handover runbook

Everything needed to run, back up and recover this service. Written for whoever inherits it,
which may not be the person who built it.

---

## 1. What this is

A WhatsApp assistant for Timmins Training Consulting. It answers course enquiries from approved
content, and it contacts new leads from Meta ad campaigns automatically.

Three moving parts, all in one deployed service:

| Part | What it does |
|---|---|
| **WhatsApp webhook** | Receives customer messages, works out a reply, sends it |
| **Lead pipeline** | Reads new leads from the Google Sheet, sends the first message |
| **Admin dashboard** | `/admin` — courses, company knowledge, leads, runtime controls |

The bot answers from two sources: **structured facts** (fees, dates, venue — the exact answers)
and **RAG search** over course documents and the knowledge base. Both come from the same
content, and the dashboard edits it.

---

## 2. Day-to-day: the dashboard

Sign in at `/admin` with the admin password.

- **Overview** — leads, messages, courses, knowledge topics
- **Leads** — filter and read any conversation
- **Courses** — add, edit, archive or delete a course; upload its documents
- **Knowledge** — company-wide facts and policies
- **Controls** — safe mode, auto-outreach, sending hours, password

### Adding a course
Click **+ Add course**, upload the brochure, press **Read document**. The fields fill in.
**Check them — especially the fees and dates — then Save.** The brochure is attached and
becomes searchable automatically; it is ready when its status shows `indexed`.

### Removing a course
**Archive** is the normal choice: the course stops being offered and stops being searchable, but
everything is kept and it can be brought back later without re-uploading anything.
**Delete permanently** erases the course, its documents and its search data. It cannot be undone.

---

## 3. Settings

Change from **Controls**, effective on the next message, no redeploy:

`BOT_SAFE_MODE` · `AUTO_OUTREACH_ENABLED` · `AUTO_OUTREACH_HOURS` · `AUTO_OUTREACH_TZ` ·
`AUTO_OUTREACH_MAX_PER_RUN` · `AUTO_OUTREACH_DELAY_SECONDS` · `LEAD_SYNC_CONTACT_CUTOFF` ·
`REPLY_DELAY_SECONDS` · `OCR_PAGE_CAP`

**Read once at startup — changing these needs a redeploy:**

| Variable | Notes |
|---|---|
| `CONTENT_SOURCE` | `db` = dashboard content (normal). `files` = the repo's files |
| `RAG_STORE` | `postgres` keeps the search index in the database. Required |
| `DATABASE_URL` | Set by Railway |
| `LEAD_SYNC_INTERVAL_MINUTES` | How often to check for new leads |
| `ENABLE_GOOGLE_SHEETS` | Whether to read leads from the Sheet |
| `VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN` | WhatsApp credentials |
| `RAG_V2_MIN_SCORE` | Answer confidence threshold. Leave alone unless testing |

`BOT_SAFE_MODE=true` means the bot **works out replies but sends nothing**. It is the safe
default and the first thing to turn on if something looks wrong.

---

## 4. Backups

Two things must be backed up, and they are different:

**a) The database** — leads, conversations, content, search index.
```bash
pg_dump "$DATABASE_URL" > timmins-$(date +%F).sql
```
Enable Railway's own Postgres backups as well. **This has not been done yet — do it.**

**b) The content, as readable files** — the escape hatch:
```bash
DATABASE_URL='postgresql://...' python scripts/export_content_from_db.py
```
Writes `content_export/` containing `courses/<slug>/config.yaml` + `overview.md`,
`knowledge/*.md` and `policies.yaml` — the same layout the bot originally read. Commit it, or
keep it somewhere safe. A test asserts this export can be re-imported to identical content, so
it is a genuine recovery path and not just a dump.

### Restoring
```bash
psql "$DATABASE_URL" < timmins-2026-08-13.sql          # whole database
DATABASE_URL='postgresql://...' python scripts/import_content_to_db.py   # content only
```
Then check `/admin` shows the expected number of courses.

**Do a restore drill before you rely on it.** A backup nobody has restored is a hypothesis.

---

## 5. When something is wrong

**Check `/health/ready` first.** It reports the database, queue, safe mode, content source and
course count — and warns explicitly if the content source is the database but no courses are in
it.

| Symptom | Likely cause |
|---|---|
| Bot answers nothing about any course | Course count is 0 — content never imported. Run the importer, or unset `CONTENT_SOURCE` |
| Uploaded document stuck on `failed` | The error is shown under the filename. Scans need `tesseract-ocr` + `poppler-utils` (installed via `nixpacks.toml`) |
| Uploaded document indexed but bot ignores it | `RAG_STORE` is not `postgres`, so chunks went to a local file that is wiped on redeploy |
| No replies at all | `BOT_SAFE_MODE` is on, or WhatsApp credentials expired |
| New leads never contacted | Auto-outreach off, outside sending hours, or leads predate the contact cutoff |
| Dashboard edits do nothing | `CONTENT_SOURCE` is not `db` — edits are refused with a message saying so |

Logs are structured `event=...` lines; searching for `event=` in Railway's log view is the
fastest way to see what the service did.

---

## 6. Before handover — outstanding

- [ ] **Enable Railway Postgres backups** and take one manual `pg_dump`
- [ ] **Do a restore drill** into a scratch database
- [ ] **Change the admin password** from Controls to something long and random
- [ ] **Change the WhatsApp two-step PIN** — it is still `123456`
- [ ] **Regenerate the WhatsApp System User token with no expiry** (the current one expires)
- [ ] **Complete WhatsApp coexistence onboarding** — the number is still on On-Premise, so live
      customer messages do not reach this service. Until this is done the bot cannot go live
- [ ] **Export the content** and store it outside Railway

---

## 7. Development

```bash
python -m venv venv && ./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest -q                          # tests
./venv/bin/python -m unittest discover -s tests -t .    # what CI runs
./venv/bin/ruff check . && ./venv/bin/ruff format .     # lint
uvicorn main:app --reload                               # run locally
```

`/simulate` is a WhatsApp-like chat for testing replies without sending anything.
`/rag-test` exercises the retrieval layer on its own.

Local runs use SQLite and the repo's content files, so nothing local can touch production —
provided `DATABASE_URL` is unset.
