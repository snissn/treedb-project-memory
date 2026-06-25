from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .chunking import Chunk, chunk_document
from .config import ProjectConfig, SourceConfig, Workspace, WorkspaceError
from .embedding import EmbeddingError, EmbeddingProvider, create_embedding_provider
from .sources import SourceDocument, SourceScanResult, scan_source
from .state import (
    FileIndexState,
    IndexStateError,
    SourceIndexState,
    index_state_path,
    load_index_state,
    refresh_state_identity,
    save_index_state,
    validate_state_compatible,
)
from .treedb_adapter import (
    IndexDocument,
    TreeDBAdapter,
    TreeDBAdapterError,
    create_treedb_adapter,
)

ProviderFactory = Callable[[Any], EmbeddingProvider]
AdapterFactory = Callable[..., TreeDBAdapter]


class IndexingError(Exception):
    """Raised when non-dry-run indexing cannot complete."""


@dataclass(frozen=True)
class PlannedFile:
    path: str
    document_hash: str
    chunks: list[Chunk]
    mtime: float | None
    size_bytes: int | None

    @property
    def chunk_ids(self) -> list[str]:
        return [chunk.chunk_id for chunk in self.chunks]


@dataclass(frozen=True)
class UpsertItem:
    source_id: str
    planned_file: PlannedFile
    chunk: Chunk


def index_workspace(
    workspace: Workspace,
    config: ProjectConfig,
    *,
    source_id: str | None = None,
    rebuild: bool = False,
    provider_factory: ProviderFactory = create_embedding_provider,
    adapter_factory: AdapterFactory = create_treedb_adapter,
) -> dict[str, Any]:
    """Index changed chunks into TreeDB and persist local incremental state."""

    started = time.perf_counter()
    sources = _select_sources(config, source_id)
    state = _load_state(workspace, config)
    _validate_state(state, config, rebuild=rebuild)

    target_source_ids = {source.id for source in sources}
    rebuild_delete_ids = state.chunk_ids_for_sources(target_source_ids) if rebuild else []
    source_plans: list[dict[str, Any]] = []
    delete_ids: list[str] = list(rebuild_delete_ids)
    upsert_items: list[UpsertItem] = []
    report_sources: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for source in sources:
        scan = scan_source(source)
        planned_files = _planned_files(scan, config.workspace)
        previous_files = (
            {}
            if rebuild
            else dict(state.sources.get(source.id, SourceIndexState()).files)
        )
        source_unavailable = _source_unavailable(scan)
        deleted_paths: list[str] = []
        changed_paths: list[str] = []
        unchanged_paths: list[str] = []
        source_delete_ids: list[str] = []
        source_upsert_items: list[UpsertItem] = []

        if not source_unavailable:
            for path, previous in sorted(previous_files.items()):
                if path not in planned_files:
                    deleted_paths.append(path)
                    source_delete_ids.extend(previous.chunk_ids)

        for path, planned in sorted(planned_files.items()):
            previous = previous_files.get(path)
            if _planned_file_unchanged(previous, planned):
                unchanged_paths.append(path)
                continue
            changed_paths.append(path)
            if previous is not None:
                source_delete_ids.extend(previous.chunk_ids)
            source_upsert_items.extend(
                UpsertItem(source_id=source.id, planned_file=planned, chunk=chunk)
                for chunk in planned.chunks
            )

        delete_ids.extend(source_delete_ids)
        upsert_items.extend(source_upsert_items)
        scan_warnings = [skip.to_json() for skip in scan.warnings()]
        warnings.extend(scan_warnings)
        source_report = _source_index_report(
            source=source,
            scan=scan,
            planned_files=planned_files,
            unchanged_paths=unchanged_paths,
            changed_paths=changed_paths,
            deleted_paths=deleted_paths,
            source_delete_ids=source_delete_ids,
            source_upsert_items=source_upsert_items,
            source_unavailable=source_unavailable,
        )
        report_sources.append(source_report)
        source_plans.append(
            {
                "source": source,
                "planned_files": planned_files,
                "changed_paths": changed_paths,
                "deleted_paths": deleted_paths,
                "source_unavailable": source_unavailable,
            }
        )

    adapter = _create_adapter(config, adapter_factory)
    index_documents: list[IndexDocument] = []
    indexed_at = _utc_now()
    if upsert_items:
        provider = _create_provider(config, provider_factory)
        try:
            index_documents = _embed_index_documents(upsert_items, provider, indexed_at)
        except EmbeddingError as exc:
            raise IndexingError(str(exc)) from exc

    unique_delete_ids = list(dict.fromkeys(delete_ids))
    deleted_chunks = 0
    upserted_chunks = 0
    try:
        if unique_delete_ids:
            deleted = adapter.delete_documents(unique_delete_ids)
            deleted_chunks = len(unique_delete_ids) if deleted is None else int(deleted)
        if index_documents:
            upserted_chunks = adapter.upsert_documents(index_documents)
        adapter_document_count = adapter.count_documents()
    except TreeDBAdapterError as exc:
        raise IndexingError(str(exc)) from exc

    if rebuild:
        state.reset_sources(target_source_ids)
    refresh_state_identity(state, config)
    for plan in source_plans:
        if plan["source_unavailable"]:
            continue
        source_state = state.source(plan["source"].id)
        for path in plan["deleted_paths"]:
            source_state.files.pop(path, None)
        for path in plan["changed_paths"]:
            planned = plan["planned_files"][path]
            source_state.files[path] = FileIndexState(
                document_hash=planned.document_hash,
                chunk_ids=planned.chunk_ids,
                chunk_count=len(planned.chunks),
                mtime=planned.mtime,
                size_bytes=planned.size_bytes,
                indexed_at=indexed_at,
            )
    save_index_state(workspace, state)

    elapsed = time.perf_counter() - started
    return _index_report(
        config=config,
        workspace=workspace,
        sources=report_sources,
        warnings=warnings,
        dry_run=False,
        rebuild=rebuild,
        elapsed_seconds=elapsed,
        deleted_chunks=deleted_chunks,
        upserted_chunks=upserted_chunks,
        adapter_document_count=adapter_document_count,
    )


def status_workspace(
    workspace: Workspace,
    config: ProjectConfig,
    *,
    source_id: str | None = None,
    check_service: bool = False,
    adapter_factory: AdapterFactory = create_treedb_adapter,
) -> dict[str, Any]:
    sources = _select_sources(config, source_id)
    state = _load_state(workspace, config)
    compatibility_warning: dict[str, Any] | None = None
    try:
        validate_state_compatible(state, config, rebuild=False)
    except IndexStateError as exc:
        compatibility_warning = {
            "code": "index_state_config_mismatch",
            "message": str(exc),
        }

    report_sources = []
    warnings: list[dict[str, Any]] = []
    for source in sources:
        scan = scan_source(source)
        planned_files = _planned_files(scan, config.workspace)
        previous_files = dict(state.sources.get(source.id, SourceIndexState()).files)
        source_unavailable = _source_unavailable(scan)
        deleted_paths: list[str] = []
        changed_paths: list[str] = []
        unchanged_paths: list[str] = []

        if not source_unavailable:
            deleted_paths = sorted(path for path in previous_files if path not in planned_files)
        for path, planned in sorted(planned_files.items()):
            previous = previous_files.get(path)
            if _planned_file_unchanged(previous, planned):
                unchanged_paths.append(path)
            else:
                changed_paths.append(path)

        scan_warnings = [skip.to_json() for skip in scan.warnings()]
        warnings.extend(scan_warnings)
        indexed_chunks = sum(file.chunk_count for file in previous_files.values())
        report_sources.append(
            {
                "id": source.id,
                "type": source.type,
                "root": source.root,
                "root_exists": not source_unavailable,
                "files_scanned": scan.files_scanned,
                "current_files": len(planned_files),
                "current_chunks": sum(len(file.chunks) for file in planned_files.values()),
                "indexed_files": len(previous_files),
                "indexed_chunks": indexed_chunks,
                "unchanged_files": len(unchanged_paths),
                "changed_files": len(changed_paths),
                "deleted_files": len(deleted_paths),
                "skipped": len(scan.skipped),
                "warnings": scan_warnings,
            }
        )

    if compatibility_warning is not None:
        warnings.insert(0, compatibility_warning)
    report = {
        "workspace": config.workspace,
        "workspace_root": str(workspace.root),
        "state_path": str(index_state_path(workspace)),
        "state_exists": index_state_path(workspace).exists(),
        "embedding": {
            "provider": state.embedding_provider,
            "model": state.embedding_model,
            "dimension": state.embedding_dimension,
        },
        "treedb": {
            "adapter": state.treedb_adapter,
            "base_url": state.treedb_base_url,
            "index": state.treedb_index,
            "similarity": state.treedb_similarity,
            "service_lifecycle": config.treedb.service_lifecycle,
        },
        "sources": report_sources,
        "source_count": len(report_sources),
        "indexed_file_count": sum(source["indexed_files"] for source in report_sources),
        "indexed_chunk_count": sum(source["indexed_chunks"] for source in report_sources),
        "current_file_count": sum(source["current_files"] for source in report_sources),
        "current_chunk_count": sum(source["current_chunks"] for source in report_sources),
        "changed_file_count": sum(source["changed_files"] for source in report_sources),
        "deleted_file_count": sum(source["deleted_files"] for source in report_sources),
        "warning_count": len(warnings),
        "warnings": warnings,
    }
    if check_service:
        try:
            adapter = _create_adapter(config, adapter_factory)
            report["treedb"]["adapter_document_count"] = adapter.count_documents()
            report["treedb"]["health"] = adapter.health()
        except (IndexingError, TreeDBAdapterError) as exc:
            warning = {
                "code": "treedb_status_unavailable",
                "message": str(exc),
            }
            report["warnings"].append(warning)
            report["warning_count"] = len(report["warnings"])
            report["treedb"]["health"] = {"ok": False, "error": str(exc)}
    return report


def _select_sources(config: ProjectConfig, source_id: str | None) -> list[SourceConfig]:
    if source_id is not None and source_id not in config.sources:
        raise WorkspaceError(f"source ID '{source_id}' is not configured")
    return (
        [config.sources[source_id]]
        if source_id is not None
        else list(config.sources.values())
    )


def _load_state(workspace: Workspace, config: ProjectConfig):
    try:
        return load_index_state(workspace, config)
    except IndexStateError as exc:
        raise IndexingError(str(exc)) from exc


def _validate_state(state: Any, config: ProjectConfig, *, rebuild: bool) -> None:
    try:
        validate_state_compatible(state, config, rebuild=rebuild)
    except IndexStateError as exc:
        raise IndexingError(str(exc)) from exc


def _create_adapter(
    config: ProjectConfig,
    adapter_factory: AdapterFactory,
) -> TreeDBAdapter:
    try:
        return adapter_factory(
            config.treedb,
            embedding_dimension=config.embedding.dimension,
        )
    except TreeDBAdapterError as exc:
        raise IndexingError(str(exc)) from exc


def _create_provider(
    config: ProjectConfig,
    provider_factory: ProviderFactory,
) -> EmbeddingProvider:
    try:
        provider = provider_factory(config.embedding)
    except EmbeddingError as exc:
        raise IndexingError(str(exc)) from exc
    if provider.dimension != config.embedding.dimension:
        raise IndexingError(
            f"embedding provider dimension mismatch: provider has {provider.dimension}, "
            f"config has {config.embedding.dimension}"
        )
    return provider


def _planned_files(
    scan: SourceScanResult,
    workspace_id: str,
) -> dict[str, PlannedFile]:
    documents_by_path: dict[str, list[SourceDocument]] = defaultdict(list)
    for document in scan.documents:
        documents_by_path[document.path].append(document)

    planned: dict[str, PlannedFile] = {}
    for path, documents in sorted(documents_by_path.items()):
        chunks: list[Chunk] = []
        document_hashes: list[str] = []
        mtimes: list[float] = []
        sizes: list[int] = []
        for document in documents:
            document_hashes.append(_document_state_hash(document, workspace_id))
            if document.mtime is not None:
                mtimes.append(document.mtime)
            if document.size_bytes is not None:
                sizes.append(document.size_bytes)
            chunks.extend(chunk_document(document, workspace_id))
        if not chunks:
            continue
        planned[path] = PlannedFile(
            path=path,
            document_hash=_combined_hash(document_hashes, [chunk.chunk_id for chunk in chunks]),
            chunks=chunks,
            mtime=max(mtimes) if mtimes else None,
            size_bytes=max(sizes) if sizes else None,
        )
    return planned


def _planned_file_unchanged(
    previous: FileIndexState | None,
    planned: PlannedFile,
) -> bool:
    return (
        previous is not None
        and previous.document_hash == planned.document_hash
        and previous.chunk_ids == planned.chunk_ids
    )


def _document_state_hash(document: SourceDocument, workspace_id: str) -> str:
    digest = hashlib.sha256()
    fields = [
        workspace_id,
        document.source_id,
        document.source_type,
        document.source_root,
        document.path,
        document.absolute_path,
        document.document_hash,
        document.document_kind,
        str(document.start_line),
        str(document.end_line),
    ]
    for field in fields:
        digest.update(field.encode("utf-8"))
        digest.update(b"\0")
    for key, value in sorted(document.metadata.items()):
        digest.update(str(key).encode("utf-8"))
        digest.update(b"=")
        digest.update(repr(value).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _combined_hash(document_hashes: list[str], chunk_ids: list[str]) -> str:
    digest = hashlib.sha256()
    for value in document_hashes:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(b"\1")
    for value in chunk_ids:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _source_unavailable(scan: SourceScanResult) -> bool:
    return any(
        skip.warning and skip.path == "." and skip.code in {"missing_root", "symlink"}
        for skip in scan.skipped
    )


def _embed_index_documents(
    upsert_items: list[UpsertItem],
    provider: EmbeddingProvider,
    indexed_at: str,
) -> list[IndexDocument]:
    vectors = provider.embed([item.chunk.content for item in upsert_items])
    if len(vectors) != len(upsert_items):
        raise IndexingError(
            f"embedding provider returned {len(vectors)} vectors for {len(upsert_items)} chunks"
        )
    documents: list[IndexDocument] = []
    for item, vector in zip(upsert_items, vectors):
        if len(vector) != provider.dimension:
            raise IndexingError(
                f"embedding dimension mismatch for chunk {item.chunk.chunk_id}: "
                f"got {len(vector)}, expected {provider.dimension}"
            )
        meta = dict(item.chunk.metadata)
        meta.update(
            {
                "indexed_at": indexed_at,
                "embedding_provider": provider.provider,
                "embedding_model": provider.model,
                "embedding_dimension": provider.dimension,
            }
        )
        documents.append(
            IndexDocument(
                id=item.chunk.chunk_id,
                content=item.chunk.content,
                embedding=list(vector),
                meta=meta,
            )
        )
    return documents


def _source_index_report(
    *,
    source: SourceConfig,
    scan: SourceScanResult,
    planned_files: dict[str, PlannedFile],
    unchanged_paths: list[str],
    changed_paths: list[str],
    deleted_paths: list[str],
    source_delete_ids: list[str],
    source_upsert_items: list[UpsertItem],
    source_unavailable: bool,
) -> dict[str, Any]:
    chunk_kinds: dict[str, int] = {}
    for planned in planned_files.values():
        for chunk in planned.chunks:
            kind = chunk.metadata["chunk_kind"]
            chunk_kinds[kind] = chunk_kinds.get(kind, 0) + 1
    warnings = [skip.to_json() for skip in scan.warnings()]
    return {
        "id": source.id,
        "type": source.type,
        "root": source.root,
        "root_exists": not source_unavailable,
        "files_scanned": scan.files_scanned,
        "documents": len(scan.documents),
        "chunks": sum(len(planned.chunks) for planned in planned_files.values()),
        "unchanged_files": len(unchanged_paths),
        "changed_files": len(changed_paths),
        "deleted_files": len(deleted_paths),
        "upserted_chunks_planned": len(source_upsert_items),
        "deleted_chunks_planned": len(dict.fromkeys(source_delete_ids)),
        "skipped": len(scan.skipped),
        "warnings": warnings,
        "chunk_kinds": dict(sorted(chunk_kinds.items())),
    }


def _index_report(
    *,
    config: ProjectConfig,
    workspace: Workspace,
    sources: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    dry_run: bool,
    rebuild: bool,
    elapsed_seconds: float,
    deleted_chunks: int,
    upserted_chunks: int,
    adapter_document_count: int,
) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "rebuild": rebuild,
        "workspace": config.workspace,
        "workspace_root": str(workspace.root),
        "state_path": str(index_state_path(workspace)),
        "embedding": config.embedding.to_json(),
        "treedb": config.treedb.to_json(),
        "sources": sources,
        "source_count": len(sources),
        "file_count": sum(source["files_scanned"] for source in sources),
        "document_count": sum(source["documents"] for source in sources),
        "chunk_count": sum(source["chunks"] for source in sources),
        "unchanged_file_count": sum(source["unchanged_files"] for source in sources),
        "changed_file_count": sum(source["changed_files"] for source in sources),
        "deleted_file_count": sum(source["deleted_files"] for source in sources),
        "skip_count": sum(source["skipped"] for source in sources),
        "warning_count": len(warnings),
        "warnings": warnings,
        "upserted_chunks": upserted_chunks,
        "deleted_chunks": deleted_chunks,
        "adapter_document_count": adapter_document_count,
        "elapsed_seconds": elapsed_seconds,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
