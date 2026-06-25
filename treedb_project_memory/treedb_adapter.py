from __future__ import annotations

import importlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import TreeDBConfig


class TreeDBAdapterError(Exception):
    """Raised when TreeDB/Haystack indexing cannot proceed."""


@dataclass(frozen=True)
class IndexDocument:
    id: str
    content: str
    embedding: list[float]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    mode: str
    top_k: int
    filters: dict[str, Any] = field(default_factory=dict)
    query_embedding: list[float] | None = None


@dataclass(frozen=True)
class RetrievedDocument:
    id: str
    content: str
    score: float | None
    meta: dict[str, Any] = field(default_factory=dict)


class TreeDBAdapter(Protocol):
    def health(self) -> dict[str, Any]:
        """Return service health/capability information."""

    def count_documents(self) -> int:
        """Count documents through the TreeDB service."""

    def upsert_documents(self, documents: list[IndexDocument]) -> int:
        """Upsert embedded documents through TreeDB/Haystack."""

    def delete_documents(self, document_ids: list[str]) -> int | None:
        """Delete explicit document IDs through TreeDB/Haystack."""

    def search_documents(self, request: RetrievalRequest) -> list[RetrievedDocument]:
        """Retrieve indexed documents for a query."""


def create_treedb_adapter(
    config: TreeDBConfig,
    *,
    embedding_dimension: int,
) -> TreeDBAdapter:
    if config.adapter == "memory":
        return InMemoryTreeDBAdapter(config, embedding_dimension=embedding_dimension)
    if config.adapter == "haystack":
        return HaystackTreeDBAdapter(config, embedding_dimension=embedding_dimension)
    raise TreeDBAdapterError(f"unsupported TreeDB adapter: {config.adapter}")


class InMemoryTreeDBAdapter:
    """Self-contained adapter for tests and local smoke runs."""

    def __init__(self, config: TreeDBConfig, *, embedding_dimension: int) -> None:
        self.config = config
        self.embedding_dimension = embedding_dimension
        self.documents: dict[str, IndexDocument] = {}

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "adapter": "memory",
            "index": self.config.index,
            "embedding_dimension": self.embedding_dimension,
        }

    def count_documents(self) -> int:
        return len(self.documents)

    def upsert_documents(self, documents: list[IndexDocument]) -> int:
        for document in documents:
            if len(document.embedding) != self.embedding_dimension:
                raise TreeDBAdapterError(
                    f"document {document.id} has embedding dimension "
                    f"{len(document.embedding)}, expected {self.embedding_dimension}"
                )
            self.documents[document.id] = document
        return len(documents)

    def delete_documents(self, document_ids: list[str]) -> int:
        deleted = 0
        for document_id in dict.fromkeys(document_ids):
            if document_id in self.documents:
                deleted += 1
                del self.documents[document_id]
        return deleted

    def search_documents(self, request: RetrievalRequest) -> list[RetrievedDocument]:
        _validate_retrieval_request(request, supports_source_filter=True)
        if request.mode in {"semantic", "hybrid"} and request.query_embedding is None:
            raise TreeDBAdapterError(
                f"{request.mode} retrieval requires a query embedding"
            )
        scored: list[RetrievedDocument] = []
        terms = _query_terms(request.query)
        for document in self.documents.values():
            if not _matches_filters(document.meta, request.filters):
                continue
            score = _memory_score(document, request, terms)
            scored.append(
                RetrievedDocument(
                    id=document.id,
                    content=document.content,
                    score=score,
                    meta=dict(document.meta),
                )
            )
        scored.sort(key=lambda item: (-(item.score or 0.0), item.id))
        return scored[: request.top_k]


class HaystackTreeDBAdapter:
    """Thin boundary around upstream TreeDB Haystack DocumentStore."""

    def __init__(self, config: TreeDBConfig, *, embedding_dimension: int) -> None:
        self.config = config
        self.embedding_dimension = embedding_dimension
        imports = _load_haystack_imports()
        self._document_cls = imports["Document"]
        self._duplicate_policy = imports["DuplicatePolicy"]
        document_store_cls = imports["TreeDBDocumentStore"]
        try:
            self._store = document_store_cls(
                base_url=config.base_url,
                index=config.index,
                embedding_dimension=embedding_dimension,
                similarity=config.similarity,
                return_embedding=False,
                ensure_index=config.ensure_index,
                timeout=config.timeout_seconds,
            )
        except Exception as exc:  # upstream packages expose their own error classes
            raise TreeDBAdapterError(f"TreeDB document store setup failed: {exc}") from exc

    def health(self) -> dict[str, Any]:
        client = getattr(self._store, "client", None)
        payload: dict[str, Any] = {
            "base_url": self.config.base_url,
            "index": self.config.index,
            "embedding_dimension": self.embedding_dimension,
        }
        if client is not None and hasattr(client, "health"):
            try:
                payload["service"] = dict(client.health())
            except Exception as exc:
                raise TreeDBAdapterError(f"TreeDB health check failed: {exc}") from exc
        index_info = getattr(self._store, "index_info", None)
        if index_info is not None:
            payload["index_info"] = _index_info_to_dict(index_info)
        return payload

    def count_documents(self) -> int:
        try:
            return int(self._store.count_documents())
        except Exception as exc:
            raise TreeDBAdapterError(f"TreeDB document count failed: {exc}") from exc

    def upsert_documents(self, documents: list[IndexDocument]) -> int:
        if not documents:
            return 0
        haystack_documents = [
            self._document_cls(
                id=document.id,
                content=document.content,
                embedding=list(document.embedding),
                meta=dict(document.meta),
            )
            for document in documents
        ]
        try:
            return int(
                self._store.write_documents(
                    haystack_documents,
                    policy=self._duplicate_policy.OVERWRITE,
                )
            )
        except Exception as exc:
            raise TreeDBAdapterError(f"TreeDB document upsert failed: {exc}") from exc

    def delete_documents(self, document_ids: list[str]) -> int | None:
        ids = list(dict.fromkeys(document_ids))
        if not ids:
            return 0
        try:
            result = self._store.delete_documents(ids)
        except Exception as exc:
            raise TreeDBAdapterError(f"TreeDB document delete failed: {exc}") from exc
        return None if result is None else int(result)

    def search_documents(self, request: RetrievalRequest) -> list[RetrievedDocument]:
        _validate_retrieval_request(request, supports_source_filter=True)
        if request.mode in {"semantic", "hybrid"} and request.query_embedding is None:
            raise TreeDBAdapterError(
                f"{request.mode} retrieval requires a query embedding"
            )
        if request.mode == "hybrid":
            raise TreeDBAdapterError(
                "hybrid retrieval is not supported by the selected TreeDB/Haystack adapter"
            )
        imports = _load_haystack_retrieval_imports()
        filters = _haystack_filters(request.filters)
        try:
            if request.mode == "semantic":
                retriever = imports["EmbeddingRetriever"](
                    document_store=self._store,
                    filters=filters,
                    top_k=request.top_k,
                )
                result = retriever.run(query_embedding=request.query_embedding)
            elif request.mode == "keyword":
                retriever = imports["BM25Retriever"](
                    document_store=self._store,
                    filters=filters,
                    top_k=request.top_k,
                )
                result = retriever.run(query=request.query)
            else:
                raise TreeDBAdapterError(
                    "unsupported retrieval mode; expected one of: keyword, semantic"
                )
        except TreeDBAdapterError:
            raise
        except TypeError as exc:
            raise TreeDBAdapterError(
                "TreeDB/Haystack retrieval API is incompatible with this adapter; "
                "update the upstream integration or select a supported mode"
            ) from exc
        except Exception as exc:
            raise TreeDBAdapterError(f"TreeDB retrieval failed: {exc}") from exc
        documents = result.get("documents") if isinstance(result, dict) else None
        if documents is None:
            raise TreeDBAdapterError("TreeDB retrieval did not return documents")
        return [_from_haystack_document(document) for document in documents]


def _load_haystack_imports() -> dict[str, Any]:
    missing_message = (
        "TreeDB/Haystack indexing requires optional upstream packages. Install "
        "Haystack plus treedb-client and treedb-haystack from the upstream TreeDB "
        "repository; this package intentionally does not add private local path "
        "dependencies."
    )
    try:
        haystack = importlib.import_module("haystack")
        document_cls = getattr(haystack, "Document")
        duplicate_types = importlib.import_module("haystack.document_stores.types")
        stores = importlib.import_module("haystack_integrations.document_stores.treedb")
    except (ImportError, AttributeError) as exc:
        raise TreeDBAdapterError(missing_message) from exc

    try:
        return {
            "Document": document_cls,
            "DuplicatePolicy": getattr(duplicate_types, "DuplicatePolicy"),
            "TreeDBDocumentStore": getattr(stores, "TreeDBDocumentStore"),
        }
    except AttributeError as exc:
        raise TreeDBAdapterError(missing_message) from exc


def _load_haystack_retrieval_imports() -> dict[str, Any]:
    missing_message = (
        "TreeDB/Haystack retrieval requires upstream retriever components. Install "
        "Haystack plus the TreeDB Haystack integration, or select an adapter/mode "
        "that advertises retrieval support."
    )
    candidates = [
        (
            "haystack_integrations.components.retrievers.treedb",
            "TreeDBEmbeddingRetriever",
            "TreeDBBM25Retriever",
        ),
        (
            "haystack_integrations.components.retrievers.treedb",
            "TreeDBDocumentRetriever",
            "TreeDBBM25Retriever",
        ),
    ]
    for module_name, embedding_name, keyword_name in candidates:
        try:
            module = importlib.import_module(module_name)
            return {
                "EmbeddingRetriever": getattr(module, embedding_name),
                "BM25Retriever": getattr(module, keyword_name),
            }
        except (ImportError, AttributeError):
            continue
    raise TreeDBAdapterError(missing_message)


def _validate_retrieval_request(
    request: RetrievalRequest,
    *,
    supports_source_filter: bool,
) -> None:
    if request.mode not in {"semantic", "keyword", "hybrid"}:
        raise TreeDBAdapterError(
            "unsupported retrieval mode; expected one of: hybrid, keyword, semantic"
        )
    if request.top_k <= 0:
        raise TreeDBAdapterError("retrieval top_k must be positive")
    unsupported_filters = sorted(set(request.filters) - {"source_id"})
    if unsupported_filters:
        raise TreeDBAdapterError(
            f"unsupported retrieval filter(s): {', '.join(unsupported_filters)}"
        )
    if request.filters and not supports_source_filter:
        raise TreeDBAdapterError(
            "source filtering is not supported by the selected retriever"
        )


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    source_id = filters.get("source_id")
    if source_id is not None and metadata.get("source_id") != source_id:
        return False
    return True


def _query_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z0-9_]+", query.lower()) if term]


def _memory_score(
    document: IndexDocument,
    request: RetrievalRequest,
    terms: list[str],
) -> float:
    keyword = _keyword_score(document.content, terms)
    if request.mode == "keyword":
        return keyword
    semantic = _cosine_score(document.embedding, request.query_embedding or [])
    if request.mode == "semantic":
        return semantic
    return semantic + keyword


def _keyword_score(content: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    haystack = content.lower()
    return float(sum(haystack.count(term) for term in terms))


def _cosine_score(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _haystack_filters(filters: dict[str, Any]) -> dict[str, Any] | None:
    if not filters:
        return None
    return {
        "field": "meta.source_id",
        "operator": "==",
        "value": filters["source_id"],
    }


def _from_haystack_document(document: Any) -> RetrievedDocument:
    document_id = getattr(document, "id", None)
    content = getattr(document, "content", None)
    if document_id is None or content is None:
        raise TreeDBAdapterError("retrieved document is missing id or content")
    score = getattr(document, "score", None)
    meta = getattr(document, "meta", {}) or {}
    return RetrievedDocument(
        id=str(document_id),
        content=str(content),
        score=None if score is None else float(score),
        meta=dict(meta),
    )


def _index_info_to_dict(index_info: Any) -> dict[str, Any]:
    if hasattr(index_info, "to_dict"):
        return dict(index_info.to_dict())
    payload: dict[str, Any] = {}
    for key in (
        "name",
        "dimension",
        "metric",
        "generation",
        "contract_version",
        "document_type",
    ):
        if hasattr(index_info, key):
            payload[key] = getattr(index_info, key)
    capabilities = getattr(index_info, "capabilities", None)
    if capabilities is not None:
        if hasattr(capabilities, "to_dict"):
            payload["capabilities"] = dict(capabilities.to_dict())
        else:
            payload["capabilities"] = {
                key: getattr(capabilities, key)
                for key in dir(capabilities)
                if not key.startswith("_") and isinstance(getattr(capabilities, key), bool)
            }
    return payload
