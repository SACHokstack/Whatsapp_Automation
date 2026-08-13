"""Turn an uploaded course document into searchable chunks.

The pipeline the client never sees: upload -> extract text (OCR if scanned) -> chunk -> embed ->
write into the vector store, scoped to one course. It reuses the existing chunker, the warm
in-process embedder, and the same store the query path reads from, so an ingested document is
searchable on the very next question with no restart — `HybridRetriever` fetches candidates from
the store per query and recomputes BM25 over them, holding no persisted index of its own.

Two rules keep this safe alongside the boot-time index build:

1. Writes are always `sync_course`, never `sync`. A global prune here would delete every other
   course's chunks (see `PostgresVectorStore.sync_course`).
2. A course is re-synced from *all* of its indexed documents, not just the new one, because the
   scoped prune deletes anything in the course that is not in the batch it is given.
"""

from __future__ import annotations

import logging

from rag_v2.chunking import ChunkingConfig
from rag_v2.ingestion import build_chunks
from rag_v2.models import Chunk, CorpusDocument

logger = logging.getLogger(__name__)

# The chunk metadata that marks uploaded course files as first-class approved evidence.
# `_AUTHORITY_PRIORITY` in rag_v2.ingestion ranks this above generated config text, so an
# uploaded brochure wins over a synthesized fact when the two say the same thing.
UPLOAD_AUTHORITY = "approved_course_files"
UPLOAD_TOPIC = "course_kb"


class IngestError(RuntimeError):
    pass


def document_to_corpus(row: dict) -> CorpusDocument | None:
    """Build the CorpusDocument for one `documents` row.

    Single source of truth for this shape: the boot-time rebuild path
    (`rag_v2.runtime._uploaded_documents`) calls this too, so a rebuild reproduces exactly the
    chunk_ids the dashboard ingested (they hash document_id + ordinal + text) instead of
    churning the index.
    """
    slug = row.get("course_slug")
    text = (row.get("extracted_text") or "").strip()
    if not slug or not text:
        return None
    return CorpusDocument(
        document_id=f"{slug}-upload-{row['id']}",
        title=f"{slug} uploaded document {row.get('filename', '')}".strip(),
        text=text,
        source_ref=f"upload/{row.get('filename', row['id'])}",
        course_id=slug,
        topic=UPLOAD_TOPIC,
        metadata={"authority": UPLOAD_AUTHORITY},
    )


def _store_and_embedder():
    """The same store and embedder the query path uses, so writes land where reads look."""
    from rag_v2.embeddings import configured_embedder
    from rag_v2.runtime import get_runtime

    embedder = configured_embedder()
    store = get_runtime()._make_store()
    # Idempotent: creates the tables on first use and records model/dimension. Without it a
    # sync into a never-built index raises IndexCompatibilityError.
    store.initialize(model_id=embedder.model_id, dimension=embedder.dimension)
    return store, embedder


def _embed_missing(store, embedder, chunks: list[Chunk]) -> dict[str, list[float]]:
    """Embed only chunks the store has never seen; unchanged text keeps its vector."""
    existing = store.chunk_ids()
    missing = [c for c in chunks if c.chunk_id not in existing]
    embeddings: dict[str, list[float]] = {}
    batch_size = 32
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        vectors = embedder.embed_documents([c.text for c in batch])
        if len(vectors) != len(batch):
            raise IngestError("embedding provider did not return one vector per chunk")
        for chunk, vector in zip(batch, vectors, strict=True):
            embeddings[chunk.chunk_id] = vector
    return embeddings


def resync_course(slug: str) -> dict:
    """Rebuild one course's uploaded-document chunks from the DB. Returns a small report.

    Called after any change to a course's documents (upload, delete) and on reactivation.
    Safe to call repeatedly — unchanged chunks keep their IDs and their embeddings.
    """
    from services.content_store import documents_for_course

    rows = documents_for_course(slug)
    documents = [d for d in (document_to_corpus(r) for r in rows) if d is not None]
    store, embedder = _store_and_embedder()

    if not documents:
        written, pruned = store.sync_course(slug, [], {})
        logger.info("event=course_resync slug=%s documents=0 chunks=0 pruned=%d", slug, pruned)
        return {"documents": 0, "chunks": 0, "written": written, "pruned": pruned, "deduped": 0}

    chunks = build_chunks(documents, ChunkingConfig())
    embeddings = _embed_missing(store, embedder, chunks)
    written, pruned = store.sync_course(slug, chunks, embeddings)
    logger.info(
        "event=course_resync slug=%s documents=%d chunks=%d written=%d pruned=%d",
        slug,
        len(documents),
        len(chunks),
        written,
        pruned,
    )
    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "written": written,
        "pruned": pruned,
    }


def prune_course(slug: str) -> int:
    """Remove a course's chunks from retrieval, keeping its rows. Returns chunks pruned.

    This is what archiving does: the course stops being findable immediately, but its documents
    and extracted text survive, so reactivating is a `resync_course` away with no re-upload and
    no re-OCR.
    """
    store, _ = _store_and_embedder()
    _, pruned = store.sync_course(slug, [], {})
    logger.info("event=course_pruned slug=%s pruned=%d", slug, pruned)
    return pruned


def ingest_document(document_id: int) -> dict:
    """Extract, then re-sync the owning course. Drives the row's ingest_status throughout."""
    from services.content_store import get_document, set_document_status
    from services.doc_extract import ExtractionError, extract_text

    row = get_document(document_id, with_bytes=True)
    if not row:
        raise IngestError(f"document {document_id} not found")
    slug = str(row["course_slug"])

    try:
        text, method = extract_text(
            bytes(row["raw_bytes"] or b""),
            str(row.get("filename") or ""),
            str(row.get("content_type") or ""),
        )
    except ExtractionError as error:
        set_document_status(document_id, "failed", error=str(error))
        logger.warning("event=ingest_extract_failed id=%s error=%s", document_id, error)
        return {"status": "failed", "error": str(error)}
    except Exception as error:  # noqa: BLE001 - a malformed upload must not kill the worker
        set_document_status(document_id, "failed", error=f"extraction crashed: {error}")
        logger.exception("event=ingest_extract_crashed id=%s", document_id)
        return {"status": "failed", "error": str(error)}

    # Mark indexed before re-syncing: resync_course reads the *indexed* documents of the course,
    # so this row has to be visible to it to be included in the batch.
    set_document_status(document_id, "embedding", extracted_text=text, extraction_method=method)
    set_document_status(document_id, "indexed")
    try:
        report = resync_course(slug)
    except Exception as error:  # noqa: BLE001
        set_document_status(document_id, "failed", error=f"indexing failed: {error}")
        logger.exception("event=ingest_index_failed id=%s slug=%s", document_id, slug)
        return {"status": "failed", "error": str(error)}

    set_document_status(document_id, "indexed", chunk_count=report.get("chunks", 0))
    logger.info(
        "event=ingest_complete id=%s slug=%s method=%s chars=%d chunks=%s",
        document_id,
        slug,
        method,
        len(text),
        report.get("chunks"),
    )
    return {"status": "indexed", "method": method, "characters": len(text), **report}


def process_next_document() -> bool:
    """Claim and ingest one pending document. Returns True if there was work to do."""
    from services.content_store import claim_pending_document

    row = claim_pending_document()
    if not row:
        return False
    try:
        ingest_document(int(row["id"]))
    except Exception:  # noqa: BLE001 - never let one document stop the worker
        logger.exception("event=ingest_worker_error id=%s", row.get("id"))
        try:
            from services.content_store import set_document_status

            set_document_status(int(row["id"]), "failed", error="worker error")
        except Exception:
            logger.exception("event=ingest_status_write_failed id=%s", row.get("id"))
    return True
