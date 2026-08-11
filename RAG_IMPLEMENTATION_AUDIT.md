# RAG Implementation Audit

Audit date: 28 July 2026

Evidence used:
- `RAG_BLACK_BOX_AUDIT.md` for screenshot-derived customer failures.
- Current implementation in `main.py`, `services/knowledge_base.py`, `services/ai_reply.py`, `services/conversation_controller.py`, `services/structured_facts.py`, and `services/response_guard.py`.
- Current offline replay: `./venv/bin/python scripts/evaluate_responses.py` passed 43/43 cases.

Scope: RAG, answer grounding, course/topic selection, and response-quality controls only. Architecture, hosting, DevOps, and production hardening are intentionally out of scope.

## Executive verdict

The visible screenshot failures have been patched heavily, and the current offline replay now passes the screenshot-derived regression cases. That is good progress, but it does not mean the RAG is trustworthy.

The current system is not a true grounded RAG pipeline. It is a hybrid of exact-answer rules, deterministic one-off repairs, broad keyword retrieval, and a live LLM fallback. The high-risk screenshot cases are increasingly handled by bypassing generation, while the remaining generative path still lacks passage-level retrieval, evidence scoring, claim validation, and answer coverage checks.

In short: the bot is safer than the screenshots show, but the RAG core is still structurally weak.

Current implementation risk rating: **High for live unsupervised use**.

## Important current fact

`scripts/evaluate_responses.py` currently passes 43/43 in offline fallback mode. That suite covers many screenshot failures: wrong course switching, August catalog filtering, installment correction, human handoff, stop handling, contact correction, trainer follow-up, and form-fill null handling.

However, that command mostly exercises deterministic fallback behavior, not the live Groq path. Passing it proves the patched rules work for known examples. It does not prove that the live RAG path is grounded for new wording, new multi-intent questions, or unseen contradictions.

## How the current RAG actually works

The current answer path is roughly:

1. `main._resolve_course()` tries explicit course detection, stored lead course, then keyword course detection.
2. `main._faq_reply()` calls `conversation_controller.decide_reply()`.
3. Many high-risk intents route to `structured_facts.exact_answer()`.
4. Some cases route to `ai_reply._deterministic_reply()`.
5. Remaining RAG cases call `ai_reply.ai_reply()`.
6. `ai_reply.ai_reply()` builds context with `knowledge_base.build_rag_context()`, sends it to Groq, sanitizes the answer, then `response_guard.validate_reply()` checks a small set of rules.

That means the system often avoids RAG for known-danger cases, but when RAG is used, the grounding controls are still thin.

## Findings

### IA-01 — Retrieval is keyword document selection, not passage-level RAG

Severity: **Critical**

`services/knowledge_base.py` ranks shared knowledge files by:
- exact keyword substring matches;
- token overlap with the topic name;
- token overlap with the whole document.

It returns whole Markdown documents, not small chunks with metadata, citations, or confidence. There is also no minimum relevance threshold. Any positive score can be treated as usable context.

Why this causes bad answers:
- Broad words like `embedded`, `course`, `trainer`, `payment`, `requirements`, or `contact` can pull a plausible but wrong document.
- The LLM receives context that may be topically adjacent but not actually answering the user.
- The generator is asked to be confident when the answer is in the context, but the retriever does not prove the answer is in the right passage.

Required fix:
- Split course and shared knowledge into labeled passages.
- Attach metadata: `course_slug`, `topic`, `fact_type`, `date_range`, `source_file`, and `source_section`.
- Filter before ranking using hard constraints such as active course, month, HRDC ID, course name, and requested topic.
- Add a relevance threshold. Below threshold, clarify or abstain.

### IA-02 — Course grounding improved, but topic state is still mostly raw history

Severity: **High**

Course detection has strong improvements: explicit course names, HRDC registration numbers, unique dates, referrals, and stored lead courses are handled. This directly addresses several screenshot failures.

The weaker part is topic/follow-up grounding. The live prompt receives recent conversation as raw text from the last 16 messages. Deterministic code special-cases some follow-ups like `yes` after trainer, but there is no general structured state for:
- `active_topic`;
- `last_question_asked`;
- `last_offered_options`;
- `pending_action`;
- `last_supported_claims`;
- `last_disputed_claim`.

Why this causes bad answers:
- WhatsApp users often say `yes`, `this course`, `that one`, `what about fees`, or `you are wrong`.
- Raw history makes the model infer the referent instead of the app resolving it deterministically.
- The current fixes cover known examples but not the full class of short follow-ups.

Required fix:
- Maintain structured conversation state separate from transcript text.
- Resolve short replies before retrieval/generation.
- If the previous assistant turn listed multiple courses, require an explicit choice.
- On correction, bind the correction to the exact previous claim and retract only that claim.

### IA-03 — The guard is too shallow to prove factual grounding

Severity: **Critical**

`services/response_guard.py` currently checks:
- empty response;
- word count over 120;
- internal phrases like `knowledge base`;
- unapproved URLs;
- exact cross-course name leakage;
- invented RM fee values for the selected course;
- obvious unsupported installment confirmation.

It does not verify most high-risk claims:
- dates;
- venue;
- duration;
- trainer profile;
- HRDC number or deadline;
- payment deadline;
- certificate inclusions;
- company contact email as an email claim;
- catalog availability;
- prerequisite claims;
- whether every requested field was answered.

Why this causes bad answers:
- A live model can still invent a date, venue, trainer detail, HRDC detail, or inclusion and pass validation.
- Cross-course leakage is only caught if the exact other course name appears, not if the wrong trainer, venue, month, or curriculum leaks.
- The guard rejects some bad answers, but it does not certify good ones.

Required fix:
- Extract high-risk claims from the proposed answer.
- Compare each claim against structured facts or retrieved passage text.
- Reject unsupported dates, fees, venues, course names, trainer details, HRDC details, payment policies, contact details, and catalog claims.
- Add a requested-field coverage check for multi-part questions.

### IA-04 — Exact-answer routing is doing the real safety work, not RAG

Severity: **High**

`conversation_controller.decide_reply()` routes many important intents to exact answers: payment, cancellation, certification, contact, trainer, fees, schedule, venue, duration, and HRDC.

That is the right direction for high-risk facts. But it means the system is becoming safer by bypassing the RAG rather than by improving RAG grounding.

Why this matters:
- New intents that do not match a rule can still fall into the live model path.
- The codebase accumulates screenshot-specific patches, which can pass regression tests while leaving unseen variants unsafe.
- The boundary between exact facts, deterministic fallback, and RAG is not yet a formal contract.

Required fix:
- Keep exact answers for commercial facts.
- Define a clear rule: high-risk factual answers must come from structured facts or verified passages, never free generation.
- Use RAG mainly for low-risk explanatory content after course and topic are confirmed.

### IA-05 — Shared policy retrieval can contaminate course-specific answers

Severity: **High**

`build_rag_context()` appends course context and then the top two shared knowledge documents for the user message. Shared docs are selected independently of course identity and can include broad policy text.

Why this causes bad answers:
- A payment, cancellation, trainer, or company doc can dominate a course answer.
- The model can bridge a course-specific question with unrelated shared-policy material.
- This matches the black-box pattern where installment challenges drifted into cancellation or generic course pitches.

Required fix:
- Treat shared policy docs as separate answer sources, not generic extra context.
- Only include shared policy passages when the detected intent requires them.
- Keep course-specific context and shared-policy context in separate labeled fields.
- Do not let a shared policy passage answer a course-specific curriculum, trainer, fee, date, or venue question.

### IA-06 — The live LLM prompt asks for discipline but cannot enforce it

Severity: **High**

The system prompt contains many good rules: answer every part, do not invent, do not mention the knowledge base, resolve short replies, avoid unsupported trainer claims, and avoid repeating wrong contact details.

Those rules are necessary, but not sufficient. The prompt is doing work that should be done by program logic and validation.

Why this causes bad answers:
- If retrieval gives irrelevant context, the prompt cannot reliably know that context is wrong.
- If a detail is absent, the model may still produce a plausible bridge.
- If multiple user intents are present, there is no deterministic coverage checklist.

Required fix:
- Move grounding decisions out of the prompt.
- Build an intent/field plan before generation.
- Pass only verified passages for each field.
- Validate the generated answer against the plan before sending.

### IA-07 — Multi-intent handling is partial

Severity: **Medium-High**

The deterministic fallback can combine fees, schedule, duration, venue, HRDC, and some curriculum extraction. `structured_facts.exact_answer()` also appends schedule, venue, and duration to fee answers if those words appear.

But this is not a general decomposition system. It does not produce a field-level plan like:
- requested fee;
- requested curriculum;
- requested prerequisites;
- requested trainer;
- requested HRDC;
- requested registration steps.

Why this causes bad answers:
- The answer may satisfy the first detected intent and miss later ones.
- Tests can pass if expected substrings appear, while some requested fields are silently omitted.
- Live generation can compress or skip parts without being caught.

Required fix:
- Parse every user message into requested fields.
- Retrieve/resolve each field separately.
- Answer each field or explicitly say it is unconfirmed.
- Validate that each requested field is represented in the final reply.

### IA-08 — The evaluation suite is useful but too narrow

Severity: **Medium**

The current replay suite is valuable and should be kept. It proves known screenshot failures do not regress in offline deterministic mode.

Limitations:
- It does not run the live Groq path by default.
- It mainly checks substrings, not claim support.
- It does not fuzz paraphrases.
- It does not verify every claim in the answer.
- It does not replay full webhook event edge cases such as retries, statuses, deleted messages, and concurrent inbound events.

Required fix:
- Add a separate `grounding_eval` that records requested fields, expected source facts, forbidden facts, and whether each answer claim is supported.
- Run deterministic and live modes separately.
- Add paraphrase sets for each screenshot-derived case.
- Add negative tests where no answer should be generated.

## Highest-risk live RAG path

The most dangerous current path is:

1. A message does not match exact or deterministic high-risk routing.
2. `build_rag_context()` includes broad course overview plus up to two shared docs.
3. The prompt asks the model to answer under 120 words.
4. `validate_reply()` only catches a small subset of unsupported claims.
5. The answer is sent if it is short, does not mention internals, and avoids a few obvious bad patterns.

This path can still produce fluent unsupported answers.

## Recommended remediation order

1. Keep the current exact-answer rules for high-risk facts.
2. Add structured `active_topic`, `last_question_asked`, `last_offered_options`, and `last_disputed_claim`.
3. Replace shared-doc retrieval with passage retrieval and metadata filters.
4. Add a requested-field planner for every inbound question.
5. Add claim extraction and validation for dates, fees, venues, trainer details, HRDC, contact details, payment, cancellation, and catalog items.
6. Restrict live generation to low-risk explanatory wording over already-selected facts.
7. Expand evaluation from screenshot substring regression to source-grounded transcript tests.

## Acceptance gate before trusting RAG again

Do not rely on live RAG for unsupervised customer answers until these are true:

- Explicit course name, HRDC number, referral ad, and unique date select the correct course 100% of the time.
- Ambiguous course references clarify instead of guessing.
- Every high-risk claim in the answer is traceable to a structured fact or retrieved passage.
- Multi-part questions have field-level coverage.
- Corrections retract the exact disputed claim without introducing a new one.
- Short replies resolve from structured previous-turn state, not broad transcript inference.
- Live AI and deterministic replay both pass the same black-box transcript set.

## Bottom line

The project now has good targeted patches for the screenshot failures. The remaining problem is that the RAG layer still does not have a hard grounding contract.

The next fix should not be another prompt rewrite or more Markdown content. The next fix should be:

**field planning -> constrained passage retrieval -> claim validation -> answer coverage check**.
