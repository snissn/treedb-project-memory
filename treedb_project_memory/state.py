from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import EmbeddingConfig, ProjectConfig, TreeDBConfig, Workspace

INDEX_STATE_NAME = "index-state.json"
INDEX_STATE_VERSION = 1


class IndexStateError(Exception):
    """Raised when local index state cannot be loaded or reused safely."""


@dataclass
class FileIndexState:
    document_hash: str
    chunk_ids: list[str]
    chunk_count: int
    mtime: float | None = None
    size_bytes: int | None = None
    indexed_at: str | None = None

    @classmethod
    def from_json(cls, data: Any) -> "FileIndexState":
        if not isinstance(data, dict):
            raise IndexStateError("file state entries must be objects")
        chunk_ids = data.get("chunk_ids")
        if not isinstance(chunk_ids, list) or any(
            not isinstance(chunk_id, str) for chunk_id in chunk_ids
        ):
            raise IndexStateError("file state chunk_ids must be a list of strings")
        return cls(
            document_hash=_string_field(data, "document_hash"),
            chunk_ids=list(chunk_ids),
            chunk_count=_int_field(data, "chunk_count"),
            mtime=_optional_number(data.get("mtime"), "mtime"),
            size_bytes=_optional_int(data.get("size_bytes"), "size_bytes"),
            indexed_at=data.get("indexed_at")
            if isinstance(data.get("indexed_at"), str)
            else None,
        )

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "document_hash": self.document_hash,
            "chunk_ids": list(self.chunk_ids),
            "chunk_count": self.chunk_count,
        }
        if self.mtime is not None:
            payload["mtime"] = self.mtime
        if self.size_bytes is not None:
            payload["size_bytes"] = self.size_bytes
        if self.indexed_at is not None:
            payload["indexed_at"] = self.indexed_at
        return payload


@dataclass
class SourceIndexState:
    files: dict[str, FileIndexState] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: Any) -> "SourceIndexState":
        if not isinstance(data, dict):
            raise IndexStateError("source state entries must be objects")
        files = data.get("files", {})
        if not isinstance(files, dict):
            raise IndexStateError("source state files must be a mapping")
        return cls(
            files={
                str(path): FileIndexState.from_json(raw_file)
                for path, raw_file in files.items()
            }
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "files": {
                path: file_state.to_json()
                for path, file_state in sorted(self.files.items())
            }
        }


@dataclass
class IndexState:
    workspace: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    treedb_adapter: str
    treedb_base_url: str
    treedb_index: str
    treedb_similarity: str
    sources: dict[str, SourceIndexState] = field(default_factory=dict)
    version: int = INDEX_STATE_VERSION

    @classmethod
    def empty(cls, config: ProjectConfig) -> "IndexState":
        return cls(
            workspace=config.workspace,
            embedding_provider=config.embedding.provider,
            embedding_model=config.embedding.model,
            embedding_dimension=config.embedding.dimension,
            treedb_adapter=config.treedb.adapter,
            treedb_base_url=config.treedb.base_url,
            treedb_index=config.treedb.index,
            treedb_similarity=config.treedb.similarity,
        )

    @classmethod
    def from_json(cls, data: Any) -> "IndexState":
        if not isinstance(data, dict):
            raise IndexStateError("index state must be a JSON object")
        version = _int_field(data, "version")
        if version != INDEX_STATE_VERSION:
            raise IndexStateError(
                f"unsupported index state version {version}; rebuild the index"
            )
        sources = data.get("sources", {})
        if not isinstance(sources, dict):
            raise IndexStateError("index state sources must be a mapping")
        return cls(
            version=version,
            workspace=_string_field(data, "workspace"),
            embedding_provider=_string_field(data, "embedding_provider"),
            embedding_model=_string_field(data, "embedding_model"),
            embedding_dimension=_int_field(data, "embedding_dimension"),
            treedb_adapter=_string_field(data, "treedb_adapter"),
            treedb_base_url=_string_field(data, "treedb_base_url"),
            treedb_index=_string_field(data, "treedb_index"),
            treedb_similarity=_string_field(data, "treedb_similarity"),
            sources={
                str(source_id): SourceIndexState.from_json(raw_source)
                for source_id, raw_source in sources.items()
            },
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "workspace": self.workspace,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "treedb_adapter": self.treedb_adapter,
            "treedb_base_url": self.treedb_base_url,
            "treedb_index": self.treedb_index,
            "treedb_similarity": self.treedb_similarity,
            "sources": {
                source_id: source_state.to_json()
                for source_id, source_state in sorted(self.sources.items())
            },
        }

    def source(self, source_id: str) -> SourceIndexState:
        if source_id not in self.sources:
            self.sources[source_id] = SourceIndexState()
        return self.sources[source_id]

    def chunk_ids_for_sources(self, source_ids: set[str]) -> list[str]:
        ids: list[str] = []
        for source_id in sorted(source_ids):
            source = self.sources.get(source_id)
            if source is None:
                continue
            for file_state in source.files.values():
                ids.extend(file_state.chunk_ids)
        return ids

    def reset_sources(self, source_ids: set[str]) -> None:
        for source_id in source_ids:
            self.sources[source_id] = SourceIndexState()


def index_state_path(workspace: Workspace) -> Path:
    return workspace.state_dir / INDEX_STATE_NAME


def load_index_state(workspace: Workspace, config: ProjectConfig) -> IndexState:
    path = index_state_path(workspace)
    if not path.exists():
        return IndexState.empty(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IndexStateError(f"index state JSON is invalid: {exc}") from exc
    state = IndexState.from_json(data)
    return state


def save_index_state(workspace: Workspace, state: IndexState) -> None:
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    path = index_state_path(workspace)
    path.write_text(
        json.dumps(state.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validate_state_compatible(
    state: IndexState,
    config: ProjectConfig,
    *,
    rebuild: bool,
) -> None:
    if rebuild:
        return
    expected = _state_signature(config.embedding, config.treedb)
    actual = {
        "embedding_provider": state.embedding_provider,
        "embedding_model": state.embedding_model,
        "embedding_dimension": state.embedding_dimension,
        "treedb_adapter": state.treedb_adapter,
        "treedb_base_url": state.treedb_base_url,
        "treedb_index": state.treedb_index,
        "treedb_similarity": state.treedb_similarity,
    }
    if actual != expected:
        raise IndexStateError(
            "local index state was built with a different embedding or TreeDB "
            f"configuration: state={actual}, config={expected}; run "
            "'treedb-project-memory index --rebuild' after rebuilding the TreeDB index"
        )


def refresh_state_identity(state: IndexState, config: ProjectConfig) -> None:
    state.workspace = config.workspace
    state.embedding_provider = config.embedding.provider
    state.embedding_model = config.embedding.model
    state.embedding_dimension = config.embedding.dimension
    state.treedb_adapter = config.treedb.adapter
    state.treedb_base_url = config.treedb.base_url
    state.treedb_index = config.treedb.index
    state.treedb_similarity = config.treedb.similarity


def _state_signature(
    embedding: EmbeddingConfig,
    treedb: TreeDBConfig,
) -> dict[str, Any]:
    return {
        "embedding_provider": embedding.provider,
        "embedding_model": embedding.model,
        "embedding_dimension": embedding.dimension,
        "treedb_adapter": treedb.adapter,
        "treedb_base_url": treedb.base_url,
        "treedb_index": treedb.index,
        "treedb_similarity": treedb.similarity,
    }


def _string_field(data: dict[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str):
        raise IndexStateError(f"index state field {field_name!r} must be a string")
    return value


def _int_field(data: dict[str, Any], field_name: str) -> int:
    value = data.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise IndexStateError(f"index state field {field_name!r} must be an integer")
    return value


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise IndexStateError(f"index state field {field_name!r} must be an integer")
    return value


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndexStateError(f"index state field {field_name!r} must be a number")
    return float(value)
