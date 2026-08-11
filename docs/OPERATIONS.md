# Operations runbook

## Deployment checklist

1. Configure all required environment variables in the hosting platform.
2. Set `ENABLE_GOOGLE_SHEETS=true` only after credentials and workbook access are verified.
3. Confirm `WHATSAPP_API_VERSION` against the version enabled for the Meta application.
4. Provision the PostgreSQL database declared in `render.yaml` and verify `DATABASE_URL`.
5. Run `python -m unittest discover -s tests -v` and `ruff check .`.
6. Run outreach and import commands in `--dry-run` mode first.
7. Verify that all expired course configurations have `active: false`.
8. Confirm `/health/ready` reports `database: postgresql`, `status: ready` and no growing queue.
9. Keep `BOT_SAFE_MODE=true` for the first production rollout.

## Database migration

Dry-run the existing local database migration:

```bash
DATABASE_URL='postgresql://...' python scripts/migrate_sqlite_to_postgres.py --dry-run
```

Run the migration during a maintenance window after stopping the old webhook instance:

```bash
DATABASE_URL='postgresql://...' python scripts/migrate_sqlite_to_postgres.py
```

Do not run outreach or accept new webhook traffic during the copy. After migration, deploy with
`DATABASE_URL` and verify lead/message totals through `/health/ready` and `/stats`.

## Safe rollout

1. Deploy to a test WhatsApp number with `BOT_SAFE_MODE=true`.
2. Replay `python scripts/evaluate_responses.py` and the multi-turn tests.
3. Test message ordering by sending two short messages rapidly.
4. Test human takeover and `BOT_PAUSED` before using real leads.
5. Route a small lead cohort first and monitor rejected responses and queue retries.

## Logging

Application logs use key/value events. Customer phone numbers are represented by a stable hash,
and message bodies and API response bodies are not written by the webhook service. Set
`LOG_LEVEL` to `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

## Content updates

Course and knowledge changes are detected automatically. A service restart is not required for
normal Markdown or YAML edits. Validate YAML locally before deployment by running the tests.

## Google Sheets

The service reads each worksheet to locate a lead, then sends all field mutations in a single
batch request. Keep phone numbers normalized and headers consistent. Use `setup_sheet.py
--dry-run` to detect missing structure.

## Incident response

- Disable the Render service or revoke the WhatsApp token if unauthorized messages are sent.
- Rotate the Meta, AWS/Bedrock, fallback LLM-provider, and Google credentials if logs or local files are exposed.
- Preserve logs using hashed subject IDs to correlate failures without exposing phone numbers.
- Reconcile PostgreSQL message history and `inbound_events` against WhatsApp delivery statuses.

## Data handling

Lead data contains personal information. Restrict access to databases, spreadsheets and logs;
define a retention period; and securely remove exports when they are no longer required.
