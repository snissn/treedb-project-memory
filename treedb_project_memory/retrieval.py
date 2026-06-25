from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .citations import Citation, citation_from_metadata
from .config import ProjectConfig, Workspace, WorkspaceError
from .embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from .treedb_adapter import (
    RetrievedDocument,
    RetrievalRequest,
    TreeDBAdapterError,
    create_treedb_adapter,
)

ProviderFactory = Any
AdapterFactory = Any


class RetrievalError(Exception):
    """Raised when retrieval cannot complete with the selected capabilities."""


@dataclass(frozen=True)
class SearchResult:
    id: str
    score: float | None
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    citation: Citation | None = None

    def to_json(self, *, include_content: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "score": self.score,
            "metadata": dict(self.metadata),
            "citation": None if self.citation is None else self.citation.to_json(),
        }
        if include_content:
            payload["content"] = self.content
        return payload


@dataclass(frozen=True)
class RetrievalTrace:
    query: str
    mode: str
    filters: dict[str, Any]
    top_k: int
    document_ids: list[str]
    scores: list[float | None]
    citations: list[dict[str, Any]]
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "mode": self.mode,
            "filters": dict(self.filters),
            "top_k": self.top_k,
            "document_ids": list(self.document_ids),
            "scores": list(self.scores),
            "citations": list(self.citations),
            "details": dict(self.details),
        }


class SearchableAdapter(Protocol):
    def search_documents(self, request: RetrievalRequest) -> list[RetrievedDocument]:
        """Return retrieval documents for an explicit request."""


def search_workspace(
    workspace: Workspace,
    config: ProjectConfig,
    *,
    query: str,
    mode: str | None = None,
    top_k: int | None = None,
    source_id: str | None = None,
    provider_factory: ProviderFactory = create_embedding_provider,
    adapter_factory: AdapterFactory = create_treedb_adapter,
) -> tuple[list[SearchResult], RetrievalTrace]:
    if not query.strip():
        raise RetrievalError("query must be non-empty")
    resolved_mode = (mode or config.retrieval.default_mode).strip().lower()
    if resolved_mode not in {"semantic", "keyword", "hybrid"}:
        raise RetrievalError(
            "mode must be one of: hybrid, keyword, semantic"
        )
    resolved_top_k = top_k if top_k is not None else config.retrieval.top_k
    if resolved_top_k <= 0:
        raise RetrievalError("top_k must be positive")
    filters = _build_filters(config, source_id)

    query_embedding: list[float] | None = None
    if resolved_mode in {"semantic", "hybrid"}:
        provider = _create_provider(config, provider_factory)
        try:
            vectors = provider.embed([query])
        except EmbeddingError as exc:
            raise RetrievalError(str(exc)) from exc
        if len(vectors) != 1:
            raise RetrievalError(
                f"embedding provider returned {len(vectors)} vectors for one query"
            )
        query_embedding = vectors[0]
        if len(query_embedding) != provider.dimension:
            raise RetrievalError(
                f"query embedding dimension mismatch: got {len(query_embedding)}, "
                f"expected {provider.dimension}"
            )

    adapter = _create_adapter(config, adapter_factory)
    started = time.perf_counter()
    request = RetrievalRequest(
        query=query,
        mode=resolved_mode,
        top_k=resolved_top_k,
        filters=filters,
        query_embedding=query_embedding,
    )
    try:
        retrieved = adapter.search_documents(request)
    except TreeDBAdapterError as exc:
        raise RetrievalError(str(exc)) from exc
    elapsed = time.perf_counter() - started

    results = [_search_result(document) for document in retrieved]
    trace = RetrievalTrace(
        query=query,
        mode=resolved_mode,
        filters=filters,
        top_k=resolved_top_k,
        document_ids=[result.id for result in results],
        scores=[result.score for result in results],
        citations=[
            result.citation.to_json()
            for result in results
            if result.citation is not None
        ],
        details={
            "adapter": config.treedb.adapter,
            "index": config.treedb.index,
            "elapsed_seconds": elapsed,
            "embedding_used": query_embedding is not None,
        },
    )
    return results, trace


def _build_filters(config: ProjectConfig, source_id: str | None) -> dict[str, Any]:
    if source_id is None:
        return {}
    if source_id not in config.sources:
        raise WorkspaceError(f"source ID '{source_id}' is not configured")
    return {"source_id": source_id}


def _create_provider(
    config: ProjectConfig,
    provider_factory: ProviderFactory,
) -> EmbeddingProvider:
    try:
        provider = provider_factory(config.embedding)
    except EmbeddingError as exc:
        raise RetrievalError(str(exc)) from exc
    if provider.dimension != config.embedding.dimension:
        raise RetrievalError(
            f"embedding provider dimension mismatch: provider has {provider.dimension}, "
            f"config has {config.embedding.dimension}"
        )
    return provider


def _create_adapter(
    config: ProjectConfig,
    adapter_factory: AdapterFactory,
) -> SearchableAdapter:
    try:
        return adapter_factory(
            config.treedb,
            embedding_dimension=config.embedding.dimension,
        )
    except TreeDBAdapterError as exc:
        raise RetrievalError(str(exc)) from exc


def _search_result(document: RetrievedDocument) -> SearchResult:
    metadata = dict(document.meta)
    citation = citation_from_metadata(metadata, document.id)
    return SearchResult(
        id=document.id,
        score=document.score,
        content=document.content,
        metadata=metadata,
        citation=citation,
    )
