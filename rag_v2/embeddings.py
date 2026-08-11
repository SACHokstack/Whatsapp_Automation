from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Iterable, Sequence
from typing import Protocol

Vector = tuple[float, ...]


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...


def normalize(vector: Iterable[float]) -> Vector:
    values = tuple(float(value) for value in vector)
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ValueError("embedding vector has zero magnitude")
    return tuple(value / norm for value in values)


class FastEmbedder:
    """Local semantic embeddings backed by FastEmbed/ONNX.

    The heavyweight model import and initialization are lazy, so index inspection and tests do
    not need the optional runtime dependency or a model download.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        *,
        cache_dir: str | None = None,
        batch_size: int = 64,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._batch_size = batch_size
        self._model = None
        self._dimension: int | None = None

    @property
    def model_id(self) -> str:
        return f"fastembed:{self._model_name}"

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            probe = self.embed_query("embedding dimension probe")
            self._dimension = len(probe)
        return self._dimension

    def _get_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as error:
                raise RuntimeError(
                    "FastEmbed is not installed. Install project requirements before building "
                    "the semantic index."
                ) from error
            kwargs = {"model_name": self._model_name}
            if self._cache_dir:
                kwargs["cache_dir"] = self._cache_dir
            self._model = TextEmbedding(**kwargs)
        return self._model

    def _embed(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        model = self._get_model()
        vectors = [
            normalize(vector) for vector in model.embed(list(texts), batch_size=self._batch_size)
        ]
        if vectors:
            dimension = len(vectors[0])
            if any(len(vector) != dimension for vector in vectors):
                raise ValueError("embedding provider returned inconsistent dimensions")
            self._dimension = dimension
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return self._embed(texts)

    def embed_query(self, text: str) -> Vector:
        vectors = self._embed([text])
        if not vectors:
            raise ValueError("cannot embed an empty query batch")
        return vectors[0]


class HashingEmbedder:
    """Deterministic lexical embedder for tests and offline checks only."""

    def __init__(self, dimension: int = 384) -> None:
        self._dimension = dimension

    @property
    def model_id(self) -> str:
        return f"hashing:{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> Vector:
        values = [0.0] * self._dimension
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.md5(token.encode()).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimension
            values[index] += 1.0 if digest[4] & 1 else -1.0
        return normalize(values)

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> Vector:
        return self._vector(text)


def configured_embedder() -> Embedder:
    choice = os.getenv("RAG_EMBEDDER", "fastembed").strip().lower()
    if choice == "hashing":
        return HashingEmbedder()
    if choice not in {"fastembed", "local"}:
        raise ValueError("RAG_EMBEDDER must be 'fastembed' or 'hashing'")
    return FastEmbedder(
        model_name=os.getenv("RAG_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        cache_dir=os.getenv("RAG_MODEL_CACHE") or None,
    )
