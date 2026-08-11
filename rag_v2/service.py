from __future__ import annotations

import time

from rag_v2.generation import GroundedGenerator
from rag_v2.models import GroundedAnswer, RagRequest
from rag_v2.observability import utc_ms, write_retrieval_trace
from rag_v2.retriever import HybridRetriever


class RagService:
    """Safe orchestration boundary for retrieval and grounded generation."""

    def __init__(self, retriever: HybridRetriever, generator: GroundedGenerator) -> None:
        self.retriever = retriever
        self.generator = generator

    def answer(self, request: RagRequest) -> GroundedAnswer:
        started = time.perf_counter()
        trace = {
            "ts_ms": utc_ms(),
            "trace_id": request.trace_id,
            "stage": "retrieval",
            "status": "",
            "reason": "",
        }
        if not request.question.strip():
            trace.update({"status": "clarify", "reason": "empty query", "latency_ms": 0})
            write_retrieval_trace(trace)
            return GroundedAnswer("clarify", "What would you like to know?", reason="empty query")
        if request.require_course_scope and not request.retrieval_filter.course_id:
            trace.update(
                {"status": "clarify", "reason": "course scope is required", "latency_ms": 0}
            )
            write_retrieval_trace(trace)
            return GroundedAnswer(
                "clarify",
                "Which course are you asking about?",
                reason="course scope is required",
            )
        retrieval_query = request.retrieval_query.strip() or request.question
        trace["question"] = request.question
        evidence = self.retriever.retrieve(
            retrieval_query,
            filters=request.retrieval_filter,
            top_k=request.top_k,
            trace=trace,
        )
        if not evidence:
            trace.update(
                {
                    "status": "not_found",
                    "reason": "no evidence above retrieval threshold",
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            write_retrieval_trace(trace)
            return GroundedAnswer(
                "not_found",
                "I don't have enough verified information to answer that.",
                reason="no evidence above retrieval threshold",
            )
        answer = self.generator.generate(request, evidence)
        if answer.status == "rejected":
            trace.update(
                {
                    "stage": "generation",
                    "generator_status": answer.status,
                    "status": "rejected",
                    "reason": answer.reason,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            write_retrieval_trace(trace)
            provider_failure = any(
                marker in answer.reason.lower()
                for marker in (
                    "generation failure:",
                    "openrouter http",
                    "api key is not configured",
                )
            )
            if provider_failure:
                # Evidence was found. Preserve infrastructure failure as a
                # distinct state so the customer is not falsely told that the
                # approved course material lacks the answer.
                return GroundedAnswer(
                    "rejected",
                    "",
                    sources=tuple(evidence),
                    reason=answer.reason,
                )
            return GroundedAnswer(
                "not_found",
                "I don't have enough verified information to answer that.",
                sources=tuple(evidence),
                reason=answer.reason,
            )
        trace.update(
            {
                "stage": "generation",
                "generator_status": answer.status,
                "status": answer.status,
                "reason": answer.reason,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "claims": [
                    {"text": claim.text, "source_ids": list(claim.source_ids)}
                    for claim in answer.claims
                ],
            }
        )
        write_retrieval_trace(trace)
        return answer
