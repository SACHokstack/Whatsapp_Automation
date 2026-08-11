# RAG v2

This package is a clean replacement for the legacy context-building code. It does not import the
legacy knowledge loader, course loader, reply cache, router, or response guard.

## Guarantees

- real local semantic embeddings through FastEmbed;
- persistent, inspectable vectors in SQLite;
- hybrid semantic and BM25 retrieval;
- hard course, topic, and document filters;
- section-aware chunks with stable identifiers;
- minimum-score abstention;
- one citation set for every generated claim;
- rejection of missing or unknown citations;
- deterministic dependency injection for offline tests.

SQLite is intentional for this corpus size. The store is behind one class boundary, so a dedicated
vector database can replace it later without changing ingestion, retrieval, generation, or callers.

## Canonical corpus

The new implementation consumes JSON Lines, not the legacy directory layout. Each line is one
approved source document:

```json
{
  "document_id": "unique-stable-id",
  "title": "Human-readable title",
  "text": "# Section\nApproved source text",
  "source_ref": "approved/source-and-version",
  "course_id": "course-slug-or-null",
  "topic": "fees-or-schedule-or-company",
  "metadata": {"authority": "approved", "version": "1"}
}
```

`document_id`, `title`, `text`, and `source_ref` are required. `course_id` and `topic` become hard
retrieval filters. Put only approved customer-facing information into this corpus; do not copy
prompt instructions or internal notes into source text.

See `corpus.example.jsonl` for a runnable shape. It contains fictional data and is not production
knowledge.

## Build the index

```bash
python -m rag_v2 build \
  --corpus path/to/approved-corpus.jsonl \
  --index var/rag_v2.sqlite
```

The first build downloads the configured embedding model. The default is
`BAAI/bge-small-en-v1.5`; override it with `--embedding-model`. An index records the exact model and
dimension and refuses queries from an incompatible embedder.

## Inspect retrieval

Retrieval can be tested without Groq:

```bash
python -m rag_v2 retrieve \
  --index var/rag_v2.sqlite \
  --course course-alpha \
  --topic commercial \
  "What is the fee?"
```

The JSON output includes combined, semantic, and lexical scores plus the exact chunks selected.
The conservative default retrieval threshold is `0.42`; calibrate it on a held-out corpus rather
than lowering it to make every question return a passage.

## Generate a grounded answer

```bash
python -m rag_v2 query \
  --index var/rag_v2.sqlite \
  --course course-alpha \
  --require-course \
  "What are the fee and dates?"
```

Generation uses `GROQ_API_KEY`. The model must return structured claims with valid retrieved chunk
IDs. Invalid or uncited output is rejected and becomes `not_found`.

## Application boundary

The caller must pass confirmed course scope for course-specific questions:

```python
request = RagRequest(
    question=message,
    retrieval_filter=RetrievalFilter(course_id=active_course, topics=("commercial",)),
    require_course_scope=True,
)
answer = service.answer(request)
```

Course selection, human handoff, stop consent, and message deduplication belong outside RAG. Do not
silently infer a course inside the retriever. If the application cannot confirm a course,
`require_course_scope=True` returns a clarification before retrieval.

## Cutover rule

Do not route customer traffic to RAG v2 merely because the index builds. First create an approved
corpus and replay the black-box cases in `RAG_BLACK_BOX_AUDIT.md`. Compare legacy and v2 responses
in shadow mode, then switch only after the acceptance criteria pass.
