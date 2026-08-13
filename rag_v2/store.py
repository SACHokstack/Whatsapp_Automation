from __future__ import annotations

import json
import sqlite3
from array import array
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from rag_v2.embeddings import Vector, normalize
from rag_v2.models import Chunk, RetrievalFilter

_SCHEMA_VERSION = "1"


class IndexCompatibilityError(RuntimeError):
    pass


def _encode_vector(vector: Sequence[float]) -> bytes:
    return array("f", normalize(vector)).tobytes()


def _decode_vector(value: bytes, dimension: int) -> Vector:
    decoded = array("f")
    decoded.frombytes(value)
    if len(decoded) != dimension:
        raise IndexCompatibilityError(
            f"stored vector has dimension {len(decoded)}; expected {dimension}"
        )
    return normalize(decoded)


class SQLiteVectorStore:
    """Small-corpus persistent vector store with hard metadata filtering.

    This deliberately uses SQLite instead of hiding retrieval behind application state. It is
    deterministic, inspectable, and sufficient for a course catalog-sized corpus. The interface
    can later be implemented by a dedicated vector database without changing the retriever.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, *, model_id: str, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("embedding dimension must be positive")
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    course_id TEXT,
                    topic TEXT,
                    metadata_json TEXT NOT NULL,
                    embedding BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_course_idx ON chunks(course_id);
                CREATE INDEX IF NOT EXISTS chunks_topic_idx ON chunks(topic);
                CREATE INDEX IF NOT EXISTS chunks_document_idx ON chunks(document_id);
                """
            )
            existing = dict(connection.execute("SELECT key, value FROM index_metadata"))
            expected = {
                "schema_version": _SCHEMA_VERSION,
                "model_id": model_id,
                "dimension": str(dimension),
            }
            if existing and any(existing.get(key) != value for key, value in expected.items()):
                raise IndexCompatibilityError(
                    "index was built with a different schema or embedding model; rebuild it"
                )
            connection.executemany(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                expected.items(),
            )
            connection.commit()

    def set_metadata(self, values: dict[str, str | int]) -> None:
        if not values:
            return
        rows = [(key, str(value)) for key, value in values.items()]
        with self._connection() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                rows,
            )
            connection.commit()

    def metadata(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        with self._connection() as connection:
            try:
                return dict(connection.execute("SELECT key, value FROM index_metadata"))
            except sqlite3.OperationalError:
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

    def replace(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("every chunk must have exactly one embedding")
        metadata = self.metadata()
        if not metadata:
            raise IndexCompatibilityError("initialize the index before writing chunks")
        dimension = int(metadata["dimension"])
        rows = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            if len(embedding) != dimension:
                raise ValueError(
                    f"chunk {chunk.chunk_id} has dimension {len(embedding)}; expected {dimension}"
                )
            rows.append(
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.title,
                    chunk.text,
                    chunk.source_ref,
                    chunk.ordinal,
                    chunk.course_id,
                    chunk.topic,
                    json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    _encode_vector(embedding),
                )
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM chunks")
            connection.executemany(
                """
                INSERT INTO chunks(
                    chunk_id, document_id, title, text, source_ref, ordinal,
                    course_id, topic, metadata_json, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()

    def chunk_ids(self) -> set[str]:
        with self._connection() as connection:
            try:
                rows = connection.execute("SELECT chunk_id FROM chunks").fetchall()
            except sqlite3.OperationalError:
                return set()
        return {str(row[0]) for row in rows}

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
        target_ids = {chunk.chunk_id for chunk in chunks}
        rows = []
        metadata_rows = []
        for chunk in chunks:
            metadata_rows.append(
                (
                    chunk.document_id,
                    chunk.title,
                    chunk.text,
                    chunk.source_ref,
                    chunk.ordinal,
                    chunk.course_id,
                    chunk.topic,
                    json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    chunk.chunk_id,
                )
            )
            embedding = new_embeddings.get(chunk.chunk_id)
            if embedding is None:
                continue
            if len(embedding) != dimension:
                raise ValueError(
                    f"chunk {chunk.chunk_id} has dimension {len(embedding)}; expected {dimension}"
                )
            rows.append(
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.title,
                    chunk.text,
                    chunk.source_ref,
                    chunk.ordinal,
                    chunk.course_id,
                    chunk.topic,
                    json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True),
                    _encode_vector(embedding),
                )
            )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if target_ids:
                placeholders = ",".join("?" for _ in target_ids)
                connection.execute(
                    f"DELETE FROM chunks WHERE chunk_id NOT IN ({placeholders})",
                    tuple(target_ids),
                )
            else:
                connection.execute("DELETE FROM chunks")
            if rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO chunks(
                        chunk_id, document_id, title, text, source_ref, ordinal,
                        course_id, topic, metadata_json, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            # Chunk IDs include the evidence text, so an existing ID can safely reuse its
            # embedding. Structural metadata and provenance are deliberately not part of
            # that ID and must still be refreshed when an ingestion parser improves.
            if metadata_rows:
                connection.executemany(
                    """
                    UPDATE chunks
                    SET document_id = ?, title = ?, text = ?, source_ref = ?, ordinal = ?,
                        course_id = ?, topic = ?, metadata_json = ?
                    WHERE chunk_id = ?
                    """,
                    metadata_rows,
                )
            connection.executemany(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES (?, ?)",
                (
                    ("document_count", str(document_count)),
                    ("chunk_count", str(len(chunks))),
                    ("corpus_version", corpus_version),
                    ("built_at", datetime.now(timezone.utc).isoformat()),
                    ("backend", "sqlite"),
                    ("vector_index", "linear_scan"),
                    ("lexical_index", "in_memory_bm25"),
                ),
            )
            connection.commit()
        return len(rows), max(0, len(self.chunk_ids() - target_ids))

    def sync_course(
        self,
        course_id: str,
        chunks: Sequence[Chunk],
        new_embeddings: dict[str, Sequence[float]],
    ) -> tuple[int, int]:
        """Replace exactly one course's chunks, leaving every other course untouched.

        The SQLite twin of `PostgresVectorStore.sync_course`; see that docstring for why the
        prune must be scoped by course_id rather than global.
        """
        metadata = self.metadata()
        if not metadata:
            raise IndexCompatibilityError("initialize the index before writing chunks")
        dimension = int(metadata["dimension"])
        target_ids = {chunk.chunk_id for chunk in chunks}

        rows = []
        metadata_rows = []
        for chunk in chunks:
            meta = json.dumps(chunk.metadata, ensure_ascii=False, sort_keys=True)
            metadata_rows.append(
                (
                    chunk.document_id,
                    chunk.title,
                    chunk.text,
                    chunk.source_ref,
                    chunk.ordinal,
                    chunk.course_id,
                    chunk.topic,
                    meta,
                    chunk.chunk_id,
                )
            )
            embedding = new_embeddings.get(chunk.chunk_id)
            if embedding is None:
                continue
            if len(embedding) != dimension:
                raise ValueError(
                    f"chunk {chunk.chunk_id} has dimension {len(embedding)}; expected {dimension}"
                )
            rows.append(
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.title,
                    chunk.text,
                    chunk.source_ref,
                    chunk.ordinal,
                    chunk.course_id,
                    chunk.topic,
                    meta,
                    _encode_vector(embedding),
                )
            )

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = {
                str(row[0])
                for row in connection.execute(
                    "SELECT chunk_id FROM chunks WHERE course_id = ?", (course_id,)
                ).fetchall()
            }
            if target_ids:
                placeholders = ",".join("?" for _ in target_ids)
                connection.execute(
                    f"DELETE FROM chunks WHERE course_id = ? AND chunk_id NOT IN ({placeholders})",
                    (course_id, *target_ids),
                )
            else:
                connection.execute("DELETE FROM chunks WHERE course_id = ?", (course_id,))
            if rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO chunks(
                        chunk_id, document_id, title, text, source_ref, ordinal,
                        course_id, topic, metadata_json, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            if metadata_rows:
                connection.executemany(
                    """
                    UPDATE chunks
                    SET document_id = ?, title = ?, text = ?, source_ref = ?, ordinal = ?,
                        course_id = ?, topic = ?, metadata_json = ?
                    WHERE chunk_id = ?
                    """,
                    metadata_rows,
                )
            connection.commit()
        return len(rows), max(0, len(before - target_ids))

    def _where(self, filters: RetrievalFilter) -> tuple[str, list[str]]:
        clauses: list[str] = []
        values: list[str] = []
        if filters.course_id is not None:
            clauses.append("course_id = ?")
            values.append(filters.course_id)
        if filters.topics:
            clauses.append(f"topic IN ({','.join('?' for _ in filters.topics)})")
            values.extend(filters.topics)
        if filters.document_ids:
            clauses.append(f"document_id IN ({','.join('?' for _ in filters.document_ids)})")
            values.extend(filters.document_ids)
        return (" WHERE " + " AND ".join(clauses) if clauses else "", values)

    def iter_candidates(
        self, filters: RetrievalFilter | None = None
    ) -> Iterable[tuple[Chunk, Vector]]:
        metadata = self.metadata()
        if not metadata:
            raise IndexCompatibilityError("index has not been built")
        dimension = int(metadata["dimension"])
        where, values = self._where(filters or RetrievalFilter())
        query = "SELECT * FROM chunks" + where + " ORDER BY document_id, ordinal"
        with self._connection() as connection:
            rows = connection.execute(query, values).fetchall()
        for row in rows:
            yield (
                Chunk(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    title=row["title"],
                    text=row["text"],
                    source_ref=row["source_ref"],
                    ordinal=int(row["ordinal"]),
                    course_id=row["course_id"],
                    topic=row["topic"],
                    metadata=json.loads(row["metadata_json"]),
                ),
                _decode_vector(row["embedding"], dimension),
            )

    def count(self, filters: RetrievalFilter | None = None) -> int:
        where, values = self._where(filters or RetrievalFilter())
        with self._connection() as connection:
            try:
                row = connection.execute("SELECT COUNT(*) FROM chunks" + where, values).fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row[0])

    def health(self) -> dict[str, str | int]:
        metadata = self.metadata()
        metadata["actual_chunk_count"] = str(self.count())
        return metadata
