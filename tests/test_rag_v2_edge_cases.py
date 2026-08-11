from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rag_v2.generation import (
    BedrockGroundedGenerator,
    OpenRouterGroundedGenerator,
    validate_generation,
    wants_detail_offer,
    wants_detailed_response,
)
from rag_v2.ingestion import build_index
from rag_v2.models import Chunk, CorpusDocument, RagRequest, RetrievalFilter, SearchResult
from rag_v2.retriever import HybridRetriever, RetrievalConfig
from rag_v2.service import RagService
from rag_v2.store import SQLiteVectorStore


class EdgeEmbedder:
    model_id = "test:edge-semantic-v1"
    dimension = 5

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        lower = text.lower()
        values = (
            float(sum(term in lower for term in ("alpha", "rm1,000", "penang"))),
            float(sum(term in lower for term in ("beta", "rm2,000", "kuala"))),
            float(sum(term in lower for term in ("fee", "price", "cost", "rm"))),
            float(sum(term in lower for term in ("date", "schedule", "september"))),
            float(sum(term in lower for term in ("email", "contact", "@"))),
        )
        if not any(values):
            return (0.0, 0.0, 0.0, 0.0, -1.0)
        magnitude = sum(value * value for value in values) ** 0.5
        return tuple(value / magnitude for value in values)

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)


class PayloadGenerator:
    def __init__(self, payload):
        self.payload = payload

    def generate(self, request, evidence):
        return validate_generation(self.payload, evidence)


def _documents() -> list[CorpusDocument]:
    return [
        CorpusDocument(
            document_id="alpha-commercial",
            title="Course Alpha commercial facts",
            text=(
                "# Fees\nCourse Alpha costs RM1,000 per participant.\n\n"
                "# Schedule\nCourse Alpha runs on 1–2 September 2026 in Penang."
            ),
            source_ref="approved/alpha-v1",
            course_id="alpha",
            topic="commercial",
            metadata={"authority": "approved"},
        ),
        CorpusDocument(
            document_id="beta-commercial",
            title="Course Beta commercial facts",
            text=(
                "# Fees\nCourse Beta costs RM2,000 per participant.\n\n"
                "# Schedule\nCourse Beta runs on 8–9 September 2026 in Kuala Lumpur."
            ),
            source_ref="approved/beta-v1",
            course_id="beta",
            topic="commercial",
            metadata={"authority": "approved"},
        ),
        CorpusDocument(
            document_id="company-contact",
            title="Company contact",
            text="# Contact\nCustomers can contact Timmins at info@timmins-consulting.com.",
            source_ref="approved/company-v1",
            topic="company",
            metadata={"authority": "approved"},
        ),
    ]


class RagV2ProductionEdgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteVectorStore(Path(self.directory.name) / "index.sqlite")
        self.embedder = EdgeEmbedder()
        build_index(_documents(), embedder=self.embedder, store=self.store)
        self.retriever = HybridRetriever(
            self.store,
            self.embedder,
            RetrievalConfig(min_score=0.2),
        )

    def tearDown(self):
        self.directory.cleanup()

    def _alpha_fee_evidence(self):
        evidence = self.retriever.retrieve(
            "Course Alpha fee and schedule",
            filters=RetrievalFilter(course_id="alpha", topics=("commercial",)),
            top_k=3,
        )
        self.assertTrue(evidence)
        return evidence

    @patch("requests.post")
    def test_openrouter_generator_uses_gpt_oss_structured_output(self, post):
        evidence = self._alpha_fee_evidence()
        chunk_id = self._source_id_with(evidence, "RM1,000")
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "status": "answered",
                                "claims": [
                                    {
                                        "text": "Course Alpha costs RM1,000 per participant.",
                                        "source_ids": [chunk_id],
                                    }
                                ],
                                "clarification_question": None,
                            }
                        )
                    }
                }
            ]
        }
        post.return_value = response
        generator = OpenRouterGroundedGenerator(api_key="test-openrouter-key")

        answer = generator.generate(
            RagRequest(
                "What is the fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            ),
            evidence,
        )

        self.assertEqual("answered", answer.status)
        _, kwargs = post.call_args
        self.assertEqual("openai/gpt-oss-120b", kwargs["json"]["model"])
        self.assertEqual("json_schema", kwargs["json"]["response_format"]["type"])
        self.assertEqual({"effort": "low", "exclude": True}, kwargs["json"]["reasoning"])
        self.assertEqual("Bearer test-openrouter-key", kwargs["headers"]["Authorization"])

    def test_bedrock_generator_uses_converse_schema_and_validates_evidence(self):
        evidence = self._alpha_fee_evidence()
        chunk_id = self._source_id_with(evidence, "RM1,000")
        payload = {
            "status": "answered",
            "claims": [
                {
                    "text": "Course Alpha costs RM1,000 per participant.",
                    "source_ids": [chunk_id],
                }
            ],
            "clarification_question": None,
        }
        client = Mock()
        client.converse.return_value = {
            "output": {
                "message": {
                    "content": [
                        {"reasoningContent": {"reasoningText": {"text": "internal"}}},
                        # GPT-OSS on Bedrock can prepend one extra constrained-
                        # output brace; the parser repairs this exact shape.
                        {"text": "{\n" + json.dumps(payload)},
                    ]
                }
            }
        }
        generator = BedrockGroundedGenerator(
            model="openai.gpt-oss-120b-1:0",
            region="ap-south-1",
            client=client,
        )

        answer = generator.generate(
            RagRequest(
                "What is the fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            ),
            evidence,
        )

        self.assertEqual("answered", answer.status)
        kwargs = client.converse.call_args.kwargs
        self.assertEqual("openai.gpt-oss-120b-1:0", kwargs["modelId"])
        self.assertEqual("json_schema", kwargs["outputConfig"]["textFormat"]["type"])
        schema = kwargs["outputConfig"]["textFormat"]["structure"]["jsonSchema"]
        self.assertEqual("grounded_answer", schema["name"])
        self.assertEqual("object", json.loads(schema["schema"])["type"])
        self.assertEqual({"reasoning_effort": "low"}, kwargs["additionalModelRequestFields"])
        self.assertEqual(4000, kwargs["inferenceConfig"]["maxTokens"])

    def test_bedrock_failure_rejects_without_unverified_fallback(self):
        client = Mock()
        client.converse.side_effect = RuntimeError("bedrock unavailable")
        evidence = self._alpha_fee_evidence()

        answer = BedrockGroundedGenerator(client=client).generate(
            RagRequest(
                "What is the fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            ),
            evidence,
        )

        self.assertEqual("rejected", answer.status)
        self.assertEqual("bedrock generation failure: RuntimeError", answer.reason)

    def test_generator_forbids_unverified_session_to_day_mapping(self):
        chunk = Chunk(
            chunk_id="session-1",
            document_id="outline",
            title="Session 1: Foundations",
            text="Session 1: Foundations\nCore concepts and practical foundation exercises.",
            source_ref="approved/outline.pdf#page=2",
            ordinal=0,
            course_id="alpha",
            topic="course_hrdc_outline",
            metadata={
                "section_type": "agenda",
                "session_start": 1,
                "session_end": 1,
                "day_mapping": "not_explicit",
                "declared_hours": 14,
                "declared_days": 2,
                "session_hours_total": 16,
                "duration_status": "conflict",
            },
        )
        evidence = [SearchResult(chunk, 0.8, 0.7, 0.5, 0.08)]
        prompt = OpenRouterGroundedGenerator(api_key="unused")._user_prompt(
            RagRequest(
                "What is the Day 1 syllabus?",
                retrieval_query="day 1 syllabus",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            ),
            evidence,
        )
        self.assertIn("does not explicitly map its sessions to Day 1", prompt)
        self.assertIn("Do not assign sessions to Day 1", prompt)
        self.assertIn("DURATION_STATUS: conflict", prompt)

    @patch("requests.post")
    def test_openrouter_rate_limit_rejects_without_unverified_fallback(self, post):
        post.return_value = Mock(status_code=429)
        evidence = self._alpha_fee_evidence()

        answer = OpenRouterGroundedGenerator(api_key="test-openrouter-key").generate(
            RagRequest(
                "What is the fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha"),
            ),
            evidence,
        )

        self.assertEqual("rejected", answer.status)
        self.assertEqual("openrouter HTTP 429", answer.reason)

    @staticmethod
    def _source_id_with(evidence, text: str) -> str:
        lowered = text.lower()
        for result in evidence:
            if lowered in result.chunk.text.lower():
                return result.chunk.chunk_id
        raise AssertionError(f"no evidence chunk contains {text!r}")

    def test_rejects_money_claim_not_present_in_cited_evidence(self):
        evidence = self._alpha_fee_evidence()
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Course Alpha costs RM9,999 per participant.",
                        "source_ids": [evidence[0].chunk.chunk_id],
                    }
                ],
            },
            evidence,
        )
        self.assertEqual("rejected", answer.status)
        self.assertIn("unsupported", answer.reason)

    def test_rejects_date_claim_not_present_in_cited_evidence(self):
        evidence = self._alpha_fee_evidence()
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Course Alpha runs on 10–11 September 2026.",
                        "source_ids": [evidence[0].chunk.chunk_id],
                    }
                ],
            },
            evidence,
        )
        self.assertEqual("rejected", answer.status)
        self.assertIn("unsupported", answer.reason)

    def test_rejects_contact_claim_not_present_in_cited_evidence(self):
        evidence = self.retriever.retrieve(
            "Timmins contact email",
            filters=RetrievalFilter(topics=("company",)),
            top_k=2,
        )
        self.assertTrue(evidence)
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "You can email Timmins at sales@example.com.",
                        "source_ids": [evidence[0].chunk.chunk_id],
                    }
                ],
            },
            evidence,
        )
        self.assertEqual("rejected", answer.status)
        self.assertIn("unsupported", answer.reason)

    def test_rejects_cross_course_claim_with_valid_but_wrong_citation(self):
        evidence = self._alpha_fee_evidence()
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Course Beta costs RM2,000 per participant.",
                        "source_ids": [evidence[0].chunk.chunk_id],
                    }
                ],
            },
            evidence,
        )
        self.assertEqual("rejected", answer.status)
        self.assertIn("unsupported", answer.reason)

    def test_accepts_supported_high_risk_claims_when_values_are_in_evidence(self):
        evidence = self._alpha_fee_evidence()
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Course Alpha costs RM1,000 per participant.",
                        "source_ids": [self._source_id_with(evidence, "RM1,000")],
                    },
                    {
                        "text": "Course Alpha runs on 1–2 September 2026 in Penang.",
                        "source_ids": [self._source_id_with(evidence, "1–2 September 2026")],
                    },
                ],
            },
            evidence,
        )
        self.assertEqual("answered", answer.status)
        self.assertTrue(answer.grounded)
        self.assertTrue(answer.text.startswith("Course Alpha costs"))
        self.assertIn("\n\n• Course Alpha runs", answer.text)

    def test_default_answer_has_overview_then_up_to_four_bullets(self):
        evidence = self._alpha_fee_evidence()
        source_id = evidence[0].chunk.chunk_id
        claims = [
            {"text": f"Supported learning outcome {index}.", "source_ids": [source_id]}
            for index in range(1, 7)
        ]
        answer = validate_generation({"status": "answered", "claims": claims}, evidence)
        self.assertTrue(answer.text.startswith("Supported learning outcome 1."))
        self.assertEqual(4, sum(line.startswith("• ") for line in answer.text.splitlines()))
        self.assertIn("outcome 5", answer.text)
        self.assertNotIn("outcome 6", answer.text)

    def test_detailed_request_can_return_more_bullets(self):
        evidence = self._alpha_fee_evidence()
        source_id = evidence[0].chunk.chunk_id
        claims = [
            {"text": f"Supported learning outcome {index}.", "source_ids": [source_id]}
            for index in range(1, 6)
        ]
        answer = validate_generation(
            {"status": "answered", "claims": claims}, evidence, detailed=True
        )
        self.assertEqual(4, sum(line.startswith("• ") for line in answer.text.splitlines()))
        self.assertIn("outcome 5", answer.text)
        self.assertTrue(wants_detailed_response("Please give me a detailed breakdown"))
        self.assertFalse(wants_detailed_response("What will I learn?"))

    def test_concise_syllabus_answer_offers_an_in_depth_followup(self):
        evidence = self._alpha_fee_evidence()
        source_id = evidence[0].chunk.chunk_id
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "The course provides a practical syllabus overview.",
                        "source_ids": [source_id],
                    },
                    {
                        "text": "It covers supported course material.",
                        "source_ids": [source_id],
                    },
                ],
            },
            evidence,
            offer_details=True,
        )
        self.assertIn("ask and I can explain the syllabus in depth", answer.text)
        self.assertTrue(wants_detail_offer("give me the price and syllabus"))

    def test_detailed_syllabus_answer_does_not_repeat_detail_offer(self):
        evidence = self._alpha_fee_evidence()
        source_id = evidence[0].chunk.chunk_id
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "This is the requested detailed syllabus.",
                        "source_ids": [source_id],
                    }
                ],
            },
            evidence,
            detailed=True,
            offer_details=True,
        )
        self.assertNotIn("ask and I can explain", answer.text)

    def test_rejects_syllabus_misattributed_to_another_course(self):
        evidence = self._alpha_fee_evidence()
        source_id = evidence[0].chunk.chunk_id
        answer = validate_generation(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": (
                            "Embedded Linux System Internals course syllabus covers system "
                            "internals and debugging."
                        ),
                        "source_ids": [source_id],
                    }
                ],
            },
            evidence,
            expected_course_id="embedded-linux-debugging-aug-2026",
            question="give me the syllabus of this course",
        )
        self.assertEqual("rejected", answer.status)
        self.assertIn("misattributes", answer.reason)

    def test_service_abstains_when_generator_returns_unsupported_claim(self):
        evidence = self._alpha_fee_evidence()
        generator = PayloadGenerator(
            {
                "status": "answered",
                "claims": [
                    {
                        "text": "Course Alpha costs RM9,999 per participant.",
                        "source_ids": [evidence[0].chunk.chunk_id],
                    }
                ],
            }
        )
        service = RagService(self.retriever, generator)
        answer = service.answer(
            RagRequest(
                "What is the Course Alpha fee?",
                retrieval_filter=RetrievalFilter(course_id="alpha", topics=("commercial",)),
                require_course_scope=True,
            )
        )
        self.assertEqual("not_found", answer.status)
        self.assertIn("verified information", answer.text)


if __name__ == "__main__":
    unittest.main()
