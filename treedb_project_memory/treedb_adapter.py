from __future__ import annotations

import importlib
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


class TreeDBAdapter(Protocol):
    def health(self) -> dict[str, Any]:
        """Return service health/capability information."""

    def count_documents(self) -> int:
        """Count documents through the TreeDB service."""

    def upsert_documents(self, documents: list[IndexDocument]) -> int:
        """Upsert embedded documents through TreeDB/Haystack."""

    def delete_documents(self, document_ids: list[str]) -> int | None:
        """Delete explicit document IDs through TreeDB/Haystack."""


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
