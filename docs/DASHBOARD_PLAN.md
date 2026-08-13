# Admin dashboard — design plan

> **Status: delivered.** P0–P5 all shipped and are running in production. This is kept as the
> design record: what was decided, why, and what the risks were. It is written in future tense
> because it was written before the build — read it as intent, not as outstanding work.
>
> `docs/HANDOVER.md` is the operational guide. This document is the reasoning behind it.

## How the build differed from this plan

Recorded honestly, because the deviations are the interesting part.

**Ingestion does not run on the durable queue.** That queue is shaped around WhatsApp inbound
events (sender, message payload) and its worker dispatches to message handling. Rather than bend
it, the `documents` table doubles as the queue: a row is uploaded `pending`, a dedicated worker
claims it into `extracting` with a conditional UPDATE as the lock, and the dashboard polls the
same column the worker writes. One store instead of two, and status is visible by construction.

**`sync_course` was added to both vector stores, not just Postgres**, so the guarantee holds in
local SQLite too and can be tested without a database. Its signature is
`sync_course(course_id, chunks, embeddings)` — no `corpus_version`, which only means something
for a whole-corpus build.

**Page markup lives in `web/pages.py`, not inline in `main.py`.** `main.py` had grown past 3,600
lines, most of it HTML/CSS/JS strings. Extracting them cut it by ~900 lines with no behaviour
change, and gave the later dashboard work somewhere to go.

**RAG generation settings and the retrieval threshold were not made live.** `RAG_V2_MIN_SCORE` is
read at import, and the service is cached on a version key that would not notice the change — a
switch that appears to work and does nothing is worse than no switch. They are listed read-only
as requiring a redeploy.

**`CONTENT_SOURCE` still defaults to `files`.** The plan intended `db` as the default at P5. In
practice a deployment pointed at `db` before the import ran produced an empty catalogue that RAG
kept masking — this happened in production. Changing the default would make that the failure mode
for every fresh deploy, so the default stayed `files` and `/health/ready` now reports the content
source and course count and warns when the catalogue is empty.

**Added beyond the plan:**
- **Create a course from its brochure** (`services/course_extract.py`) — upload the PDF, the form
  is filled in for review. Requested after the plan was written.
- **`scripts/export_content_from_db.py`** — writes the database back out as the original files,
  with a round-trip test proving it can be re-imported to identical content. This is what makes
  the move to database content reversible.
- **Test isolation fix** — CI runs `unittest discover`, which never loaded `conftest.py`, so the
  suite ran against the real database and index and had been red on every push while pytest was
  green. The plan's "suite stays green after every phase" was only true under pytest until this
  was fixed.

**Not done:** R1's "N added, M deduped" is not surfaced in the UI. Chunk counts are shown, but a
chunk silently dropped by content-hash dedup is still invisible.

**R8 resolved:** the reader cache key is read from the database on every call, so a
`content_version` bump in one process is picked up by another. Multi-instance is safe; verified.

The suite was ~261 tests when this was written. It is now 344.

---

## Original plan (as approved)

# No-code admin dashboard for the seasonal course chatbot

## Context

The bot is going to a non-technical client who must run each season himself. Today, adding a
course means editing `courses/<slug>/config.yaml` + `overview.md`, dropping in docs, running
ingestion, and **git commit + redeploy** — impossible for the client. The content splits into two
kinds he named: **course details** (seasonal, added and purged when the intake ends) and **company
data** (permanent). The goal is a click-only web dashboard, in the same app, to add/edit/archive
courses, upload their docs (which get OCR'd → embedded → searchable automatically), edit company
knowledge, view leads, and flip runtime controls — with all content in Postgres, no files, no
deploys.

Locked product decisions (from the user):
- **Docs = mix** of digital and scanned → extract text first (`pdfplumber`/`python-docx`, already
  present), fall back to **Tesseract OCR** per page when there's no text layer.
- **Auth = single shared admin password**, changeable from the UI, via a cookie session. This also
  closes the currently-open PII endpoints (`/lead`, `/conversation`, `/stats`).
- **Course removal = ARCHIVE by default** (`active=false`, keep data, reversible), with a separate
  explicit **Delete permanently** that purges the course row + its `rag_chunks`.

The unlock is **P0: move content from files to Postgres behind the existing loader signatures.**
Until that lands, no dashboard button can add a course. Everything else builds on it.

## Key finding that de-risks the whole thing

**RAG search is self-refreshing.** `HybridRetriever.retrieve()` calls
`store.iter_candidates()` live per query ([rag_v2/retriever.py:205](rag_v2/retriever.py#L205)) and
recomputes BM25 over that fresh candidate set ([retriever.py:234](rag_v2/retriever.py#L234)) — there
is no persisted in-memory lexical index. So chunks written into `rag_chunks` at runtime are
searchable on the **next question, with no restart or singleton invalidation.** The only caches that
need a nudge after a content write are the file-fingerprinted `@lru_cache`s in the three readers,
handled by a single `content_version` bump (below).

**The one architectural rule (dual-writer trap):** the boot path rebuilds from **files** and prunes
`rag_chunks` **globally** ([runtime.py:286-288](rag_v2/runtime.py#L286), global prune at
[pg_store.py:152](rag_v2/pg_store.py#L152)); a force-rebuild or SQLite mode would wipe
dashboard-ingested chunks. **Rule: in DB mode, Postgres is the single source and boot only ever
reuses** (`RAG_STORE=postgres`, never `RAG_FORCE_REBUILD`; in DB mode `_documents()` stops emitting
file-driven course/overview/knowledge docs so the two writers can't fight).

## Data model (new tables)

Added idempotently in **both** [services/postgres_store.py](services/postgres_store.py)
`_initialize_schema()` (line 77) and [services/sqlite_store.py](services/sqlite_store.py) `init_db()`
(line 40), accessors re-exported through [services/persistence.py](services/persistence.py). Course
config is **hybrid**: routed/queried fields as columns, variable maps as JSON, `overview` as a
Markdown TEXT column (so `structured_facts.py`'s regex parsers stay byte-for-byte identical). `*_json`
is JSONB on Postgres / TEXT-json on SQLite; loaders `json.loads` on read.

- **`courses`** — `slug PK, name, active, dates, venue, fees_json, hrdc_deadline, payment_deadline,
  hot_budget_threshold, keywords_json, overview TEXT, outreach_json, archived_at, created_at,
  updated_at`. Mirrors the current `config.yaml` fields; `slug` = old directory name.
- **`knowledge`** — `topic PK, body TEXT, updated_at` (was `knowledge/*.md`, keyed by filename stem).
- **`policies`** — single row `data_json` (was `knowledge/policies.yaml`: payment, cancellation.tiers,
  certification, company{...}).
- **`documents`** — `id, course_slug FK, filename, content_type, raw_bytes BYTEA, extracted_text,
  extraction_method(text|ocr|mixed), ingest_status(pending|extracting|embedding|indexed|failed),
  ingest_error, chunk_count, uploaded_at, updated_at`. Store the original bytes in Postgres (Railway
  FS is ephemeral) so the client can re-download/re-OCR/replace a doc.
- **`app_settings`** — `key PK, value, updated_at`. Reserved keys: `content_version` (monotone int,
  the reader-cache refresh signal), migrated runtime toggles, `admin_password_hash`.
- **`admin_users`** — `id, password_hash, updated_at` (single shared password; `hashlib.pbkdf2_hmac`,
  no new dep).

## Reader swap (behind existing signatures)

Keep every public signature so the ~10 caller modules and the tests stay untouched. Each loader gets
a DB branch gated by a `CONTENT_SOURCE` setting (`files` default | `db`):
- [services/course_loader.py](services/course_loader.py): `_load_courses_cached` (line 70) hydrates
  the **same** `CourseConfig` dataclass from a `SELECT * FROM courses`; `course_content_version()`
  (line 59) returns `("db", content_version_int)` in DB mode; `refresh_courses()` stays the cache-clear
  entry point.
- [services/knowledge_base.py](services/knowledge_base.py): same for `_load_knowledge_base_cached`
  (line 153) / `knowledge_content_version()` (line 143) / `refresh_knowledge_base()`.
- [services/structured_facts.py](services/structured_facts.py): `load_policies()` (line 22) reads the
  `policies` row. **All overview regex parsers unchanged** — they operate on `course.overview` which
  stays raw Markdown.

**One-time importer** `scripts/import_content_to_db.py`: walks the 5 active + 3 inactive
`courses/<slug>/` dirs, `knowledge/*.md`, `policies.yaml` (reusing the existing read logic) and UPSERTs
into the new tables. Idempotent. Ship with `CONTENT_SOURCE=files` (zero behaviour change), run the
importer, flip to `db`; files stay on disk as fallback until P5 retires them.

## Course-scoped ingestion (reuse the pipeline)

- **New `PostgresVectorStore.sync_course(slug, chunks, embeddings, corpus_version)`** in
  [rag_v2/pg_store.py](rag_v2/pg_store.py) — the one genuinely new bit, because the existing `sync()`
  (line 133) prunes globally. Scoped prune: `DELETE FROM rag_chunks WHERE course_id=%s AND NOT
  (chunk_id = ANY(%s))`, then the same per-chunk upsert loop. Catalog/knowledge chunks
  (`course_id IS NULL`) untouched.
- **Extractor** `services/doc_extract.py::extract_text()` — `pdfplumber` per page; empty page → OCR
  via `pytesseract` on a `pdf2image`/Pillow raster; `python-docx` for Word. Sets `extraction_method`.
- **Ingest** `services/course_ingest.py` — build `CorpusDocument(course_id=slug, topic="course_kb")`,
  reuse `chunk_document` ([rag_v2/chunking.py](rag_v2/chunking.py)) and `configured_embedder()`
  (warm in-process FastEmbed bge-small), then `sync_course`. Runs on the **durable-queue worker**
  (reuse [services/durable_queue.py](services/durable_queue.py) lease/retry + the
  `_event_worker_loop` pattern), not the request thread; the dashboard polls `documents.ingest_status`.
  Use a longer lease + per-page streaming + a page cap for OCR (R5).
- **Archive** → `active=false` + `sync_course(slug, chunks=[])` to prune that course's chunks from
  retrieval while keeping `courses`/`documents` rows (re-ingestable on reactivate). **Delete** →
  `DELETE rag_chunks WHERE course_id=slug`, then `documents`, then the `courses` row, one transaction.
- **Deps:** `requirements.txt` += `pytesseract, pdf2image, Pillow, python-multipart`. New
  `nixpacks.toml` with `aptPkgs = ["tesseract-ocr","poppler-utils"]`.

## Dashboard + auth (same app, inline-HTML pattern)

Cookie session, no new framework — mirrors the token check at
[main.py:2600](main.py#L2600). Password hashed (`pbkdf2_hmac` + `secrets.compare_digest`), session =
HMAC-signed stateless cookie (`HttpOnly; Secure; SameSite=Lax`, no shared store — single worker,
documented). A `require_auth(request)` helper called inline at the top of every dashboard route/JSON
endpoint (routes are decorated directly, no `Depends`), **including the now-guarded `/lead/{phone}`,
`/conversation/{phone}`, `/stats`.**

Pages as inline HTML string constants served via `HTMLResponse`, exactly like `_WA_SIMULATOR_HTML`
([main.py:385](main.py#L385)), fetching JSON client-side: `/admin/login`, `/admin`,
`/admin/courses` (list, add/edit/archive/delete, per-course upload + ingest status),
`/admin/knowledge` (topics + policies editor), `/admin/leads` (reuse `list_leads`,
`get_conversation_history`), `/admin/controls`. Every content-mutating endpoint ends by bumping
`app_settings.content_version` and calling the loader `refresh_*()` helper.

## Runtime settings layer

`services/settings.py::get_setting(key, default)` → `app_settings` then `os.getenv` then default.
Migrate the per-request/per-call toggles (`BOT_SAFE_MODE`, `AUTO_OUTREACH_*`,
`LEAD_SYNC_CONTACT_CUTOFF`, RAG generation/threshold) to flip live. `LEAD_SYNC_INTERVAL_MINUTES`
(startup thread) and `VERIFY_TOKEN`/`ENABLE_GOOGLE_SHEETS` (import-time) stay env, shown read-only on
the controls page with a "requires restart/redeploy" note.

## Phasing (files touched)

- **P0 — Foundations (invisible unlock + closes PII):** new tables in both stores + persistence
  re-export; `services/settings.py`; `scripts/import_content_to_db.py`; reader DB branches in the
  three loaders; auth helpers + `/admin/login` + guard the 3 PII endpoints in `main.py`;
  `python-multipart`. Enforce the single-source rule in `rag_v2/runtime.py` `_documents()` under DB mode.
- **P1 — Read-only dashboard:** `_ADMIN_*_HTML` + `/admin`, `/admin/leads`, read-only `/admin/api/*`.
- **P2 — Course CRUD + ingestion (headline):** `pg_store.sync_course`; `services/doc_extract.py`;
  `services/course_ingest.py`; ingest job type in `durable_queue.py` + worker branch; course pages +
  `/admin/api/courses*` + upload/status; `pytesseract/pdf2image/Pillow`; `nixpacks.toml`.
- **P3 — Company knowledge CRUD:** `/admin/knowledge`, `/admin/api/knowledge`, `/admin/api/policies`.
- **P4 — Runtime controls:** wire `get_setting` into `main.py`/`auto_outreach.py`/`lead_sync.py`/
  `runtime.py`; `/admin/controls` + `/admin/api/settings` + `/admin/api/password`.
- **P5 — Handover:** retire `courses/`+`knowledge/` as source of truth (`CONTENT_SOURCE=db` default,
  keep as export); backup runbook (`pg_dump` of new tables + `rag_chunks`); rotate weak PIN/tokens;
  document the restart-required settings.

## Verification

Global: the ~261-test `pytest` suite stays green after every phase (signatures preserved,
`CONTENT_SOURCE=files` default makes P0 a no-op for existing tests). Add a fixture running the loader
tests under both `files` and `db` sources.

- **P0:** import into a scratch DB; assert `load_courses()`/`load_policies()`/`load_knowledge_base()`
  are field-for-field identical files-vs-db for all 8 courses, and that
  `structured_facts.exact_answer` returns identical strings under both (guards the overview parsers).
  `curl /lead/{phone}` without cookie → 401; with cookie → 200.
- **P1:** log in, load each read-only page; JSON endpoints reject a missing cookie; `/simulate`
  unaffected.
- **P2:** scripted **upload → extract → embed → ask** e2e against a throwaway Postgres: create a
  course, upload one digital + one scanned PDF, poll `ingest_status` to `indexed`, then `/simulate` a
  question answerable only from the uploaded doc → grounded answer, **no restart** (proves live
  visibility). Archive → that answer disappears; reactivate+re-ingest → restored. Delete →
  `SELECT count(*) FROM rag_chunks WHERE course_id=slug` = 0.
- **P3:** edit a knowledge topic in the UI; `/simulate` a company question reflects it immediately.
- **P4:** toggle `BOT_SAFE_MODE` in the UI, send a webhook, behaviour flips with no restart.
- **P5:** restore-from-backup drill; full suite green with `CONTENT_SOURCE=db`.

## Risks to hold in mind

- **R2/R3 dual-writer (biggest):** boot `build_index` global-prunes from files; must run Postgres
  reuse mode and make `_documents()` single-source in DB mode, or the two ingestion paths clobber each
  other.
- **R1 chunk dedup:** content-hash dedup ([ingestion.py:31](rag_v2/ingestion.py#L31)) may silently
  drop an uploaded chunk that overlaps existing text — surface "N added, M deduped" in the UI.
- **R5 OCR cost:** `pdf2image` + Tesseract are CPU/memory heavy; page cap + longer lease + per-page
  streaming on a small Railway instance.
- **R8 multi-instance cache:** `content_version` bump is process-local `lru_cache`; poll
  `app_settings.content_version` per request as the cache key, or stay single-instance.
