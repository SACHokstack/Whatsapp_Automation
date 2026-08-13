"""P2: course CRUD, document ingestion, and the scoped-prune guarantee.

The load-bearing test here is `test_upload_is_searchable_without_a_restart` — the whole point of
the dashboard is that a client can upload a document and have the bot answer from it, with no
redeploy. The second is `test_ingesting_one_course_leaves_other_chunks_alone`: the boot path's
`sync()` prunes globally, so if the per-course write ever did the same it would silently wipe the
rest of the index.

Uses the hashing embedder so no model has to load. That is selected per-test via
`_HashingEmbedderMixin` rather than at import time — setting RAG_EMBEDDER globally would
follow the whole test session into other modules and quietly swap the embedder they run
against.
"""

import os
import unittest
import unittest.mock

from rag_v2.embeddings import configured_embedder
from rag_v2.models import Chunk
from rag_v2.store import SQLiteVectorStore


class _HashingEmbedderMixin:
    """Run each test against a private, throwaway vector index using the hashing embedder.

    Self-contained on purpose: CI runs `unittest discover`, which does not load pytest's
    conftest, so these tests cannot rely on it to redirect RAG_V2_INDEX. Without this the
    tests would open the real index — built with a different embedding model — and fail with
    IndexCompatibilityError.
    """

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        import rag_v2.runtime as runtime

        index_dir = tempfile.mkdtemp(prefix="p2-rag-")
        self._env = unittest.mock.patch.dict(
            os.environ,
            {
                "RAG_EMBEDDER": "hashing",
                "RAG_V2_INDEX": str(Path(index_dir) / "rag.sqlite"),
                "RAG_STORE": "",  # SQLite store, never the shared Postgres one
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)

        # The runtime caches its store path at construction, so it has to be rebuilt against
        # the patched environment — and put back afterwards for whatever runs next.
        previous_runtime = runtime._runtime
        runtime._runtime = None
        self.addCleanup(setattr, runtime, "_runtime", previous_runtime)
        self.addCleanup(shutil.rmtree, index_dir, ignore_errors=True)
        super().setUp()


DIGITAL_PDF_TEXT = (
    "Timmins Advanced Widget Engineering runs in the Kuala Lumpur lab. "
    "The special laboratory access code for enrolled delegates is ZEBRA-4417. "
    "Delegates must bring a laptop with at least 16GB of memory."
)


def _store(tmp_path):
    return SQLiteVectorStore(tmp_path)


def _chunk(chunk_id, course_id, text="hello world"):
    return Chunk(
        chunk_id=chunk_id,
        document_id=f"doc-{chunk_id}",
        title="t",
        text=text,
        source_ref="ref",
        ordinal=0,
        course_id=course_id,
        topic="course_kb",
    )


class ScopedPruneTests(_HashingEmbedderMixin, unittest.TestCase):
    """sync_course must never touch another course's chunks, or the catalog's."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        super().setUp()  # selects the hashing embedder before one is constructed
        self.dir = tempfile.mkdtemp(prefix="p2-store-")
        self.store = _store(Path(self.dir) / "index.sqlite")
        self.embedder = configured_embedder()
        self.store.initialize(model_id=self.embedder.model_id, dimension=self.embedder.dimension)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, chunks):
        vectors = self.embedder.embed_documents([c.text for c in chunks])
        embeddings = {c.chunk_id: v for c, v in zip(chunks, vectors, strict=True)}
        return embeddings

    def test_ingesting_one_course_leaves_other_chunks_alone(self):
        alpha = [_chunk("a1", "alpha"), _chunk("a2", "alpha")]
        beta = [_chunk("b1", "beta")]
        catalog = [_chunk("c1", None)]  # course_id IS NULL — the catalog/company chunks
        for group in (alpha, beta, catalog):
            course_id = group[0].course_id
            self.store.sync_course(course_id, group, self._write(group))

        self.assertEqual({"a1", "a2", "b1", "c1"}, self.store.chunk_ids())

        # Re-sync alpha with a different chunk set: only alpha's chunks may change.
        replacement = [_chunk("a3", "alpha")]
        self.store.sync_course("alpha", replacement, self._write(replacement))

        self.assertEqual({"a3", "b1", "c1"}, self.store.chunk_ids())

    def test_empty_sync_prunes_only_that_course(self):
        alpha = [_chunk("a1", "alpha")]
        beta = [_chunk("b1", "beta")]
        for group in (alpha, beta):
            self.store.sync_course(group[0].course_id, group, self._write(group))

        _, pruned = self.store.sync_course("alpha", [], {})

        self.assertEqual(1, pruned)
        self.assertEqual({"b1"}, self.store.chunk_ids())


class DocExtractTests(unittest.TestCase):
    def test_plain_text_and_markdown(self):
        from services.doc_extract import extract_text

        text, method = extract_text(b"# Title\n\nSome course notes.", "notes.md")
        self.assertEqual("plain", method)
        self.assertIn("Some course notes.", text)

    def test_unsupported_type_is_rejected_clearly(self):
        from services.doc_extract import ExtractionError, extract_text

        with self.assertRaises(ExtractionError) as caught:
            extract_text(b"\x00\x01", "mystery.xyz")
        self.assertIn("unsupported", str(caught.exception).lower())

    def test_empty_file_is_rejected(self):
        from services.doc_extract import ExtractionError, extract_text

        with self.assertRaises(ExtractionError):
            extract_text(b"   ", "empty.txt")


class DocumentQueueTests(unittest.TestCase):
    """The documents table doubles as the ingest queue; claiming must be exactly-once."""

    def setUp(self):
        from services import content_store as cs

        self.cs = cs
        cs.upsert_course("queue-test-course", {"name": "Queue Test", "active": True})
        # Remove the fixture again: the content DB is shared with the rest of the suite, and a
        # stray course would show up in another module's view of the catalogue.
        self.addCleanup(cs.delete_course, "queue-test-course")

    def test_claim_is_exactly_once(self):
        doc_id = self.cs.add_document("queue-test-course", "a.txt", "text/plain", b"hello there")
        first = self.cs.claim_pending_document()
        self.assertIsNotNone(first)
        self.assertEqual(doc_id, int(first["id"]))
        self.assertEqual("extracting", first["ingest_status"])
        # A second worker racing for the same row must come away with nothing.
        self.assertIsNone(self.cs.claim_pending_document())

    def test_status_transitions_and_listing(self):
        doc_id = self.cs.add_document("queue-test-course", "b.txt", "text/plain", b"body text")
        self.cs.set_document_status(
            doc_id, "indexed", extracted_text="body text", extraction_method="plain", chunk_count=3
        )
        row = self.cs.get_document(doc_id)
        self.assertEqual("indexed", row["ingest_status"])
        self.assertEqual(3, row["chunk_count"])
        listed = self.cs.list_documents("queue-test-course")
        self.assertIn(doc_id, [int(r["id"]) for r in listed])
        # Listing must never carry the blob into the dashboard payload.
        self.assertNotIn("raw_bytes", listed[0])

    def test_delete_returns_owning_course(self):
        doc_id = self.cs.add_document("queue-test-course", "c.txt", "text/plain", b"text")
        self.assertEqual("queue-test-course", self.cs.delete_document(doc_id))
        self.assertIsNone(self.cs.get_document(doc_id))


class IngestEndToEndTests(_HashingEmbedderMixin, unittest.TestCase):
    """Upload -> extract -> chunk -> embed -> retrievable, in-process, with no restart."""

    def setUp(self):
        from services import content_store as cs

        super().setUp()  # selects the hashing embedder before ingestion runs
        self.cs = cs
        self.slug = "widget-engineering"
        cs.upsert_course(self.slug, {"name": "Advanced Widget Engineering", "active": True})

    def tearDown(self):
        from services.course_ingest import prune_course

        try:
            prune_course(self.slug)
        except Exception:
            pass
        self.cs.delete_course(self.slug)

    def test_upload_is_searchable_without_a_restart(self):
        from rag_v2.models import RetrievalFilter
        from rag_v2.retriever import HybridRetriever, RetrievalConfig
        from services.course_ingest import _store_and_embedder, process_next_document

        doc_id = self.cs.add_document(
            self.slug, "outline.txt", "text/plain", DIGITAL_PDF_TEXT.encode()
        )

        self.assertTrue(process_next_document())

        row = self.cs.get_document(doc_id)
        self.assertEqual("indexed", row["ingest_status"], row.get("ingest_error"))
        self.assertGreater(row["chunk_count"], 0)

        # The decisive assertion: query the live store the same way the bot does, with no
        # rebuild and no restart, and find the fact that only existed in the uploaded file.
        store, embedder = _store_and_embedder()
        # min_score is a relevance threshold tuned for real bge-small vectors; the hashing
        # embedder used here scores semantically at random, so the threshold is disabled.
        # Everything else is the production path: a live store read plus BM25 recomputed
        # over the fetched candidates, which is what makes a new chunk visible immediately.
        retriever = HybridRetriever(store, embedder, RetrievalConfig(min_score=0.0))
        results = retriever.retrieve(
            "laboratory access code", filters=RetrievalFilter(course_id=self.slug), top_k=5
        )
        self.assertTrue(results, "uploaded document was not retrievable")
        self.assertIn("ZEBRA-4417", " ".join(r.chunk.text for r in results))

    def test_archiving_removes_the_course_from_retrieval_and_reactivating_restores_it(self):
        from services.course_ingest import process_next_document, prune_course, resync_course

        self.cs.add_document(self.slug, "outline.txt", "text/plain", DIGITAL_PDF_TEXT.encode())
        process_next_document()
        store, _ = self._store()
        self.assertGreater(self._course_chunk_count(store), 0)

        prune_course(self.slug)
        self.assertEqual(0, self._course_chunk_count(store))

        # The document rows survived an archive, so reactivating needs no re-upload or re-OCR.
        report = resync_course(self.slug)
        self.assertGreater(report["chunks"], 0)
        self.assertGreater(self._course_chunk_count(store), 0)

    def test_deleting_the_document_removes_its_chunks(self):
        from services.course_ingest import process_next_document, resync_course

        doc_id = self.cs.add_document(
            self.slug, "outline.txt", "text/plain", DIGITAL_PDF_TEXT.encode()
        )
        process_next_document()
        store, _ = self._store()
        self.assertGreater(self._course_chunk_count(store), 0)

        self.cs.delete_document(doc_id)
        resync_course(self.slug)

        self.assertEqual(0, self._course_chunk_count(store))

    def test_a_broken_upload_fails_that_document_only(self):
        from services.course_ingest import process_next_document

        bad = self.cs.add_document(self.slug, "broken.xyz", "application/x-weird", b"\x00\x01\x02")
        self.assertTrue(process_next_document())
        row = self.cs.get_document(bad)
        self.assertEqual("failed", row["ingest_status"])
        self.assertTrue(row["ingest_error"])

        # The worker survives and still processes the next document.
        good = self.cs.add_document(
            self.slug, "outline.txt", "text/plain", DIGITAL_PDF_TEXT.encode()
        )
        self.assertTrue(process_next_document())
        self.assertEqual("indexed", self.cs.get_document(good)["ingest_status"])

    def _store(self):
        from services.course_ingest import _store_and_embedder

        return _store_and_embedder()

    def _course_chunk_count(self, store):
        from rag_v2.models import RetrievalFilter

        return store.count(RetrievalFilter(course_id=self.slug))


class RebuildParityTests(unittest.TestCase):
    """A full rebuild must reproduce the ingested chunks, not churn them."""

    def test_rebuild_path_builds_the_same_document_shape(self):
        from rag_v2.runtime import _uploaded_documents
        from services import content_store as cs
        from services.course_ingest import document_to_corpus

        slug = "parity-course"
        cs.upsert_course(slug, {"name": "Parity", "active": True})
        doc_id = cs.add_document(slug, "f.txt", "text/plain", b"some text")
        cs.set_document_status(doc_id, "indexed", extracted_text="some text")
        try:
            row = next(r for r in cs.list_indexed_documents() if int(r["id"]) == doc_id)
            direct = document_to_corpus(row)
            via_rebuild = [d for d in _uploaded_documents({slug}) if d.document_id.startswith(slug)]
            self.assertIn(direct, via_rebuild)
        finally:
            cs.delete_course(slug)


class CourseApiTests(unittest.TestCase):
    """Course mutations are refused unless the bot actually reads content from the DB."""

    def _client(self, authed=True):
        from fastapi.testclient import TestClient

        import main
        from services import content_store as cs

        cs.set_admin_password("p2-secret")
        client = TestClient(main.app, base_url="https://testserver")
        if authed:
            client.post("/admin/login", data={"password": "p2-secret"}, follow_redirects=False)
        return client

    def test_mutations_require_auth(self):
        client = self._client(authed=False)
        self.assertEqual(401, client.post("/admin/api/courses", json={"name": "X"}).status_code)
        self.assertEqual(401, client.delete("/admin/api/courses/x").status_code)

    def test_mutations_are_refused_in_files_mode(self):
        os.environ["CONTENT_SOURCE"] = "files"
        client = self._client()
        response = client.post("/admin/api/courses", json={"name": "New Course"})
        self.assertEqual(409, response.status_code)
        self.assertIn("CONTENT_SOURCE=db", response.json()["detail"])

    def test_create_and_archive_in_db_mode(self):
        import services.course_loader as cl
        from services import content_store as cs

        os.environ["CONTENT_SOURCE"] = "db"
        cl._load_courses_cached.cache_clear()
        client = self._client()
        try:
            created = client.post(
                "/admin/api/courses",
                json={
                    "name": "Brand New Course",
                    "dates": "1-2 Jan 2027",
                    "venue": "KL",
                    "keywords": "widget, gadget",
                    "overview": "# Brand New Course\n\nAll about widgets.",
                },
            )
            self.assertEqual(200, created.status_code, created.text)
            slug = created.json()["slug"]
            self.assertEqual("brand-new-course", slug)

            # It is immediately visible to the loader the bot reads from — no restart.
            self.assertIn(slug, cl.load_courses())
            self.assertEqual(["widget", "gadget"], cl.load_courses()[slug].keywords)

            archived = client.post(f"/admin/api/courses/{slug}/active", json={"active": False})
            self.assertEqual(200, archived.status_code, archived.text)
            self.assertFalse(cl.load_courses()[slug].active)

            self.assertEqual(200, client.delete(f"/admin/api/courses/{slug}").status_code)
            self.assertNotIn(slug, cl.load_courses())
        finally:
            os.environ["CONTENT_SOURCE"] = "files"
            cs.delete_course("brand-new-course")
            cl._load_courses_cached.cache_clear()

    def test_upload_rejects_an_empty_file(self):
        import services.course_loader as cl
        from services import content_store as cs

        os.environ["CONTENT_SOURCE"] = "db"
        cl._load_courses_cached.cache_clear()
        cs.upsert_course("upload-api-course", {"name": "Upload API", "active": True})
        client = self._client()
        try:
            response = client.post(
                "/admin/api/courses/upload-api-course/documents",
                files={"file": ("empty.txt", b"", "text/plain")},
            )
            self.assertEqual(400, response.status_code)
        finally:
            os.environ["CONTENT_SOURCE"] = "files"
            cs.delete_course("upload-api-course")
            cl._load_courses_cached.cache_clear()


if __name__ == "__main__":
    unittest.main()
