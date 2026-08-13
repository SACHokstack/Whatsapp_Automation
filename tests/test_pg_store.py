"""Postgres vector store tests.

Gated on RAG_TEST_POSTGRES_URL (a disposable Postgres), so CI without a database skips them.
Locally:  docker run -d -e POSTGRES_PASSWORD=test -e POSTGRES_DB=ragtest -p 5433:5432 postgres:16
          RAG_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:5433/ragtest pytest tests/test_pg_store.py
"""

import os
import unittest

from rag_v2.models import Chunk, RetrievalFilter

PG_URL = os.getenv("RAG_TEST_POSTGRES_URL", "").strip()


@unittest.skipUnless(PG_URL, "set RAG_TEST_POSTGRES_URL to run the Postgres vector store tests")
class PostgresVectorStoreTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        with psycopg.connect(PG_URL) as conn, conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS rag_chunks; DROP TABLE IF EXISTS rag_index_metadata;")
            conn.commit()
        from rag_v2.pg_store import PostgresVectorStore

        self.store = PostgresVectorStore(PG_URL)
        self.store.initialize(model_id="test-model", dimension=3)

    def _chunk(self, cid, course, topic="course_facts"):
        return Chunk(
            chunk_id=cid,
            document_id="d-" + cid,
            title="T",
            text=f"text {cid}",
            source_ref="s",
            ordinal=0,
            course_id=course,
            topic=topic,
            metadata={"k": cid},
        )

    def test_sync_roundtrip_and_filtering(self):
        chunks = [self._chunk("c1", "alpha"), self._chunk("c2", "beta")]
        self.store.sync(
            chunks, {"c1": [1, 0, 0], "c2": [0, 1, 0]}, document_count=2, corpus_version="v1"
        )

        self.assertEqual(2, self.store.count())
        self.assertEqual({"c1", "c2"}, self.store.chunk_ids())
        self.assertEqual("postgres", self.store.metadata()["backend"])

        alpha = list(self.store.iter_candidates(RetrievalFilter(course_id="alpha")))
        self.assertEqual(1, len(alpha))
        chunk, vector = alpha[0]
        self.assertEqual("c1", chunk.chunk_id)
        self.assertEqual({"k": "c1"}, chunk.metadata)  # jsonb round-trips
        self.assertAlmostEqual(1.0, sum(v * v for v in vector) ** 0.5, places=5)  # normalized

    def test_sync_prunes_removed_chunks(self):
        self.store.sync(
            [self._chunk("c1", "alpha"), self._chunk("c2", "alpha")],
            {"c1": [1, 0, 0], "c2": [0, 1, 0]},
            document_count=1,
            corpus_version="v1",
        )
        self.assertEqual({"c1", "c2"}, self.store.chunk_ids())
        # c2 gone from the corpus, c1 kept (no new embedding => reuse), c3 added
        self.store.sync(
            [self._chunk("c1", "alpha"), self._chunk("c3", "alpha")],
            {"c3": [0, 0, 1]},
            document_count=1,
            corpus_version="v2",
        )
        self.assertEqual({"c1", "c3"}, self.store.chunk_ids())

    def test_incompatible_model_is_rejected(self):
        from rag_v2.pg_store import PostgresVectorStore
        from rag_v2.store import IndexCompatibilityError

        other = PostgresVectorStore(PG_URL)
        with self.assertRaises(IndexCompatibilityError):
            other.initialize(model_id="DIFFERENT-model", dimension=3)


if __name__ == "__main__":
    unittest.main()
