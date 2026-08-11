from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from rag_v2.chunking import ChunkingConfig, chunk_document
from rag_v2.embeddings import Embedder
from rag_v2.models import Chunk, CorpusDocument
from rag_v2.store import SQLiteVectorStore


class CorpusValidationError(ValueError):
    pass


_AUTHORITY_PRIORITY = {
    "approved_course_files": 60,
    "approved_brs_docx": 55,
    "approved_knowledge_file": 50,
    "approved_kb_docx": 40,
    "approved_pdf": 30,
    "generated_from_course_config": 10,
}


def _content_fingerprint(chunk: Chunk) -> str:
    # Titles vary between source formats. Compare the evidence body so copied Q&A
    # material from Markdown, DOCX, and PDF is indexed only once.
    body = chunk.text.split("\n", 1)[-1]
    body = re.sub(r"\b(?:q\d+\.?|answer\s*:)\b", " ", body, flags=re.IGNORECASE)
    normalized = " ".join(re.findall(r"[a-z0-9]+", body.lower()))
    if len(normalized.split()) < 10:
        normalized = f"{chunk.document_id} {chunk.ordinal} {normalized}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _authority_priority(chunk: Chunk) -> int:
    return _AUTHORITY_PRIORITY.get(str(chunk.metadata.get("authority") or ""), 0)


def _optional_string(value: Any, field: str, line_number: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CorpusValidationError(f"line {line_number}: {field} must be a string or null")
    normalized = value.strip()
    return normalized or None


def load_jsonl_corpus(path: str | Path) -> list[CorpusDocument]:
    corpus_path = Path(path)
    documents: list[CorpusDocument] = []
    seen_ids: set[str] = set()
    with corpus_path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise CorpusValidationError(
                    f"line {line_number}: invalid JSON: {error.msg}"
                ) from error
            if not isinstance(value, dict):
                raise CorpusValidationError(f"line {line_number}: record must be an object")
            required: dict[str, str] = {}
            for field in ("document_id", "title", "text", "source_ref"):
                item = value.get(field)
                if not isinstance(item, str) or not item.strip():
                    raise CorpusValidationError(
                        f"line {line_number}: {field} must be a non-empty string"
                    )
                required[field] = item.strip()
            if required["document_id"] in seen_ids:
                raise CorpusValidationError(
                    f"line {line_number}: duplicate document_id {required['document_id']}"
                )
            seen_ids.add(required["document_id"])
            metadata = value.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise CorpusValidationError(f"line {line_number}: metadata must be an object")
            documents.append(
                CorpusDocument(
                    **required,
                    course_id=_optional_string(value.get("course_id"), "course_id", line_number),
                    topic=_optional_string(value.get("topic"), "topic", line_number),
                    metadata=metadata,
                )
            )
    if not documents:
        raise CorpusValidationError("corpus is empty")
    return documents


def build_chunks(
    documents: Iterable[CorpusDocument], config: ChunkingConfig | None = None
) -> list[Chunk]:
    raw_chunks = [chunk for document in documents for chunk in chunk_document(document, config)]
    by_content: dict[str, Chunk] = {}
    for chunk in raw_chunks:
        fingerprint = _content_fingerprint(chunk)
        existing = by_content.get(fingerprint)
        if existing is None or _authority_priority(chunk) > _authority_priority(existing):
            by_content[fingerprint] = chunk
    chunks = list(by_content.values())
    if not chunks:
        raise CorpusValidationError("corpus produced no chunks")
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise CorpusValidationError("corpus produced duplicate chunk IDs")
    return chunks


def build_index(
    documents: Sequence[CorpusDocument],
    *,
    embedder: Embedder,
    store: SQLiteVectorStore,
    chunking: ChunkingConfig | None = None,
) -> int:
    chunks = build_chunks(documents, chunking)
    dimension = embedder.dimension
    store.initialize(model_id=embedder.model_id, dimension=dimension)
    existing_ids = store.chunk_ids()
    missing_chunks = [chunk for chunk in chunks if chunk.chunk_id not in existing_ids]
    batch_size = int(os.getenv("RAG_EMBED_BATCH_SIZE", "64"))
    new_embeddings: dict[str, Sequence[float]] = {}
    for start in range(0, len(missing_chunks), batch_size):
        batch = missing_chunks[start : start + batch_size]
        embeddings = embedder.embed_documents([chunk.text for chunk in batch])
        if len(embeddings) != len(batch):
            raise RuntimeError("embedding provider did not return one vector per chunk")
        for chunk, embedding in zip(batch, embeddings, strict=True):
            if len(embedding) != dimension:
                raise RuntimeError(
                    f"embedding provider returned dimension {len(embedding)}; expected {dimension}"
                )
            new_embeddings[chunk.chunk_id] = embedding
    corpus_version = hashlib.sha256(
        "\n".join(
            f"{chunk.chunk_id}:{chunk.document_id}:{chunk.source_ref}:{chunk.topic or ''}:"
            f"{json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True)}"
            for chunk in chunks
        ).encode("utf-8")
    ).hexdigest()
    store.sync(
        chunks,
        new_embeddings,
        document_count=len(documents),
        corpus_version=corpus_version,
    )
    return len(chunks)
