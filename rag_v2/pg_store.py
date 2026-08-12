"""Postgres-backed vector store — a drop-in for SQLiteVectorStore.

Same public interface as `rag_v2.store.SQLiteVectorStore`, so the retriever and runtime use
it unchanged. Embeddings are stored as `bytea` with the identical 4-byte-float encoding the
SQLite store uses; the retriever pulls candidates and scores them in Python, so no pgvector
extension is required at course-catalogue scale. Moving to a pgvector `vector(N)` column with
an ANN index later is a self-contained change here — the retriever interface stays the same.

Tables are namespaced `rag_*` so they never collide with the app's lead/conversation tables
in the same database.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone

from rag_v2.embeddings import Vector
from rag_v2.models import Chunk, RetrievalFilter
from rag_v2.store import (
    _SCHEMA_VERSION,
    IndexCompatibilityError,
    _decode_vector,
    _encode_vector,
)


class PostgresVectorStore:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = (dsn or os.getenv("DATABASE_URL", "")).strip()
        if not self._dsn:
            raise RuntimeError("DATABASE_URL is required for the Postgres vector store")

    @contextmanager
    def _connection(self):
        import psycopg

        connection = psycopg.connect(self._dsn)
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, *, model_id: str, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("embedding dimension must be positive")
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    course_id TEXT,
                    topic TEXT,
                    metadata_json JSONB NOT NULL,
                    embedding BYTEA NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rag_chunks_course_idx ON rag_chunks(course_id);
                CREATE INDEX IF NOT EXISTS rag_chunks_topic_idx ON rag_chunks(topic);
                CREATE INDEX IF NOT EXISTS rag_chunks_document_idx ON rag_chunks(document_id);
                """
            )
            cursor.execute("SELECT key, value FROM rag_index_metadata")
            existing = {key: value for key, value in cursor.fetchall()}
            expected = {
                "schema_version": _SCHEMA_VERSION,
                "model_id": model_id,
                "dimension": str(dimension),
            }
            if existing and any(existing.get(key) != value for key, value in expected.items()):
                raise IndexCompatibilityError(
                    "index was built with a different schema or embedding model; rebuild it"
                )
            self._upsert_metadata(cursor, expected)
            connection.commit()

    @staticmethod
    def _upsert_metadata(cursor, values) -> None:
        for key, value in dict(values).items():
            cursor.execute(
                "INSERT INTO rag_index_metadata(key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(value)),
            )

    def set_metadata(self, values: dict[str, str | int]) -> None:
        if not values:
            return
        with self._connection() as connection, connection.cursor() as cursor:
            self._upsert_metadata(cursor, values)
            connection.commit()

    def metadata(self) -> dict[str, str]:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT key, value FROM rag_index_metadata")
                return {key: value for key, value in cursor.fetchall()}
        except Exception:  # noqa: BLE001 — table may not exist yet
            return {}

    def assert_compatible(self, *, model_id: str, dimension: int) -> None:
        metadata = self.metadata()
        if not metadata:
            raise IndexCompatibilityError("index has not been built")
        if (
            metadata.get("schema_version") != _SCHEMA_VERSION
            or metadata.get("model_id") != model_id
            or metadata.get("dimension") != str(dimension)
        ):
            raise IndexCompatibilityError(
                "index embedding model does not match the configured query embedder"
            )

    def chunk_ids(self) -> set[str]:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT chunk_id FROM rag_chunks")
                return {str(row[0]) for row in cursor.fetchall()}
        except Exception:  # noqa: BLE001
            return set()

    def sync(
        self,
        chunks: Sequence[Chunk],
        new_embeddings: dict[str, Sequence[float]],
        *,
        document_count: int,
        corpus_version: str,
    ) -> tuple[int, int]:
        """Synchronize target chunks, embedding only chunks absent from the current index."""
        metadata = self.metadata()
        if not metadata:
            raise IndexCompatibilityError("initialize the index before writing chunks")
        dimension = int(metadata["dimension"])
        target_ids = [chunk.chunk_id for chunk in chunks]
        before = self.chunk_ids()

        with self._connection() as connection, connection.cursor() as cursor:
            # Drop chunks no longer in the corpus.
            if target_ids:
                cursor.execute(
                    "DELETE FROM rag_chunks WHERE NOT (chunk_id = ANY(%s))", (target_ids,)
                )
            else:
                cursor.execute("DELETE FROM rag_chunks")

            written = 0
            for chunk in chunks:
                embedding = new_embeddings.get(chunk.chunk_id)
                meta = json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True)
                if embedding is not None:
                    if len(embedding) != dimension:
                        raise ValueError(
                            f"chunk {chunk.chunk_id} has dimension {len(embedding)}; "
                            f"expected {dimension}"
                        )
                    cursor.execute(
                        """
                        INSERT INTO rag_chunks(
                            chunk_id, document_id, title, text, source_ref, ordinal,
                            course_id, topic, metadata_json, embedding
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id, title = EXCLUDED.title,
                            text = EXCLUDED.text, source_ref = EXCLUDED.source_ref,
                            ordinal = EXCLUDED.ordinal, course_id = EXCLUDED.course_id,
                            topic = EXCLUDED.topic, metadata_json = EXCLUDED.metadata_json,
                            embedding = EXCLUDED.embedding
                        """,
                        (
                            chunk.chunk_id, chunk.document_id, chunk.title, chunk.text,
                            chunk.source_ref, chunk.ordinal, chunk.course_id, chunk.topic,
                            meta, _encode_vector(embedding),
                        ),
                    )
                    written += 1
                else:
                    # Existing chunk reuses its embedding; refresh only its metadata/provenance.
                    cursor.execute(
                        """
                        UPDATE rag_chunks SET document_id=%s, title=%s, text=%s, source_ref=%s,
                            ordinal=%s, course_id=%s, topic=%s, metadata_json=%s
                        WHERE chunk_id=%s
                        """,
                        (
                            chunk.document_id, chunk.title, chunk.text, chunk.source_ref,
                            chunk.ordinal, chunk.course_id, chunk.topic, meta, chunk.chunk_id,
                        ),
                    )

            self._upsert_metadata(
                cursor,
                {
                    "document_count": document_count,
                    "chunk_count": len(chunks),
                    "corpus_version": corpus_version,
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "backend": "postgres",
                    "vector_index": "linear_scan",
                    "lexical_index": "in_memory_bm25",
                },
            )
            connection.commit()
        return written, max(0, len(before - set(target_ids)))

    @staticmethod
    def _where(filters: RetrievalFilter) -> tuple[str, list]:
        clauses: list[str] = []
        values: list = []
        if filters.course_id is not None:
            clauses.append("course_id = %s")
            values.append(filters.course_id)
        if filters.topics:
            clauses.append("topic = ANY(%s)")
            values.append(list(filters.topics))
        if filters.document_ids:
            clauses.append("document_id = ANY(%s)")
            values.append(list(filters.document_ids))
        return (" WHERE " + " AND ".join(clauses) if clauses else "", values)

    def iter_candidates(
        self, filters: RetrievalFilter | None = None
    ) -> Iterable[tuple[Chunk, Vector]]:
        metadata = self.metadata()
        if not metadata:
            raise IndexCompatibilityError("index has not been built")
        dimension = int(metadata["dimension"])
        where, values = self._where(filters or RetrievalFilter())
        query = (
            "SELECT chunk_id, document_id, title, text, source_ref, ordinal, course_id, "
            "topic, metadata_json, embedding FROM rag_chunks" + where
            + " ORDER BY document_id, ordinal"
        )
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(query, values)
            rows = cursor.fetchall()
        for row in rows:
            (
                chunk_id, document_id, title, text, source_ref, ordinal,
                course_id, topic, metadata_json, embedding,
            ) = row
            meta = metadata_json if isinstance(metadata_json, dict) else json.loads(metadata_json)
            yield (
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    title=title,
                    text=text,
                    source_ref=source_ref,
                    ordinal=int(ordinal),
                    course_id=course_id,
                    topic=topic,
                    metadata=meta,
                ),
                _decode_vector(bytes(embedding), dimension),
            )

    def count(self, filters: RetrievalFilter | None = None) -> int:
        where, values = self._where(filters or RetrievalFilter())
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM rag_chunks" + where, values)
                return int(cursor.fetchone()[0])
        except Exception:  # noqa: BLE001
            return 0

    def health(self) -> dict[str, str | int]:
        metadata = self.metadata()
        metadata["actual_chunk_count"] = str(self.count())
        return metadata
