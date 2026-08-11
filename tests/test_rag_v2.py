from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag_v2.chunking import ChunkingConfig, chunk_document
from rag_v2.generation import validate_generation
from rag_v2.ingestion import CorpusValidationError, build_chunks, build_index, load_jsonl_corpus
from rag_v2.kb_ingestion import (
    _filter_flyer_intake_rows,
    _structured_pdf_sections,
    build_brs_documents,
)
from rag_v2.models import CorpusDocument, GroundedAnswer, RagRequest, RetrievalFilter
from rag_v2.query_expansion import expand_query, requested_session
from rag_v2.retriever import HybridRetriever, RetrievalConfig
from rag_v2.runtime import _retrieval_budget
from rag_v2.service import RagService
from rag_v2.store import IndexCompatibilityError, SQLiteVectorStore


class TestEmbedder:
    model_id = "test:semantic-v1"
    dimension = 4

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        lower = text.lower()
        values = (
            float(sum(word in lower for word in ("alpha", "rm1,000", "penang"))),
            float(sum(word in lower for word in ("beta", "rm2,000", "kuala"))),
            float(sum(word in lower for word in ("fee", "price", "cost"))),
            float(sum(word in lower for word in ("schedule", "date", "when"))),
        )
        if not any(values):
            return (0.0, 0.0, 0.0, -1.0)
        magnitude = sum(value * value for value in values) ** 0.5
        return tuple(value / magnitude for value in values)

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


class CountingEmbedder(TestEmbedder):
    def __init__(self):
        self.document_calls: list[int] = []

    def embed_documents(self, texts):
        texts = list(texts)
        self.document_calls.append(len(texts))
        return super().embed_documents(texts)


class NeutralEmbedder:
    model_id = "test:neutral-v1"
    dimension = 2

    def embed_documents(self, texts):
        return [(1.0, 0.0) for _ in texts]

    def embed_query(self, text):
        return (1.0, 0.0)


class FirstEvidenceGenerator:
    def generate(self, request, evidence):
        first = evidence[0]
        return GroundedAnswer(
            "answered",
            first.chunk.text,
            sources=tuple(evidence),
        )


class InvalidCitationGenerator:
    def generate(self, request, evidence):
        return validate_generation(
            {
                "status": "answered",
                "claims": [{"text": "Invented answer", "source_ids": ["missing"]}],
                "clarification_question": None,
            },
            evidence,
        )


class FailIfCalledGenerator:
    def generate(self, request, evidence):
        raise AssertionError("generator must not run without relevant evidence")


def _documents() -> list[CorpusDocument]:
    return [
        CorpusDocument(
            document_id="alpha-commercial",
            title="Course Alpha",
            text=(
                "# Fees\nCourse Alpha costs RM1,000 per participant.\n\n"
                "# Schedule\nCourse Alpha is held in Penang on 1 September."
            ),
            source_ref="approved/alpha-v1",
            course_id="alpha",
            topic="commercial",
        ),
        CorpusDocument(
            document_id="beta-commercial",
            title="Course Beta",
            text=(
                "# Fees\nCourse Beta costs RM2,000 per participant.\n\n"
                "# Schedule\nCourse Beta is held in Kuala Lumpur on 8 September."
            ),
            source_ref="approved/beta-v1",
            course_id="beta",
            topic="commercial",
        ),
    ]


class ChunkingTests(unittest.TestCase):
    def test_day_sections_are_independent_chunks_with_day_metadata(self):
        document = CorpusDocument(
            document_id="agenda",
            title="Course overview",
            text=(
                "# Course Overview\nA practical five-day course.\n\n"
                "Course structure (5 days):\n\n"
                "Days 1–2 — Foundations:\n- Boot flow\n- Linux kernel\n\n"
                "Days 3–4 — Build system:\n- BitBake\n- Layers\n\n"
                "Day 5 — Customization:\n- Root filesystem\n- Bootloader patches"
            ),
            source_ref="courses/example/overview.md",
            metadata={"authority": "approved_course_files"},
        )
        agenda_chunks = [
            chunk
            for chunk in chunk_document(document)
            if chunk.metadata.get("section_type") == "agenda"
        ]
        self.assertEqual(3, len(agenda_chunks))
        self.assertEqual(
            (1, 2), (agenda_chunks[0].metadata["day_start"], agenda_chunks[0].metadata["day_end"])
        )
        self.assertEqual(
            (5, 5), (agenda_chunks[-1].metadata["day_start"], agenda_chunks[-1].metadata["day_end"])
        )

    def test_chunking_preserves_commercial_number_and_date_formatting(self):
        document = CorpusDocument(
            document_id="doc-format",
            title="Commercial facts",
            text="# Facts\nThe fee is RM1,000 and the dates are 1–2 September 2026.",
            source_ref="approved/format-v1",
        )
        chunks = chunk_document(document)
        self.assertEqual(1, len(chunks))
        self.assertIn("RM1,000", chunks[0].text)
        self.assertIn("1–2 September", chunks[0].text)

    def test_plain_course_syllabus_heading_is_a_topics_section(self):
        document = CorpusDocument(
            document_id="course-syllabus",
            title="Course overview",
            text=(
                "Course syllabus:\n"
                "A practical course covering boot diagnosis, kernel crashes, profiling, "
                "memory analysis, hardware troubleshooting, and fault-based labs."
            ),
            source_ref="courses/example/overview.md",
        )
        chunks = chunk_document(document)
        self.assertEqual("topics", chunks[0].metadata["section_type"])
        self.assertEqual("Course syllabus", chunks[0].title)

    def test_session_sections_are_independent_chunks_with_session_metadata(self):
        document = CorpusDocument(
            document_id="session-outline",
            title="Course outline",
            text=(
                "Session 1 (4 hours): C Basics and Storage Classes\n"
                "Data types, qualifiers, operators, and practical storage-class exercises.\n\n"
                "Session 2 (4 hours): Pointers and User-Defined Types\n"
                "Pointers, dereferencing, strings, structures, unions, and practical exercises."
            ),
            source_ref="approved/session-outline.pdf",
            metadata={"day_mapping": "not_explicit"},
        )
        chunks = chunk_document(document)
        self.assertEqual([1, 2], [chunk.metadata["session_start"] for chunk in chunks])
        self.assertTrue(all(chunk.metadata["section_type"] == "agenda" for chunk in chunks))
        self.assertTrue(all(chunk.metadata["day_mapping"] == "not_explicit" for chunk in chunks))
        self.assertTrue(all("day_start" not in chunk.metadata for chunk in chunks))
        self.assertEqual(4, chunks[0].metadata["duration_hours"])

    def test_chunks_keep_metadata_and_have_stable_ids(self):
        document = CorpusDocument(
            document_id="doc-1",
            title="Example",
            text="# First\n" + "alpha detail " * 80 + "\n# Second\n" + "beta detail " * 40,
            source_ref="approved/example-v1",
            course_id="alpha",
            topic="outline",
        )
        config = ChunkingConfig(max_tokens=60, overlap_tokens=10, min_tokens=5)
        first = chunk_document(document, config)
        second = chunk_document(document, config)
        self.assertGreater(len(first), 2)
        self.assertEqual([chunk.chunk_id for chunk in first], [chunk.chunk_id for chunk in second])
        self.assertTrue(all(chunk.course_id == "alpha" for chunk in first))
        self.assertTrue(all(chunk.source_ref == "approved/example-v1" for chunk in first))


class CorpusTests(unittest.TestCase):
    def test_flyer_intake_filter_keeps_active_row_and_removes_stale_row(self):
        pages = [
            (
                1,
                "\n".join(
                    (
                        "TRAINING SCHEDULE",
                        "Petaling Jaya 6-9 July 2026 Ibis PJCC Petaling Jaya",
                        "Petaling Jaya 17 - 20 August 2026 Ibis PJCC Petaling Jaya",
                        "WHO SHOULD ATTEND",
                        "QA Engineers and Automation Engineers",
                    )
                ),
            )
        ]
        filtered, metadata = _filter_flyer_intake_rows(
            pages, active_dates="17–20 August 2026"
        )
        text = filtered[0][1]
        self.assertNotIn("6-9 July 2026", text)
        self.assertIn("17 - 20 August 2026", text)
        self.assertEqual("filtered_to_active_intake", metadata["intake_filter_status"])
        self.assertEqual(1, metadata["stale_schedule_rows_removed"])
        self.assertEqual(1, metadata["active_schedule_rows"])

    def test_pdf_structure_preserves_sessions_without_inventing_days(self):
        footer = "Timmins Training Consulting Sdn. Bhd,"
        contact = "Office: +60.3.2785.0737 Mobile: +60 11-1667 4727 email: raj@consult-timmins.com"
        pages = [
            (
                1,
                "\n".join(
                    (
                        "Duration : 14 hours (2 days)",
                        "Course Outline:",
                        "Session 1 (4 hours): C Basics",
                        "Data types, qualifiers, and operators with a practical exercise.",
                        "Session 2 (4 hours): Pointers",
                        "Pointers, strings, structures, and unions with a practical exercise.",
                        footer,
                        contact,
                    )
                ),
            ),
            (
                2,
                "\n".join(
                    (
                        "Session 3 (4 hours): Functions",
                        "Functions, arguments, return values, and preprocessing exercises.",
                        "Session 4 (4 hours): Debugging",
                        "Breakpoints, watchpoints, stack inspection, and GDB exercises.",
                        footer,
                        contact,
                    )
                ),
            ),
        ]
        sections, schedule = _structured_pdf_sections(pages, default_title="Course")
        sessions = [section for section in sections if "session_start" in section.metadata]
        self.assertEqual([1, 2, 3, 4], [section.metadata["session_start"] for section in sessions])
        self.assertTrue(all("day_start" not in section.metadata for section in sessions))
        self.assertEqual("not_explicit", schedule["day_mapping"])
        self.assertEqual(16, schedule["session_hours_total"])
        self.assertEqual("conflict", schedule["duration_status"])
        self.assertNotIn("raj@consult", "\n".join(section.body for section in sections))

    def test_pdf_structure_propagates_only_explicit_day_to_sessions(self):
        pages = [
            (
                1,
                "\n".join(
                    (
                        "Course Duration: 14 Hours (2 Days)",
                        "Course Outline",
                        "Day 1 - 9:00 AM to 12:30 PM",
                        "Session 1: Kernel Space",
                        "Kernel architecture and a practical module exercise.",
                        "Day 1 - 1:30 PM to 5:00 PM",
                        "Session 2: Kernel Modules",
                        "Loading and unloading modules in a practical lab.",
                    )
                ),
            ),
            (
                2,
                "\n".join(
                    (
                        "Day 2",
                        "Session 3: Character Driver",
                        "Character-driver implementation and testing exercises.",
                    )
                ),
            ),
        ]
        sections, schedule = _structured_pdf_sections(pages, default_title="Course")
        sessions = [section for section in sections if "session_start" in section.metadata]
        self.assertEqual([(1, 1), (1, 1), (2, 2)], [
            (section.metadata["day_start"], section.metadata["day_end"])
            for section in sessions
        ])
        self.assertEqual("explicit", schedule["day_mapping"])

    def test_brs_indexes_only_general_client_qa_sections(self):
        documents = build_brs_documents()
        topics = {document.topic for document in documents}
        self.assertEqual({"company_kb", "operations_kb"}, topics)
        self.assertTrue(any("industries" in document.text.lower() for document in documents))
        self.assertTrue(
            any("after registration" in document.text.lower() for document in documents)
        )
        self.assertFalse(any(document.course_id for document in documents))

    def test_duplicate_content_prefers_the_higher_authority_source(self):
        text = "# Answer\nThis approved answer contains enough words to be safely deduplicated across source files."
        documents = [
            CorpusDocument(
                document_id="pdf-copy",
                title="PDF copy",
                text=text,
                source_ref="flyer.pdf",
                metadata={"authority": "approved_pdf"},
            ),
            CorpusDocument(
                document_id="course-source",
                title="Course source",
                text=text,
                source_ref="courses/example/overview.md",
                metadata={"authority": "approved_course_files"},
            ),
        ]
        chunks = build_chunks(documents)
        self.assertEqual(1, len(chunks))
        self.assertEqual("course-source", chunks[0].document_id)

    def test_jsonl_schema_rejects_duplicate_ids(self):
        record = {
            "document_id": "same",
            "title": "Title",
            "text": "Some approved text",
            "source_ref": "approved/v1",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.jsonl"
            path.write_text(json.dumps(record) + "\n" + json.dumps(record), encoding="utf-8")
            with self.assertRaises(CorpusValidationError):
                load_jsonl_corpus(path)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteVectorStore(Path(self.directory.name) / "index.sqlite")
        self.embedder = TestEmbedder()
        build_index(_documents(), embedder=self.embedder, store=self.store)

    def tearDown(self):
        self.directory.cleanup()

    def test_index_is_persistent_and_model_locked(self):
        reopened = SQLiteVectorStore(self.store.path)
        self.assertEqual(4, reopened.count())
        reopened.assert_compatible(model_id=self.embedder.model_id, dimension=4)
        health = reopened.health()
        self.assertEqual("2", health["document_count"])
        self.assertEqual("4", health["chunk_count"])
        self.assertEqual("sqlite", health["backend"])
        with self.assertRaises(IndexCompatibilityError):
            reopened.assert_compatible(model_id="different-model", dimension=4)

    def test_rebuild_only_embeds_missing_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "index.sqlite")
            embedder = CountingEmbedder()
            build_index(_documents(), embedder=embedder, store=store)
            build_index(_documents(), embedder=embedder, store=store)
            self.assertEqual([4], embedder.document_calls)

    def test_rebuild_refreshes_metadata_without_reembedding_unchanged_text(self):
        base = {
            "document_id": "agenda-metadata",
            "title": "Session 1: Foundations",
            "text": "Session 1: Foundations\nCore concepts and practical foundation exercises.",
            "source_ref": "approved/outline.pdf",
            "course_id": "example",
            "topic": "course_hrdc_outline",
        }
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "index.sqlite")
            embedder = CountingEmbedder()
            build_index(
                [CorpusDocument(**base, metadata={"day_start": 1, "day_end": 9})],
                embedder=embedder,
                store=store,
            )
            build_index(
                [CorpusDocument(**base, metadata={"day_start": 1, "day_end": 1})],
                embedder=embedder,
                store=store,
            )
            stored_chunk, _ = next(iter(store.iter_candidates()))
        self.assertEqual(1, stored_chunk.metadata["day_end"])
        self.assertEqual([1], embedder.document_calls)

    def test_course_filter_prevents_cross_course_retrieval(self):
        retriever = HybridRetriever(
            self.store,
            self.embedder,
            RetrievalConfig(min_score=0.2),
        )
        results = retriever.retrieve(
            "What is the fee?",
            filters=RetrievalFilter(course_id="alpha"),
            top_k=5,
        )
        self.assertTrue(results)
        self.assertTrue(all(result.chunk.course_id == "alpha" for result in results))
        self.assertNotIn("RM2,000", " ".join(result.chunk.text for result in results))

    def test_hybrid_retrieval_selects_matching_course(self):
        retriever = HybridRetriever(
            self.store,
            self.embedder,
            RetrievalConfig(min_score=0.2),
        )
        result = retriever.retrieve("Course Beta price", top_k=1)[0]
        self.assertEqual("beta", result.chunk.course_id)
        self.assertGreater(result.semantic_score, 0.5)
        self.assertGreater(result.lexical_score, 0.0)

    def test_query_expansion_and_trace_are_recorded(self):
        expanded, terms = expand_query("how much is HRDC 10001697599 claimable?")
        self.assertIn("fee", expanded.lower())
        self.assertIn("Modern Software Testing", terms)
        retriever = HybridRetriever(
            self.store,
            self.embedder,
            RetrievalConfig(min_score=0.2),
        )
        trace = {}
        retriever.retrieve("how much is Course Alpha?", top_k=1, trace=trace)
        self.assertIn("expanded_query", trace)
        self.assertEqual(1, trace["selected_count"])
        self.assertIn("semantic_score", trace["selected"][0])

    def test_installment_query_expansion(self):
        expanded, terms = expand_query("installment payment")
        self.assertIn("payment", expanded.lower())
        self.assertIn("bank transfer", terms)

    def test_syllabus_query_expansion_includes_key_topics(self):
        expanded, terms = expand_query("syllabus")
        self.assertIn("key topics", expanded.lower())

    def test_session_query_normalization_and_exact_metadata_ranking(self):
        self.assertEqual(3, requested_session("what is in the third session?"))
        document = CorpusDocument(
            document_id="sessions",
            title="Course outline",
            text=(
                "Session 1: Foundations\nCore concepts and practical foundation exercises.\n\n"
                "Session 3: Debugging\nBreakpoints, stack inspection, and practical debugging exercises."
            ),
            source_ref="approved/sessions.pdf",
            course_id="example",
            topic="course_hrdc_outline",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "sessions.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            trace = {}
            results = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "what is covered in session three",
                filters=RetrievalFilter(course_id="example"),
                top_k=2,
                trace=trace,
            )
        self.assertEqual(3, results[0].chunk.metadata["session_start"])
        self.assertEqual(3, trace["requested_session"])
        self.assertGreater(results[0].metadata_score, results[1].metadata_score)

    def test_requested_day_metadata_promotes_the_matching_agenda(self):
        document = CorpusDocument(
            document_id="course-agenda",
            title="Agenda",
            text=(
                "Days 1–2 — Foundations:\nBoot flow, U-Boot, and Linux kernel exercises.\n\n"
                "Day 5 — Customization:\nRoot filesystem packages and production image customization."
            ),
            source_ref="courses/example/overview.md",
            course_id="example",
            topic="course_overview",
            metadata={"authority": "approved_course_files"},
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "agenda.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            trace = {}
            result = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "what is on day one", top_k=1, trace=trace
            )[0]
        self.assertEqual(1, result.chunk.metadata["day_start"])
        self.assertEqual(2, result.chunk.metadata["day_end"])
        self.assertGreater(result.metadata_score, 0)
        self.assertEqual(1, trace["requested_day"])

    def test_day_by_day_query_promotes_all_agenda_sections(self):
        document = CorpusDocument(
            document_id="full-agenda",
            title="Agenda",
            text=(
                "Days 1–2 — Foundations:\nBoot flow and Linux kernel exercises.\n\n"
                "Days 3–4 — Build system:\nBitBake recipes and custom layers.\n\n"
                "Day 5 — Customization:\nRoot filesystem and bootloader patches."
            ),
            source_ref="courses/example/overview.md",
            course_id="example",
            topic="course_overview",
            metadata={"authority": "approved_course_files"},
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "agenda.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            results = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "show me the day-by-day breakdown", top_k=3
            )
        self.assertEqual({1, 3, 5}, {item.chunk.metadata["day_start"] for item in results})
        self.assertTrue(all(item.metadata_score > 0 for item in results))

    def test_breakdown_returns_every_session_even_below_top_k(self):
        # Regression: a syllabus/breakdown request is a LIST — it must not silently drop
        # sessions when the per-session chunks lose the top-k contest. Here top_k=2 is
        # smaller than the four sessions; every one must still come back.
        document = CorpusDocument(
            document_id="session-outline",
            title="Course outline",
            text=(
                "Session 1 (4 hours): C Basics\nData types and storage classes.\n\n"
                "Session 2 (4 hours): Pointers\nReferencing and user-defined types.\n\n"
                "Session 3 (4 hours): Functions\nModular code and preprocessing.\n\n"
                "Session 4 (4 hours): Debugging\nGDB breakpoints and stack inspection."
            ),
            source_ref="approved/outline.pdf",
            course_id="example",
            topic="course_hrdc_outline",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "outline.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            results = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "give me the full detailed breakdown",
                filters=RetrievalFilter(course_id="example"),
                top_k=2,
            )
        self.assertEqual({1, 2, 3, 4}, {r.chunk.metadata["session_start"] for r in results})

    def test_multi_part_query_retrieves_fee_and_schedule(self):
        retriever = HybridRetriever(
            self.store,
            self.embedder,
            RetrievalConfig(min_score=0.2),
        )
        results = retriever.retrieve(
            "What is the price and when is it scheduled?",
            filters=RetrievalFilter(course_id="alpha"),
            top_k=3,
        )
        titles = {result.chunk.title for result in results}
        self.assertEqual({"Fees", "Schedule"}, titles)

    def test_broad_syllabus_query_can_use_more_than_three_sections_from_one_source(self):
        document = CorpusDocument(
            document_id="structured-overview",
            title="Course overview",
            text="\n\n".join(
                f"# Module {index}\nCourse syllabus topic {index} includes practical exercise {index}."
                for index in range(1, 7)
            ),
            source_ref="courses/example/overview.md",
            course_id="example",
            topic="course_overview",
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "syllabus.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            results = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "course syllabus, curriculum, and covered topics",
                filters=RetrievalFilter(course_id="example"),
                top_k=5,
            )
        self.assertEqual(5, len(results))

    def test_empty_scope_returns_no_results(self):
        retriever = HybridRetriever(self.store, self.embedder)
        results = retriever.retrieve(
            "What is the fee?", filters=RetrievalFilter(course_id="missing")
        )
        self.assertEqual([], results)

    def test_intent_dependent_retrieval_budget(self):
        self.assertEqual(3, _retrieval_budget("what is the fee", course=object()))
        self.assertGreaterEqual(_retrieval_budget("compare curriculum and topics"), 7)
        self.assertGreaterEqual(_retrieval_budget("what is the day 1 syllabus", course=object()), 7)

    def test_syllabus_query_promotes_topics_section(self):
        document = CorpusDocument(
            document_id="course-with-topics",
            title="Course overview",
            text=(
                "# Key topics\n"
                "Embedded Linux boot flow, Yocto Project architecture, BitBake recipes, "
                "meta layers, BSP customization, kernel configuration, root filesystem.\n\n"
                "# Q1. Is this hands-on?\nAnswer: Yes, approximately 90% hands-on labs.\n\n"
                "# Q2. What tools are used?\nAnswer: Yocto, BitBake, U-Boot, Linux Kernel."
            ),
            source_ref="courses/example/overview.md",
            course_id="example",
            topic="course_overview",
            metadata={"authority": "approved_course_files"},
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "syllabus.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            results = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "syllabus",
                filters=RetrievalFilter(course_id="example"),
                top_k=5,
            )
        self.assertTrue(results)
        self.assertEqual("topics", results[0].chunk.metadata["section_type"])
        self.assertGreater(results[0].metadata_score, 0)

    def test_day_query_promotes_matching_agenda_over_qa(self):
        document = CorpusDocument(
            document_id="day-test",
            title="Course overview",
            text=(
                "Days 1–2 — Foundations:\nBoot flow and Linux kernel exercises.\n\n"
                "Day 5 — Customization:\nRoot filesystem and bootloader patches.\n\n"
                "Q1. Is this hands-on?\nAnswer: Yes, 90% hands-on labs.\n\n"
                "Q2. What tools are used?\nAnswer: Yocto and BitBake."
            ),
            source_ref="courses/example/overview.md",
            course_id="example",
            topic="course_overview",
            metadata={"authority": "approved_course_files"},
        )
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteVectorStore(Path(directory) / "day-test.sqlite")
            embedder = NeutralEmbedder()
            build_index([document], embedder=embedder, store=store)
            results = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.2)).retrieve(
                "what is on day one",
                filters=RetrievalFilter(course_id="example"),
                top_k=5,
            )
        self.assertTrue(results)
        self.assertEqual("agenda", results[0].chunk.metadata["section_type"])
        self.assertEqual(1, results[0].chunk.metadata["day_start"])
        self.assertGreater(results[0].metadata_score, 0)


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        store = SQLiteVectorStore(Path(self.directory.name) / "index.sqlite")
        embedder = TestEmbedder()
        build_index(_documents(), embedder=embedder, store=store)
        self.retriever = HybridRetriever(
            store,
            embedder,
            RetrievalConfig(min_score=0.2),
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_course_scope_is_required_before_retrieval(self):
        service = RagService(self.retriever, FirstEvidenceGenerator())
        answer = service.answer(RagRequest("What is the fee?", require_course_scope=True))
        self.assertEqual("clarify", answer.status)
        self.assertIn("Which course", answer.text)

    def test_standalone_retrieval_query_is_distinct_from_customer_wording(self):
        service = RagService(self.retriever, FirstEvidenceGenerator())
        answer = service.answer(
            RagRequest(
                question="syllabus",
                retrieval_query="Course Alpha fee",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            )
        )
        self.assertEqual("answered", answer.status)
        self.assertIn("Course Alpha", answer.text)

    def test_unknown_citation_is_rejected_and_abstains(self):
        service = RagService(self.retriever, InvalidCitationGenerator())
        answer = service.answer(
            RagRequest(
                "What is the fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
                require_course_scope=True,
            )
        )
        self.assertEqual("not_found", answer.status)
        self.assertIn("verified information", answer.text)
        self.assertIn("unknown evidence", answer.reason)

    def test_provider_failure_is_not_misreported_as_missing_course_data(self):
        from rag_v2.models import GroundedAnswer

        class FailedProvider:
            def generate(self, request, evidence):
                del request
                return GroundedAnswer(
                    "rejected",
                    "",
                    sources=tuple(evidence),
                    reason="bedrock generation failure: ModuleNotFoundError",
                )

        service = RagService(self.retriever, FailedProvider())
        answer = service.answer(
            RagRequest(
                "What is the fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
                require_course_scope=True,
            )
        )

        self.assertEqual("rejected", answer.status)
        self.assertNotIn("verified information", answer.text)

    def test_irrelevant_question_abstains_before_generation(self):
        service = RagService(self.retriever, FailIfCalledGenerator())
        answer = service.answer(
            RagRequest(
                "Do you provide airport pickup and a free hotel?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            )
        )
        self.assertEqual("not_found", answer.status)
        self.assertEqual("no evidence above retrieval threshold", answer.reason)

    def test_valid_claim_requires_known_citation(self):
        evidence = self.retriever.retrieve(
            "Course Alpha fee", filters=RetrievalFilter(course_id="alpha"), top_k=1
        )
        chunk_id = evidence[0].chunk.chunk_id
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Course Alpha costs RM1,000 per participant.",
                        "source_ids": [chunk_id],
                    }
                ],
                "clarification_question": None,
            },
            evidence,
        )
        self.assertEqual("answered", answer.status)
        self.assertTrue(answer.grounded)


class RuntimeWrapperTests(unittest.TestCase):
    def test_answer_accepts_retrieval_query(self):
        import inspect

        from rag_v2.runtime import answer

        signature = inspect.signature(answer)
        self.assertIn("retrieval_query", signature.parameters)

    def test_openrouter_generation_settings_accept_existing_key_name(self):
        from rag_v2.runtime import _generation_settings

        with patch.dict(
            os.environ,
            {
                "RAG_GENERATION_PROVIDER": "openrouter",
                "RAG_GENERATION_MODEL": "openai/gpt-oss-120b",
                "OPEN_ROUTER_API_KEY": "test-openrouter-key",
            },
            clear=False,
        ):
            provider, api_key, model = _generation_settings()

        self.assertEqual("openrouter", provider)
        self.assertEqual("test-openrouter-key", api_key)
        self.assertEqual("openai/gpt-oss-120b", model)

    def test_bedrock_generation_settings_use_region_and_aws_credential_chain(self):
        from rag_v2.runtime import _generation_settings

        with patch.dict(
            os.environ,
            {
                "RAG_GENERATION_PROVIDER": "bedrock",
                "RAG_GENERATION_MODEL": "",
                "BEDROCK_REGION": "ap-south-1",
            },
            clear=False,
        ):
            provider, credential_marker, model = _generation_settings()

        self.assertEqual("bedrock", provider)
        self.assertEqual("aws:ap-south-1", credential_marker)
        self.assertEqual("openai.gpt-oss-120b-1:0", model)


if __name__ == "__main__":
    unittest.main()
