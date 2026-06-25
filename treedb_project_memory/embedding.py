from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .config import EmbeddingConfig


class EmbeddingError(Exception):
    """Raised when an embedding provider cannot produce valid vectors."""


class MissingEmbeddingDependency(EmbeddingError):
    """Raised when an optional embedding dependency is not installed."""


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimension: int
    batch_size: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed input texts in order."""


def create_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    if config.provider == "deterministic":
        return DeterministicEmbeddingProvider(config)
    if config.provider == "sentence-transformers":
        return SentenceTransformersEmbeddingProvider(config)
    if config.provider == "openai-compatible":
        return OpenAICompatibleEmbeddingProvider(config)
    raise EmbeddingError(f"unsupported embedding provider: {config.provider}")


@dataclass
class DeterministicEmbeddingProvider:
    config: EmbeddingConfig

    def __post_init__(self) -> None:
        self.provider = self.config.provider
        self.model = self.config.model
        self.dimension = self.config.dimension
        self.batch_size = self.config.batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(self.model, text, self.dimension) for text in texts]


class SentenceTransformersEmbeddingProvider:
    def __init__(self, config: EmbeddingConfig) -> None:
        self.provider = config.provider
        self.model = config.model
        self.dimension = config.dimension
        self.batch_size = config.batch_size
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise MissingEmbeddingDependency(
                "embedding.provider='sentence-transformers' requires the optional "
                "sentence-transformers package; install 'treedb-project-memory[local-embeddings]'"
            ) from exc

        kwargs: dict[str, Any] = {}
        if config.device:
            kwargs["device"] = config.device
        self._model = SentenceTransformer(config.model, **kwargs)
        actual_dimension = self._model.get_sentence_embedding_dimension()
        if actual_dimension is not None and actual_dimension != self.dimension:
            raise EmbeddingError(
                f"embedding dimension mismatch for model {self.model!r}: "
                f"config has {self.dimension}, provider reports {actual_dimension}"
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        encoded = self._model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=False,
            show_progress_bar=False,
        )
        vectors = [_vector_to_list(vector) for vector in encoded]
        _validate_vectors(vectors, self.dimension, self.provider, self.model)
        return vectors


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, config: EmbeddingConfig) -> None:
        self.provider = config.provider
        self.model = config.model
        self.dimension = config.dimension
        self.batch_size = config.batch_size
        self.base_url = (config.base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key_env = config.api_key_env or "OPENAI_API_KEY"
        self.timeout_seconds = config.timeout_seconds or 60.0
        self.api_key = os.environ.get(self.api_key_env)
        if not self.api_key:
            raise EmbeddingError(
                f"embedding.provider='openai-compatible' requires ${self.api_key_env} to be set"
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed_batch(texts[start : start + self.batch_size]))
        _validate_vectors(vectors, self.dimension, self.provider, self.model)
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EmbeddingError(
                f"OpenAI-compatible embedding request failed with HTTP {exc.code}: {detail}"
            ) from exc
        except OSError as exc:
            raise EmbeddingError(
                f"OpenAI-compatible embedding request failed: {exc}"
            ) from exc

        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise EmbeddingError("embedding response was not valid JSON") from exc
        rows = data.get("data")
        if not isinstance(rows, list):
            raise EmbeddingError("embedding response must contain a data list")
        rows = sorted(rows, key=lambda row: row.get("index", 0) if isinstance(row, dict) else 0)
        vectors: list[list[float]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise EmbeddingError("embedding response data entries must be objects")
            embedding = row.get("embedding")
            if not isinstance(embedding, list):
                raise EmbeddingError("embedding response entries must contain embedding lists")
            vectors.append([float(value) for value in embedding])
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding response returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors


def _deterministic_vector(model: str, text: str, dimension: int) -> list[float]:
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        digest = hashlib.sha256(
            f"{model}\0{counter}\0{text}".encode("utf-8")
        ).digest()
        for offset in range(0, len(digest), 4):
            if len(values) >= dimension:
                break
            integer = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((integer / 4_294_967_295.0) * 2.0 - 1.0)
        counter += 1
    norm = sum(value * value for value in values) ** 0.5
    if norm == 0:
        return values
    return [value / norm for value in values]


def _vector_to_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _validate_vectors(
    vectors: list[list[float]],
    expected_dimension: int,
    provider: str,
    model: str,
) -> None:
    for index, vector in enumerate(vectors):
        if len(vector) != expected_dimension:
            raise EmbeddingError(
                f"embedding dimension mismatch for {provider}/{model}: "
                f"input {index} produced {len(vector)} values, expected {expected_dimension}"
            )
