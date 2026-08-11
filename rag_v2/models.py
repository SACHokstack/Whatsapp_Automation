from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class CorpusDocument:
    """Canonical input document. It has no dependency on the legacy KB layout."""

    document_id: str
    title: str
    text: str
    source_ref: str
    course_id: str | None = None
    topic: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    title: str
    text: str
    source_ref: str
    ordinal: int
    course_id: str | None = None
    topic: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalFilter:
    """Hard retrieval constraints. Values are never treated as ranking hints."""

    course_id: str | None = None
    topics: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float
    semantic_score: float
    lexical_score: float
    metadata_score: float = 0.0


@dataclass(frozen=True)
class RagRequest:
    question: str
    retrieval_filter: RetrievalFilter = field(default_factory=RetrievalFilter)
    require_course_scope: bool = False
    conversation: tuple[tuple[Literal["user", "assistant"], str], ...] = ()
    top_k: int = 5
    profile: str = ""
    trace_id: str = ""
    # The customer's wording is retained in `question` for response style and
    # generation.  A standalone, intent-normalized query can be supplied here for
    # retrieval, which is especially important for terse follow-ups such as
    # "syllabus" or "day two".
    retrieval_query: str = ""


@dataclass(frozen=True)
class Claim:
    text: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class GroundedAnswer:
    status: Literal["answered", "clarify", "not_found", "rejected"]
    text: str
    claims: tuple[Claim, ...] = ()
    sources: tuple[SearchResult, ...] = ()
    reason: str = ""

    @property
    def grounded(self) -> bool:
        return (
            self.status == "answered"
            and bool(self.claims)
            and all(claim.source_ids for claim in self.claims)
        )
