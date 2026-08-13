from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from rag_v2.embeddings import configured_embedder
from rag_v2.generation import (
    BedrockGroundedGenerator,
    GroqGroundedGenerator,
    GroundedGenerator,
    OpenRouterGroundedGenerator,
)
from rag_v2.ingestion import build_index
from rag_v2.kb_ingestion import build_kb_documents, kb_content_version
from rag_v2.models import CorpusDocument, RagRequest, RetrievalFilter, SearchResult
from rag_v2.retriever import HybridRetriever, RetrievalConfig
from rag_v2.service import RagService
from rag_v2.store import SQLiteVectorStore
from services.bedrock import bedrock_model, bedrock_region
from services.course_loader import course_content_version, course_context_text, load_courses
from services.fallbacks import generation_unavailable, warm_fallback
from services.knowledge_base import knowledge_content_version, load_knowledge_base

if TYPE_CHECKING:
    from services.course_loader import CourseConfig

logger = logging.getLogger(__name__)

_TOP_K = int(os.getenv("RAG_V2_TOP_K", "5"))
_MIN_SCORE = float(os.getenv("RAG_V2_MIN_SCORE", "0.42"))


def _use_postgres() -> bool:
    """Store the vector index in Postgres (RAG_STORE=postgres + DATABASE_URL) instead of the
    on-disk SQLite file. Default off, so nothing changes until this is explicitly enabled."""
    return os.getenv("RAG_STORE", "").strip().lower() in {"postgres", "postgresql", "pg"} and bool(
        os.getenv("DATABASE_URL", "").strip()
    )


def _force_rebuild() -> bool:
    """Re-embed from the content files even if the DB is populated (for content updates)."""
    return os.getenv("RAG_FORCE_REBUILD", "").strip().lower() in {"1", "true", "yes", "on"}


class _NoApiKeyGenerator:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    def generate(self, request: RagRequest, evidence: list[SearchResult]):
        del request
        from rag_v2.models import GroundedAnswer

        return GroundedAnswer(
            "rejected",
            "",
            sources=tuple(evidence),
            reason=f"{self.provider} API key is not configured",
        )


def _generation_settings() -> tuple[str, str, str]:
    provider = os.getenv("RAG_GENERATION_PROVIDER", "groq").strip().lower()
    configured_model = os.getenv("RAG_GENERATION_MODEL", "").strip()
    if provider == "openrouter":
        api_key = (
            os.getenv("OPEN_ROUTER_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
        ).strip()
        return provider, api_key, configured_model or "openai/gpt-oss-120b"
    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        model = configured_model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
        return provider, api_key, model
    if provider == "bedrock":
        # A non-empty credential marker keeps runtime initialization independent
        # of the credential mechanism; boto3 resolves IAM/profile/key/API-token.
        return provider, f"aws:{bedrock_region()}", bedrock_model(configured_model)
    raise ValueError("RAG_GENERATION_PROVIDER must be 'bedrock', 'groq', or 'openrouter'")


def _configured_generator(provider: str, api_key: str, model: str) -> GroundedGenerator:
    if provider == "bedrock":
        return BedrockGroundedGenerator(model=model, region=bedrock_region())
    if not api_key:
        return _NoApiKeyGenerator(provider)
    if provider == "openrouter":
        return OpenRouterGroundedGenerator(
            api_key=api_key,
            model=model,
            base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            site_url=os.getenv("OPENROUTER_SITE_URL", ""),
            app_name=os.getenv("OPENROUTER_APP_NAME", "Timmins WhatsApp Assistant"),
        )
    return GroqGroundedGenerator(api_key=api_key, model=model)


def _source_version() -> tuple:
    return (
        "rag-v2-runtime",
        course_content_version(),
        knowledge_content_version(),
        kb_content_version(),
    )


def _documents() -> list[CorpusDocument]:
    documents: list[CorpusDocument] = []
    courses = load_courses()
    active_lines = []
    for course in courses.values():
        if course.active:
            active_lines.append(f"- {course.name}: {course.dates}, {course.venue}")

    if active_lines:
        documents.append(
            CorpusDocument(
                document_id="catalog-active",
                title="Active Timmins course catalog",
                text="Active Timmins training courses currently scheduled:\n"
                + "\n".join(active_lines),
                source_ref="generated/catalog-active",
                topic="catalog",
                metadata={"authority": "generated_from_course_config"},
            )
        )

    for slug, course in courses.items():
        if not course.active:
            continue  # inactive courses (not in NEW_Courses) are excluded from the index
        metadata = {
            "authority": "approved_course_files",
            "active": course.active,
            "course_name": course.name,
        }
        documents.append(
            CorpusDocument(
                document_id=f"{slug}-facts",
                title=f"{course.name} facts",
                text=course_context_text(course),
                source_ref=f"courses/{slug}/config.yaml",
                course_id=slug,
                topic="course_facts",
                metadata=metadata,
            )
        )
        if course.overview.strip():
            documents.append(
                CorpusDocument(
                    document_id=f"{slug}-overview",
                    title=f"{course.name} overview",
                    text=course.overview,
                    source_ref=f"courses/{slug}/overview.md",
                    course_id=slug,
                    topic="course_overview",
                    metadata=metadata,
                )
            )

    for topic, text in load_knowledge_base().items():
        documents.append(
            CorpusDocument(
                document_id=f"knowledge-{topic}",
                title=f"Timmins {topic}",
                text=text,
                source_ref=f"knowledge/{topic}.md",
                topic=topic,
                metadata={"authority": "approved_knowledge_file"},
            )
        )

    active_slugs = {slug for slug, course in courses.items() if course.active}

    if _content_source() == "db":
        # DB is the single source of truth: the file-based external KB is superseded by the
        # content tables (facts/overview/knowledge, already loaded above) plus dashboard uploads.
        # Emitting uploaded docs here keeps a full rebuild from pruning ingested course_kb chunks
        # (stable content-hash chunk_ids => a rebuild reproduces exactly what was ingested).
        documents.extend(_uploaded_documents(active_slugs))
        return documents

    # External KB: Q&A docx files + PDF course outlines. Exclude any bound to an
    # inactive course (e.g. Python Automation — not in NEW_Courses); keep company/
    # general KB (course_id is None).
    for doc in build_kb_documents():
        if doc.course_id is None or doc.course_id in active_slugs:
            documents.append(doc)
    return documents


def _content_source() -> str:
    return os.getenv("CONTENT_SOURCE", "files").strip().lower()


def _uploaded_documents(active_slugs: set[str]) -> list[CorpusDocument]:
    """Uploaded, ingested course docs from the `documents` table, as CorpusDocuments.

    Built the same way `services.course_ingest` builds them for ingestion, so a rebuild yields
    identical (stable content-hash) chunk_ids. Empty until the dashboard ingests anything."""
    try:
        from services.content_store import list_indexed_documents
        from services.course_ingest import document_to_corpus

        rows = list_indexed_documents()
    except Exception:
        logger.exception("event=rag_v2_uploaded_documents_failed")
        return []

    docs: list[CorpusDocument] = []
    for row in rows:
        if row.get("course_slug") not in active_slugs:
            continue
        document = document_to_corpus(row)
        if document is not None:
            docs.append(document)
    return docs


_PROFILE_FIELDS = (
    ("name", "Name"),
    ("company_name", "Company"),
    ("job_title", "Role"),
    ("who_will_pay", "Who pays"),
    ("experience_years", "Experience (yrs)"),
    ("technologies", "Technologies"),
    ("motivation", "Motivation"),
    ("learning_goals", "Goals"),
    ("budget", "Budget"),
)


def _profile_text(lead: dict | None) -> str:
    """Compact, retrieval-safe customer profile for grounded personalisation."""
    if not lead:
        return ""
    rows = []
    for key, label in _PROFILE_FIELDS:
        value = str(lead.get(key) or "").strip()
        if value:
            rows.append(f"{label}: {value}")
    return "\n".join(rows)


def _conversation(history: list[dict] | None, current_message: str) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for item in (history or [])[-8:]:
        body = str(item.get("body") or "").strip()
        if not body or body == current_message:
            continue
        direction = item.get("direction")
        role = "assistant" if direction == "outbound" else "user"
        rows.append((role, body))
    return tuple(rows)


def _retrieval_budget(message: str, *, course: "CourseConfig | None" = None) -> int:
    lower = message.lower()
    if re.search(r"\bday[ -]by[ -]day\b|\bdaily breakdown\b", lower):
        return max(_TOP_K, 7)
    if re.search(r"\bday\s+(?:\d+|one|two|three|four|five)\b", lower):
        if any(term in lower for term in ("curriculum", "syllabus", "outline", "topics")):
            return max(_TOP_K, 7)
        return max(_TOP_K, 5)
    if any(
        term in lower
        for term in ("compare", "difference", "different", "versus", " vs ", "which is better")
    ):
        return max(_TOP_K, 8)
    if (
        lower.count("?") > 1
        or " and " in lower
        and any(term in lower for term in ("fee", "date", "schedule", "venue", "hrdc", "duration"))
    ):
        return max(_TOP_K, 7)
    if any(
        term in lower
        for term in ("curriculum", "syllabus", "covered", "outline", "topics", "learn")
    ):
        return max(_TOP_K, 7)
    if re.search(r"\b(fee|fees|price|cost|duration|how many days|venue|location|date)\b", lower):
        return 3 if course else 5
    return _TOP_K


class RagV2Runtime:
    def __init__(self, index_path: Path | None = None) -> None:
        self.index_path = index_path or Path(os.getenv("RAG_V2_INDEX", "var/rag_v2.sqlite"))
        self._lock = threading.Lock()
        self._version: tuple | None = None
        self._service: RagService | None = None
        self._generation_provider = ""
        self._generation_model = ""

    def _ensure_current(self) -> RagService:
        generation_provider, api_key, generation_model = _generation_settings()
        version = (
            *_source_version(),
            generation_provider,
            generation_model,
            bool(api_key),
        )
        if self._service is not None and self._version == version:
            return self._service
        with self._lock:
            if self._service is not None and self._version == version:
                return self._service
            embedder = configured_embedder()
            store = self._make_store()
            generator = _configured_generator(generation_provider, api_key, generation_model)

            # Code-only path: when the Postgres store is already populated and compatible,
            # read the pre-built index straight from it — no content files, no re-embedding.
            # (SQLite always rebuilds from files, so local dev and the current live app are
            # unchanged until RAG_STORE=postgres is set.)
            if _use_postgres() and not _force_rebuild() and self._store_ready(store, embedder):
                health = store.health()
                chunk_count = int(health.get("actual_chunk_count") or 0)
                event = "rag_v2_index_reused"
                doc_count = int(health.get("document_count") or 0)
            else:
                documents = _documents()
                chunk_count = build_index(documents, embedder=embedder, store=store)
                health = store.health()
                event = "rag_v2_index_built"
                doc_count = len(documents)

            self._service = RagService(
                HybridRetriever(store, embedder, RetrievalConfig(min_score=_MIN_SCORE)),
                generator,
            )
            self._version = version
            self._generation_provider = generation_provider
            self._generation_model = generation_model
            logger.info(
                "event=%s documents=%d chunks=%d embedder=%s dimension=%s corpus_version=%s backend=%s generation_provider=%s generation_model=%s",
                event,
                doc_count,
                chunk_count,
                embedder.model_id,
                embedder.dimension,
                health.get("corpus_version", ""),
                health.get("backend", "sqlite"),
                generation_provider,
                generation_model,
            )
            return self._service

    def _make_store(self):
        """Postgres store when RAG_STORE=postgres (+ DATABASE_URL); SQLite otherwise."""
        if _use_postgres():
            from rag_v2.pg_store import PostgresVectorStore

            return PostgresVectorStore()
        return SQLiteVectorStore(self.index_path)

    @staticmethod
    def _store_ready(store, embedder) -> bool:
        """True if the store already holds a compatible, non-empty index."""
        try:
            store.assert_compatible(model_id=embedder.model_id, dimension=embedder.dimension)
            return store.count() > 0
        except Exception:  # noqa: BLE001 — not built / incompatible / unreachable -> rebuild
            return False

    def warmup(self) -> None:
        self._ensure_current()

    def health(self) -> dict[str, str | int | bool]:
        self._ensure_current()
        store = SQLiteVectorStore(self.index_path)
        metadata = store.health()
        return {
            "ready": bool(metadata),
            "index_path": str(self.index_path),
            "generation_provider": self._generation_provider,
            "generation_model": self._generation_model,
            **metadata,
            "pgvector_recommended_when": "corpus/deployment outgrows SQLite linear scan",
        }

    def answer(
        self,
        message: str,
        *,
        retrieval_query: str | None = None,
        course: "CourseConfig | None" = None,
        lead: dict | None = None,
        history: list[dict] | None = None,
        trace_label: str | None = None,
    ) -> str:
        service = self._ensure_current()
        filters = RetrievalFilter(
            course_id=course.slug if course else None,
            topics=(
                "course_overview",
                "course_facts",
                "course_kb",
                "course_hrdc_outline",
                "course_flyer",
            )
            if course
            else (),
        )
        normalized_retrieval_query = (retrieval_query or "").strip() or message
        result = service.answer(
            RagRequest(
                question=message,
                retrieval_filter=filters,
                require_course_scope=course is not None,
                conversation=_conversation(history, message),
                top_k=_retrieval_budget(normalized_retrieval_query, course=course),
                profile=_profile_text(lead),
                trace_id=trace_label or "",
                retrieval_query=normalized_retrieval_query,
            )
        )
        if result.sources:
            logger.info(
                "event=rag_v2_sources trace_label=%s status=%s sources=%s",
                trace_label or "",
                result.status,
                [
                    {
                        "chunk_id": item.chunk.chunk_id,
                        "source_ref": item.chunk.source_ref,
                        "score": round(item.score, 4),
                        "semantic": round(item.semantic_score, 4),
                        "lexical": round(item.lexical_score, 4),
                        "metadata": round(item.metadata_score, 4),
                        "chunk_metadata": item.chunk.metadata,
                    }
                    for item in result.sources
                ],
            )
        if result.status in {"answered", "clarify"}:
            return result.text
        if result.status == "rejected":
            return generation_unavailable(course)
        return warm_fallback(course)


_runtime: RagV2Runtime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> RagV2Runtime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = RagV2Runtime()
    return _runtime


def warmup() -> None:
    get_runtime().warmup()


def health() -> dict[str, str | int | bool]:
    return get_runtime().health()


def answer(
    message: str,
    *,
    retrieval_query: str | None = None,
    course: "CourseConfig | None" = None,
    lead: dict | None = None,
    history: list[dict] | None = None,
    trace_label: str | None = None,
) -> str:
    return get_runtime().answer(
        message,
        retrieval_query=retrieval_query,
        course=course,
        lead=lead,
        history=history,
        trace_label=trace_label,
    )
