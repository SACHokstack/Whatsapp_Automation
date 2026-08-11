from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from rag_v2.embeddings import Embedder, Vector, normalize
from rag_v2.models import RetrievalFilter, SearchResult
from rag_v2.query_expansion import (
    expand_query,
    normalize_query,
    requested_day,
    requested_session,
)
from rag_v2.store import SQLiteVectorStore

_WORD_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "can",
    "could",
    "for",
    "from",
    "i",
    "in",
    "is",
    "it",
    "know",
    "me",
    "of",
    "on",
    "please",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "would",
    "you",
}


def _terms(text: str) -> list[str]:
    return [term for term in _WORD_RE.findall(text.lower()) if term not in _STOPWORDS]


def _metadata_range_matches(metadata: dict[str, Any], prefix: str, value: int) -> bool:
    try:
        start = int(metadata.get(f"{prefix}_start"))
        end = int(metadata.get(f"{prefix}_end", start))
    except (TypeError, ValueError):
        return False
    return start <= value <= end


def _structure_metadata_score(
    metadata: dict[str, Any],
    day: int | None,
    session: int | None,
    *,
    agenda_query: bool,
    broad_content_query: bool,
) -> float:
    section_type = metadata.get("section_type")
    scores = [0.0]
    if broad_content_query and section_type in ("topics", "outcomes", "tools", "agenda"):
        scores.append(0.18)
    if day is not None:
        if _metadata_range_matches(metadata, "day", day):
            scores.append(0.28)
        elif metadata.get("day_start") is None and section_type == "agenda":
            # Useful structural fallback for a session-only outline. The generator is
            # explicitly told that this is not a verified day mapping.
            scores.append(0.08)
    if session is not None:
        if _metadata_range_matches(metadata, "session", session):
            scores.append(0.28)
        elif metadata.get("session_start") is None and section_type == "agenda":
            scores.append(0.05)
    if agenda_query and day is None and session is None and section_type == "agenda":
        scores.append(0.16)
    return max(scores)


def _result_fingerprint(result: SearchResult) -> str:
    return " ".join(_terms(result.chunk.text))


def _dot(left: Vector, right: Vector) -> float:
    if len(left) != len(right):
        raise ValueError("query and document embeddings have different dimensions")
    return sum(a * b for a, b in zip(left, right, strict=True))


@dataclass(frozen=True)
class RetrievalConfig:
    semantic_weight: float = 0.78
    lexical_weight: float = 0.22
    min_score: float = 0.42
    rrf_k: int = 60

    def __post_init__(self) -> None:
        if self.semantic_weight < 0 or self.lexical_weight < 0:
            raise ValueError("retrieval weights cannot be negative")
        if self.semantic_weight + self.lexical_weight == 0:
            raise ValueError("at least one retrieval weight must be positive")
        if not 0 <= self.min_score <= 1:
            raise ValueError("min_score must be between zero and one")


class HybridRetriever:
    """Semantic + BM25 retrieval with reciprocal-rank fusion and hard filters."""

    def __init__(
        self,
        store: SQLiteVectorStore,
        embedder: Embedder,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.config = config or RetrievalConfig()

    def _bm25(self, query: str, texts: list[str]) -> list[float]:
        query_terms = _terms(query)
        if not query_terms or not texts:
            return [0.0] * len(texts)
        documents = [_terms(text) for text in texts]
        average_length = sum(len(document) for document in documents) / max(len(documents), 1)
        document_frequency = Counter(term for document in documents for term in set(document))
        query_frequency = Counter(query_terms)
        scores: list[float] = []
        k1 = 1.5
        b = 0.75
        total = len(documents)
        for document in documents:
            frequencies = Counter(document)
            score = 0.0
            for term, query_count in query_frequency.items():
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency[term]
                inverse_frequency = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + k1 * (1 - b + b * len(document) / max(average_length, 1))
                score += query_count * inverse_frequency * frequency * (k1 + 1) / denominator
            scores.append(score)
        return scores

    @staticmethod
    def _rank(scores: list[float]) -> dict[int, int]:
        ordered = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
        return {index: rank for rank, index in enumerate(ordered, start=1)}

    @staticmethod
    def _unit_semantic(score: float) -> float:
        # Cosine zero means no semantic agreement. Mapping [-1, 1] to [0, 1] would give an
        # unrelated orthogonal vector a misleading baseline score of 0.5.
        return max(0.0, min(1.0, score))

    def retrieve(
        self,
        query: str,
        *,
        filters: RetrievalFilter | None = None,
        top_k: int = 5,
        trace: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if not query.strip():
            return []
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self.store.assert_compatible(
            model_id=self.embedder.model_id, dimension=self.embedder.dimension
        )
        active_filters = filters or RetrievalFilter()
        normalized_query = normalize_query(query)
        expanded_query, expansion_terms = expand_query(normalized_query)
        day = requested_day(normalized_query)
        session = requested_session(normalized_query)
        agenda_query = bool(
            re.search(
                r"\bday[ -]by[ -]day\b|\bdaily breakdown\b|\beach day\b|"
                r"\bagenda\b|\bday\s+\d{1,2}\b|\bsession\s+\d{1,2}\b",
                normalized_query,
            )
        )
        broad_content_query = bool(
            re.search(
                r"\b(?:syllabus|curriculum|course content|course structure|covered topics|"
                r"topics covered|modules?|sessions?|learning outcomes|breakdown|detail|details|"
                r"detailed|full outline|whole (?:course|thing)|everything|step[ -]by[ -]step|"
                r"go through|walk me through|what (?:will|do) (?:i|you) (?:learn|teach)|"
                r"what(?:'s| is| does)?(?: it)? cover(?:ed|s)?)\b",
                normalized_query,
            )
        )
        candidates = list(self.store.iter_candidates(active_filters))
        if trace is not None:
            trace.update(
                {
                    "query": query,
                    "normalized_query": normalized_query,
                    "expanded_query": expanded_query,
                    "expansion_terms": list(expansion_terms),
                    "requested_day": day,
                    "requested_session": session,
                    "agenda_query": agenda_query,
                    "filters": {
                        "course_id": active_filters.course_id,
                        "topics": list(active_filters.topics),
                        "document_ids": list(active_filters.document_ids),
                    },
                    "candidate_count": len(candidates),
                    "top_k": top_k,
                    "min_score": self.config.min_score,
                }
            )
        if not candidates:
            return []

        # Expansion is useful for lexical matching. Feeding synthetic expansion terms
        # into the embedding can move it away from the user's actual meaning.
        query_vector = normalize(self.embedder.embed_query(normalized_query))
        semantic_raw = [_dot(query_vector, vector) for _, vector in candidates]
        semantic = [self._unit_semantic(score) for score in semantic_raw]
        lexical_raw = self._bm25(expanded_query, [chunk.text for chunk, _ in candidates])
        lexical_max = max(lexical_raw, default=0.0)
        lexical = [score / lexical_max if lexical_max else 0.0 for score in lexical_raw]

        semantic_rank = self._rank(semantic)
        lexical_rank = self._rank(lexical)
        weight_sum = self.config.semantic_weight + self.config.lexical_weight
        fused: list[SearchResult] = []
        for index, (chunk, _) in enumerate(candidates):
            weighted_score = (
                self.config.semantic_weight * semantic[index]
                + self.config.lexical_weight * lexical[index]
            ) / weight_sum
            rank_bonus = (
                self.config.semantic_weight / (self.config.rrf_k + semantic_rank[index])
                + self.config.lexical_weight / (self.config.rrf_k + lexical_rank[index])
            ) / weight_sum
            metadata_score = _structure_metadata_score(
                chunk.metadata,
                day,
                session,
                agenda_query=agenda_query,
                broad_content_query=broad_content_query,
            )
            # A matching day range is explicit evidence and can rescue a weak embedding,
            # while still requiring some semantic or lexical agreement.
            if weighted_score < self.config.min_score and not (
                metadata_score and (semantic[index] > 0 or lexical[index] > 0)
            ):
                continue
            final_score = min(1.0, weighted_score + rank_bonus + metadata_score)
            fused.append(
                SearchResult(
                    chunk=chunk,
                    score=final_score,
                    semantic_score=semantic[index],
                    lexical_score=lexical[index],
                    metadata_score=metadata_score,
                )
            )

        fused.sort(
            key=lambda result: (
                -result.metadata_score
                if day is not None
                or session is not None
                or agenda_query
                or broad_content_query
                else 0.0,
                -result.score,
                result.chunk.chunk_id,
            )
        )
        selected: list[SearchResult] = []
        seen_content: set[str] = set()
        per_document: Counter[str] = Counter()
        # A source file is often a structured course overview containing twenty
        # independent Q&A sections.  Limiting that whole file to three chunks is
        # useful for narrow questions, but starves broad syllabus requests of the
        # evidence needed for a representative answer.
        document_limit = min(top_k, 6) if broad_content_query else 3
        for result in fused:
            fingerprint = _result_fingerprint(result)
            if (
                fingerprint in seen_content
                or per_document[result.chunk.document_id] >= document_limit
            ):
                continue
            selected.append(result)
            seen_content.add(fingerprint)
            per_document[result.chunk.document_id] += 1
            if len(selected) >= top_k:
                break

        # A syllabus/breakdown/structure request enumerates the whole course — a LIST, not
        # a semantic sample. The per-session chunks routinely lose the top-k contest to
        # higher-scoring "Learning outcomes"/"Key topics" chunks, so the answer silently
        # drops sessions. When the whole structure is asked for (not one specific day or
        # session) on a scoped course, force in EVERY session/day/module chunk that cleared
        # the score threshold, in order, so no session is ever missing.
        whole_structure = (
            (broad_content_query or agenda_query) and session is None and day is None
        )
        if whole_structure and active_filters.course_id:
            selected_ids = {result.chunk.chunk_id for result in selected}

            def _agenda_order(result: SearchResult) -> int:
                meta = result.chunk.metadata
                for key in ("session_start", "day_start", "module_start"):
                    value = meta.get(key)
                    if value is not None:
                        return int(value)
                return 10**6

            agenda_results = sorted(
                (
                    result
                    for result in fused
                    if result.chunk.metadata.get("section_type") == "agenda"
                    and any(
                        result.chunk.metadata.get(key) is not None
                        for key in ("session_start", "day_start", "module_start")
                    )
                ),
                key=_agenda_order,
            )
            for result in agenda_results:
                if result.chunk.chunk_id not in selected_ids:
                    selected.append(result)
                    seen_content.add(_result_fingerprint(result))
                    selected_ids.add(result.chunk.chunk_id)

        if trace is not None:
            trace["selected"] = [
                {
                    "chunk_id": result.chunk.chunk_id,
                    "document_id": result.chunk.document_id,
                    "source_ref": result.chunk.source_ref,
                    "course_id": result.chunk.course_id,
                    "topic": result.chunk.topic,
                    "score": round(result.score, 6),
                    "semantic_score": round(result.semantic_score, 6),
                    "lexical_score": round(result.lexical_score, 6),
                    "metadata_score": round(result.metadata_score, 6),
                    "metadata": result.chunk.metadata,
                }
                for result in selected
            ]
            trace["selected_count"] = len(selected)
        return selected
