# Black-Box RAG Quality Audit

Audit date: 28 July 2026  
Evidence: the customer-visible conversations in `images/`  
Scope: answer quality and conversational behavior only

## Scope boundary

This is an outside-in inspection. It evaluates the system exactly as a customer would: the
message sent, the response received, and whether later responses remain consistent with the
conversation.

This audit does **not** validate the contents of the Markdown knowledge files, inspect whether a
particular fee or date matches a brochure, or review architecture and DevOps. Where a likely
cause is mentioned, it is explicitly an inference from observable behavior.

The 23 supplied images include cropped continuations and non-chat screenshots, so they should not
be treated as 23 independent tests. They show several conversation sequences with repeated
evidence of the same underlying defects.

## Executive verdict

The observed assistant is not reliable enough for unsupervised customer conversations.

The central failure is broader than poor wording: the assistant often answers from the wrong
course or wrong topic, loses the user's referent, invents unsupported specifics, and cannot
reliably recover after being corrected. It also allows qualification and fallback behavior to
override explicit requests for a person or to stop.

From the outside, this looks like a RAG system without a dependable grounding contract. Retrieval,
conversation state, and response generation are not acting as one coherent system. A fluent answer
is produced even when the system has weak or contradictory context, and the customer is rarely
shown a safe clarification or abstention.

Overall external rating: **High risk / not production-ready**.

## Observable quality scorecard

| Dimension | Rating | External evidence |
|---|---:|---|
| Course identity | Critical | A Software Testing lead is greeted as Embedded C; Embedded Linux System Internals leads are answered as generic or Embedded C leads. |
| Retrieval relevance | Critical | An August-course request returns a July course; a payment challenge triggers unrelated course and cancellation content. |
| Factual grounding | Critical | The bot confirms installments, supplies a disputed email address, and invents course names not established in the conversation. |
| Multi-turn memory | Critical | “Yes,” “this course,” corrections, and explicit course switches do not reliably bind to the immediately preceding context. |
| Intent handling | Critical | Requests to speak to a person are converted into qualification questions; a course breakdown request is converted into a generic handoff. |
| Error recovery | Critical | Corrections cause topic changes, denial of prior turns, or requests that the customer supply the company's correct information. |
| Answer completeness | Poor | Direct questions are frequently replaced by generic consultant promises rather than answered or clarified. |
| Stop and handoff behavior | Critical | The bot continues after repeated human requests and emits further messages after agreeing to stop. |
| Consistency | Poor | The same conversation alternates among course sales, cancellation, trainer, contact, and qualification topics without a stable focus. |
| Customer experience | Critical | The evidence ends in explicit frustration: “very bad responses,” “I am lost,” and “Honestly it is a mess.” |

## RAG defects versus adjacent defects

From the outside, the failures separate into two groups:

| Group | Findings | Why it matters |
|---|---|---|
| Core RAG/conversation-quality failures | BB-01 through BB-06 | Wrong entity selection, irrelevant retrieval, unsupported claims, stale follow-up context, incomplete answers, and failed correction directly determine answer correctness. |
| Adjacent flow failures | BB-07 through BB-09 | Human handoff, stop/duplicate control, and form parsing are not retrieval problems, but they feed bad context into RAG or send an otherwise avoidable bad response. |

Calling every screenshot a “RAG problem” would hide the fix. The core RAG is unsafe, but some of
the most damaging customer-visible behavior occurs before retrieval or after generation.

## Stop-ship findings

### BB-01 — Course identity is not stable

Severity: **Critical**

Evidence:

- `image copy.png`: the outreach is for Software Testing, but the assistant introduces itself as
  the assistant for Embedded C Programming and GDB Debugging.
- `image copy 3.png` and `image copy 4.png`: the referral visibly names Embedded Linux System
  Internals, but the assistant uses only “our upcoming training program.”
- `image copy 18.png`: an Embedded Linux System Internals form lead is told they are interested in
  Embedded C Programming and GDB Debugging.
- `image copy 16.png` and `image copy 17.png`: the customer explicitly supplies the HRDC number,
  full course name, and later the unique 3–7 August date range, but the assistant remains on the
  previous Embedded C context or hands off without acknowledging the switch.

Impact:

- Every later fee, schedule, curriculum, trainer, and HRDC answer becomes unsafe once the course
  identity is wrong.
- The answer can sound grounded while being grounded to the wrong entity.
- This is the highest-leverage failure because it contaminates an entire conversation.

Outside-in inference:

The system likely has multiple competing sources of course identity—ad/referral data, stored lead
state, keyword detection, and conversation history—without a strict precedence rule. Confidence:
high.

Required behavior:

- Maintain one explicit `active_course` for the conversation.
- A full course name, official identifier, or unique date range must override stale inferred state.
- If two courses remain plausible, ask which course the customer means. Never silently choose.
- Repeat the newly selected course once after a switch so the customer can verify it.

### BB-02 — Retrieval returns topically wrong material

Severity: **Critical**

Evidence:

- `image copy 9.png`: “any embedded course coming up in August?” produces a July Embedded C
  course. The subsequent “this course” fee, venue, duration, and outline answers all inherit the
  wrong selection.
- `image copy 20.png` and `image copy 21.png`: after the customer challenges an installment claim,
  the response jumps to a general Embedded C pitch and mentions cancellation, although neither is
  the requested subject.
- `image copy 21.png`: “other embedded courses” produces offerings such as Embedded Linux
  Development, Firmware Development, and Device Driver Development without establishing that
  these are actual current courses.
- `image copy 10.png` and `image copy 13.png`: a trainer question about Embedded C pulls in a
  software-testing trainer description.

Impact:

- The model receives plausible but irrelevant context and converts it into a confident answer.
- One bad retrieval result cascades through follow-up questions.
- The user cannot tell the difference between a retrieved fact and an invented bridge between
  unrelated facts.

Outside-in inference:

Retrieval likely overweights broad shared words such as “embedded,” “course,” “trainer,” or
“payment,” and underweights hard filters such as course identity, month, and current topic.
Confidence: high.

Required behavior:

- Filter by the confirmed course before ranking course-specific material.
- Treat month, course identifier, and course name as constraints, not soft keywords.
- Retrieve small, labeled passages for the current intent rather than several broad documents.
- Use a minimum relevance threshold. Below it, clarify or abstain.
- Never let shared-policy material replace the current course/topic answer.

### BB-03 — The system generates claims without an observable grounding check

Severity: **Critical**

Evidence:

- `image copy 20.png`: the assistant says installment arrangements can be discussed as though the
  option is established; the customer immediately challenges the claim.
- `image copy 11.png`: the assistant provides an email address that the customer reports is wrong.
- `image copy 10.png`: the assistant asserts trainer specialties and later replaces one unsupported
  specialty with another.
- `image copy 21.png`: the assistant supplies a course catalog that was not established anywhere
  in the conversation.

Impact:

- Commercial claims, contact information, and catalog availability are high-trust facts.
- A single invented fact damages trust in all otherwise correct answers.
- The current conversational style makes unsupported claims sound authoritative.

Outside-in inference:

The generation stage appears allowed to fill gaps instead of being restricted to supported claims.
If a response validator exists, it is not detecting all important claim types. Confidence: high.

Required behavior:

- Every externally verifiable claim must be either supported by retrieved evidence or omitted.
- Validate course, fee, date, venue, duration, contact, trainer, policy, and availability claims
  before sending.
- If support is absent, use a precise abstention: “I don't have a confirmed answer for that.”
- Do not use “a consultant will contact you” as a substitute for a grounding decision.

### BB-04 — Follow-up resolution uses stale context

Severity: **Critical**

Evidence:

- `image copy 10.png`: after asking whether the user wants trainer information, the assistant
  interprets “Yes” as a request for course dates and fees instead.
- `image copy 9.png`: “How much for this course?” binds to the bot's incorrect July course rather
  than the requested August set.
- `image copy 21.png`: “You don't know what [I] ask about it?” and “I told [you] about payment
  installation” do not reliably restore the installment topic.
- `image copy 6.png`: “OK” restarts the welcome instead of acknowledging the preceding handoff.

Impact:

- Short WhatsApp replies are common; mishandling them makes the assistant unusable in natural
  conversation.
- Once a referent is lost, each subsequent response drifts further.

Outside-in inference:

The assistant may be scanning a wide history window for keywords rather than resolving the latest
question/answer pair and maintaining structured topic state. Confidence: medium-high.

Required behavior:

- Resolve “yes,” “no,” “this,” “that,” and “it” against the immediately preceding assistant turn.
- Track `active_course`, `active_topic`, `last_question_asked`, and `pending_action` separately.
- If a referent is ambiguous, ask one short clarification.
- Do not restart a welcome after a phatic acknowledgement.

### BB-05 — Multi-intent questions are not decomposed

Severity: **High**

Evidence:

- `image copy 5.png`: “Course breakdown and fees” receives only a generic consultant handoff.
- `image copy 18.png`: “fees and curriculum and requirements” begins a partial response, with no
  visible assurance that all requested parts are handled.
- `image copy 9.png`: the user has to ask fee, venue, duration, outline, and day-one topics as
  separate turns, and the answers become progressively less reliable.

Impact:

- The assistant may answer the first recognized intent and discard the rest.
- Missing parts force extra turns, increasing the chance of context drift.

Required behavior:

- Split a message into atomic requested fields before retrieval.
- Retrieve and verify evidence for every field.
- Answer each supported field and explicitly identify only the unsupported fields.
- Run an answer-coverage check before sending.

### BB-06 — Corrections make the conversation worse

Severity: **Critical**

Evidence:

- `image copy 10.png`: after the customer identifies the software-testing trainer error, the bot
  replaces it with another unsupported trainer characterization and then ignores “Yes.”
- `image copy 11.png`: after “wrong email,” the assistant asks the customer to provide the correct
  Timmins email, reversing responsibility.
- `image copy 11.png`: the bot later claims it did not ask about the office number and says this is
  the start of the conversation, contradicting the visible transcript.
- `image copy 12.png`: “I am lost” receives another generic course question rather than a concise
  repair summary.
- `image copy 20.png` and `image copy 21.png`: challenging an installment claim causes unrelated
  content instead of a correction tied to the disputed claim.

Impact:

- Error recovery is where user trust is either restored or lost permanently.
- Contradicting visible history makes the bot appear deceptive, not merely mistaken.

Required behavior:

- Treat correction signals as a dedicated intent with the highest priority.
- Identify the exact disputed claim, retract it, and do not repeat it as fact.
- Restate the last confirmed course and topic in one sentence.
- Ask at most one clarifying question if the disputed detail cannot be isolated.
- Never ask a customer to supply the company's authoritative contact data.

### BB-07 — Human requests are overridden by qualification

Severity: **Critical**

Evidence:

- `image copy 7.png`, `image copy 14.png`, and `image copy 15.png`: “I want to speak to someone”
  and repeated variants trigger experience, tools, motivation, and learning-goal questions.
- The customer explicitly says the assistant ignored the request, but qualification continues.

Impact:

- This violates explicit user intent.
- It collects unnecessary information after the user has requested a human.
- It creates the impression that escalation is fake.

Required behavior:

- A human request must immediately cancel or pause every automated questionnaire.
- Send one acknowledgement, create one handoff, and suppress further automation until resolution.
- Do not require qualification as a condition for speaking to a person.

This is primarily an orchestration failure rather than retrieval, but it directly determines what
the user experiences as the “RAG response.”

### BB-08 — Stop intent and duplicate response control are unreliable

Severity: **Critical**

Evidence:

- `image copy 22.png`: after “Let's stop it,” the assistant confirms the end of the chat and then
  sends two more fallback/handoff messages.
- `image copy 15.png`: two deleted-message events are followed by two identical welcome messages.
- `image copy 6.png`: a normal acknowledgement causes a full welcome to be sent again.

Impact:

- The bot can continue messaging after consent has been withdrawn.
- Duplicate messages amplify every content failure and can resemble spam.

Required behavior:

- Stop must be a persistent conversation state checked before any retrieval or generation.
- One inbound message must produce at most one automated outbound response.
- Status, deletion, reaction, and retry events must never be treated as new customer text.
- Handoff and fallback paths must be mutually exclusive.

This is not a retrieval defect, but it is a production-blocking part of the observed assistant.

### BB-09 — Lead-form values are interpreted literally instead of semantically

Severity: **High**

Evidence:

- `image copy 18.png`: form values such as job title “No” and company “No” become the sentence
  “you're a No at No.”
- The same lead is assigned the wrong course despite the visible referral naming Embedded Linux
  System Internals.

Impact:

- Bad structured context is injected before RAG is even called.
- The generated response faithfully verbalizes invalid metadata and looks absurd.

Required behavior:

- Normalize null-like form values (`no`, `none`, `n/a`, blank) to missing.
- Never verbalize a field unless it passes type and plausibility checks.
- Treat explicit referral identity as stronger than generic profile fields.

## Failure-chain analysis

The screenshots show a recurring chain:

1. The wrong course or topic is selected.
2. Retrieval supplies material for that wrong selection.
3. Generation turns the material into a fluent, confident answer.
4. The user follows up with “this,” “yes,” or a correction.
5. Conversation memory binds the follow-up to stale or unrelated context.
6. The repair path introduces another topic or a generic consultant promise.
7. Qualification, welcome, or fallback logic sends additional unwanted messages.

Improving only the prompt or only the retriever will not break this chain. Course identity,
retrieval constraints, claim grounding, follow-up resolution, and send control all need explicit
contracts.

## Recommended remediation order

### Phase 0 — Contain customer harm

1. Put high-risk factual intents into verified-only mode: price, schedule, venue, contact details,
   trainer identity/background, HRDC, payment, and cancellation.
2. If course identity is uncertain, ask for the course instead of generating.
3. Make human and stop intents override all other flows.
4. Enforce exactly one response per inbound customer message.
5. Suppress free-form generation when retrieval confidence is below threshold.

### Phase 1 — Repair the RAG contract

1. Represent the conversation with explicit course and topic state.
2. Decompose multi-part questions into atomic intents.
3. Retrieve only passages compatible with the confirmed course and requested intent.
4. Require passage-level support for every factual claim.
5. Validate the complete answer for unsupported claims, cross-course leakage, and missing requested
   fields.
6. Prefer a clarification or precise abstention to a plausible guess.

### Phase 2 — Make conversation repair deterministic

1. Add dedicated handling for correction, confusion, disputed facts, human requests, and stop.
2. Bind short replies to the immediately preceding question, not any older matching keyword.
3. On correction, retract the exact claim and preserve the last confirmed course/topic.
4. Prevent welcomes and qualification prompts from reappearing in an established conversation.

### Phase 3 — Establish an external evaluation gate

Build tests from customer-visible behavior, not from whether the system repeats its own stored
content. The screenshot conversations should become replayable transcript tests with paraphrases.

Minimum test groups:

- correct course from referral, name, identifier, and date;
- course switch during an existing conversation;
- August vs July filtering;
- “this course,” “yes,” “no,” and “it” after single and multiple choices;
- two- and three-part questions;
- unsupported payment, trainer, contact, and catalog claims;
- correction after a wrong fact;
- explicit human request during every qualification step;
- stop followed by further inbound/status/retry events;
- null-like lead-form values;
- duplicate delivery of the same inbound event.

## Acceptance criteria

Do not expose the assistant to customers without supervision until it meets all of the following on
a held-out black-box transcript set:

- 100% correct course selection for explicit course names and identifiers.
- 100% clarification when course identity is ambiguous.
- 0 cross-course factual claims.
- 0 unsupported high-risk claims.
- At least 95% requested-field coverage on multi-part questions.
- At least 95% correct resolution of immediate short follow-ups.
- 100% correct handling of human and stop requests.
- At most one outbound automated response per inbound message.
- 100% correction responses that retract the disputed claim without introducing a new unsupported
  one.

## Final assessment

Externally, the assistant's main weakness is not lack of knowledge; it is lack of control over
which knowledge applies, when it is safe to state a claim, and how conversational state changes
after each turn.

The priority should therefore be:

**course identity → intent decomposition → constrained retrieval → claim validation → conversation
repair → single-send enforcement**.

Until those controls are measurable through black-box transcript tests, changing models, adding
more documents, or rewriting the system prompt is unlikely to make the assistant reliably correct.
