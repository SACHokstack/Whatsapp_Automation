# Architecture

## Production message flow

1. FastAPI validates and parses the Meta webhook.
2. Every message/status is inserted into the durable `inbound_events` table before HTTP 200.
3. A worker claims events with a lease. Failed events are retried with exponential backoff.
4. Messages are serialized per phone number so conversation state cannot race.
5. The conversation controller selects one route:
   - `exact`: structured course or policy facts;
   - `rag`: descriptive curriculum content only;
   - `context`: correction or conversational-reference recovery;
   - `clarify`: insufficient course/intent confidence.
6. The response guard rejects internal prompt language, unapproved URLs, cross-course names,
   invented fees, overly long replies, and unsupported installment claims.
7. WhatsApp delivery is attempted and accepted replies advance conversation state.

## Data ownership

Production state lives in PostgreSQL when `DATABASE_URL` is configured. SQLite remains a local
development/test backend. Google Sheets is an optional CRM projection, not conversation memory.

- `courses/*/config.yaml`: exact course names, dates, venues, fees and deadlines.
- `knowledge/policies.yaml`: exact payment, cancellation, certification and contact policies.
- `courses/*/overview.md`: descriptive curriculum content available to RAG.
- `leads`: selected course, active topic, qualification and takeover/pause state.
- `messages`: ordered inbound/outbound conversation history.
- `inbound_events`: durable, idempotent Meta webhook inbox.

## RAG boundary

RAG is not authoritative for course selection, fees, schedules, HRDC numbers, contact details,
payment policy, cancellation, certification or bot state. Those answers come from structured
records. RAG is used only for course explanations such as curriculum, prerequisites and learning
outcomes, and its output passes through the response guard before delivery.

`BOT_SAFE_MODE=true` blocks unclassified generative answers and asks a focused clarification.
Keep it enabled until the production conversation evaluation set is consistently passing.

## Conversation state

The controller persists selected course, last explicit intent, qualification step, human handoff
and `BOT_PAUSED`. An explicit course name, HRDC registration number or unique date range can
replace an incorrect stored course. A customer-requested human handoff and `BOT_PAUSED` suppress
automation; an internal hot-lead flag does not.

## Remaining delivery guarantee

The inbound side is durable and retryable. WhatsApp sending is still a synchronous external side
effect because the Meta send API does not provide an application idempotency key. A future
transactional outbound outbox can improve retries, but cannot fully eliminate the narrow crash
window after Meta accepts a send and before local state is committed.
