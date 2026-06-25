from __future__ import annotations

import hashlib
import json
import platform
import shutil
import statistics
import tempfile
import time
import tracemalloc
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chunking import chunk_document
from .config import (
    AnswerGeneratorConfig,
    ProjectConfig,
    SourceConfig,
    TreeDBConfig,
    Workspace,
    init_workspace,
    read_config,
    write_config,
)
from .indexing import index_workspace
from .retrieval import search_workspace
from .sources import scan_source
from .treedb_adapter import InMemoryTreeDBAdapter
from .ui import UISettings, create_ui_server, server_url

FIXTURE_MANIFEST = "fixture-manifest.json"
DEFAULT_QUERY = "TreeDB project memory indexing citations"


@dataclass(frozen=True)
class FixtureShape:
    file_count: int = 24
    paragraphs_per_file: int = 6
    jsonl_rows: int = 12
    words_per_paragraph: int = 80


def generate_fixture_dataset(root: Path, shape: FixtureShape) -> dict[str, Any]:
    """Create deterministic source material for repeatable CLI benchmarks."""

    if shape.file_count <= 0:
        raise ValueError("file_count must be positive")
    if shape.paragraphs_per_file <= 0:
        raise ValueError("paragraphs_per_file must be positive")
    if shape.jsonl_rows < 0:
        raise ValueError("jsonl_rows must be non-negative")
    if shape.words_per_paragraph <= 0:
        raise ValueError("words_per_paragraph must be positive")

    root.mkdir(parents=True, exist_ok=True)
    docs = root / "docs"
    records = root / "records"
    shutil.rmtree(docs, ignore_errors=True)
    shutil.rmtree(records, ignore_errors=True)
    docs.mkdir(parents=True, exist_ok=True)
    records.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for index in range(shape.file_count):
        path = docs / f"topic-{index:04d}.md"
        path.write_text(
            _markdown_fixture(index, shape.paragraphs_per_file, shape.words_per_paragraph),
            encoding="utf-8",
        )
        written.append(path)

    jsonl_path = records / "notes.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for index in range(shape.jsonl_rows):
            row = {
                "id": f"record-{index:04d}",
                "title": f"Benchmark note {index}",
                "content": _paragraph(index, 0, shape.words_per_paragraph),
            }
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    written.append(jsonl_path)

    manifest = _fixture_manifest(root, written, shape)
    (root / FIXTURE_MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def validate_fixture_dataset(root: Path) -> dict[str, Any]:
    manifest_path = root / FIXTURE_MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"fixture manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("fixture manifest files must be a list")
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("fixture manifest file entries must be objects")
        path = root / str(entry.get("path", ""))
        if not path.is_file():
            raise ValueError(f"fixture file missing: {path}")
        digest = _sha256_bytes(path.read_bytes())
        if digest != entry.get("sha256"):
            raise ValueError(f"fixture checksum mismatch: {path}")
    return manifest


def run_ingest_benchmark(
    output_dir: Path,
    *,
    shape: FixtureShape,
    runs: int = 1,
) -> dict[str, Any]:
    if runs <= 0:
        raise ValueError("runs must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = output_dir / "fixture"
    manifest = generate_fixture_dataset(fixture_root, shape)
    validate_fixture_dataset(fixture_root)

    run_reports = []
    for run_index in range(runs):
        workspace_root = output_dir / f"workspace-run-{run_index + 1}"
        shutil.rmtree(workspace_root, ignore_errors=True)
        workspace, config = _prepare_workspace(workspace_root, fixture_root)
        adapter = InMemoryTreeDBAdapter(
            config.treedb,
            embedding_dimension=config.embedding.dimension,
        )
        progress_events: list[dict[str, Any]] = []

        dry_run, dry_metrics = _measure(lambda: _build_dry_run_report(config))
        index_report, index_metrics = _measure(
            lambda: index_workspace(
                workspace,
                config,
                adapter_factory=_adapter_factory(adapter),
                progress_callback=progress_events.append,
            )
        )
        state_bytes = _directory_size(workspace.state_dir)
        run_reports.append(
            {
                "run": run_index + 1,
                "dry_run": _ingest_metrics(dry_run, dry_metrics),
                "index": _ingest_metrics(index_report, index_metrics),
                "state_bytes": state_bytes,
                "workspace_metadata_bytes": _directory_size(workspace.metadata_dir),
                "progress_events": progress_events,
            }
        )

    report = {
        "benchmark": "ingest",
        "hardware": hardware_context(),
        "dataset": _dataset_summary(manifest),
        "fixture_manifest": str(fixture_root / FIXTURE_MANIFEST),
        "runs": run_reports,
        "summary": _summarize_ingest(run_reports),
    }
    _write_report(output_dir, "ingest", report)
    return report


def run_retrieval_benchmark(
    output_dir: Path,
    *,
    shape: FixtureShape,
    queries: list[str] | None = None,
    repetitions: int = 10,
) -> dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_root = output_dir / "fixture"
    manifest = generate_fixture_dataset(fixture_root, shape)
    validate_fixture_dataset(fixture_root)

    workspace_root = output_dir / "workspace"
    shutil.rmtree(workspace_root, ignore_errors=True)
    workspace, config = _prepare_workspace(workspace_root, fixture_root)
    adapter = InMemoryTreeDBAdapter(
        config.treedb,
        embedding_dimension=config.embedding.dimension,
    )
    index_report = index_workspace(
        workspace,
        config,
        adapter_factory=_adapter_factory(adapter),
    )

    query_list = queries or [DEFAULT_QUERY]
    measurements: list[dict[str, Any]] = []
    for query in query_list:
        latencies: list[float] = []
        result_counts: list[int] = []
        for _ in range(repetitions):
            started = time.perf_counter()
            results, trace = search_workspace(
                workspace,
                config,
                query=query,
                mode="keyword",
                top_k=5,
                adapter_factory=_adapter_factory(adapter),
            )
            latencies.append(time.perf_counter() - started)
            result_counts.append(len(results))
        measurements.append(
            {
                "query": query,
                "mode": "keyword",
                "repetitions": repetitions,
                "result_count_min": min(result_counts),
                "result_count_max": max(result_counts),
                "p50_seconds": _percentile(latencies, 50),
                "p95_seconds": _percentile(latencies, 95),
                "last_trace": trace.to_json(),
            }
        )

    report = {
        "benchmark": "retrieval",
        "hardware": hardware_context(),
        "dataset": _dataset_summary(manifest),
        "fixture_manifest": str(fixture_root / FIXTURE_MANIFEST),
        "index": {
            "chunks": index_report["upserted_chunks"],
            "elapsed_seconds": index_report["elapsed_seconds"],
        },
        "measurements": measurements,
        "storage": {
            "state_bytes": _directory_size(workspace.state_dir),
            "workspace_metadata_bytes": _directory_size(workspace.metadata_dir),
        },
    }
    _write_report(output_dir, "retrieval", report)
    return report


def run_ui_smoke_benchmark(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    server = create_ui_server(UISettings(port=0))
    url = server_url(server)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        timings = {}
        for path in ["api/health", ""]:
            started = time.perf_counter()
            with urllib.request.urlopen(url + path, timeout=5) as response:
                body = response.read()
            timings[path or "root"] = {
                "status": response.status,
                "bytes": len(body),
                "elapsed_seconds": time.perf_counter() - started,
            }
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    report = {
        "benchmark": "ui-smoke",
        "hardware": hardware_context(),
        "url": url,
        "requests": timings,
    }
    _write_report(output_dir, "ui-smoke", report)
    return report


def hardware_context() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
    }


def _prepare_workspace(workspace_root: Path, fixture_root: Path) -> tuple[Workspace, ProjectConfig]:
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = init_workspace(workspace_root, "benchmark", force=True)
    config = read_config(workspace)
    config.treedb = TreeDBConfig(adapter="memory")
    config.answer_generator = AnswerGeneratorConfig(provider="extractive")
    config.retrieval.default_mode = "keyword"
    config.sources["docs"] = SourceConfig(
        id="docs",
        type="folder",
        root=str(fixture_root / "docs"),
        include=["**/*.md"],
        exclude=[],
    )
    config.sources["records"] = SourceConfig(
        id="records",
        type="jsonl",
        root=str(fixture_root / "records" / "notes.jsonl"),
        include=["**/*.jsonl"],
        exclude=[],
        content_field="content",
    )
    write_config(workspace, config)
    return workspace, read_config(workspace)


def _adapter_factory(adapter: InMemoryTreeDBAdapter):
    def factory(_treedb: TreeDBConfig, *, embedding_dimension: int):
        if embedding_dimension != adapter.embedding_dimension:
            raise ValueError("benchmark adapter embedding dimension mismatch")
        return adapter

    return factory


def _build_dry_run_report(config: ProjectConfig) -> dict[str, Any]:
    source_reports = []
    total_files = 0
    total_documents = 0
    total_chunks = 0
    total_skipped = 0
    warnings = []
    for source in config.sources.values():
        scan = scan_source(source)
        chunks = []
        for document in scan.documents:
            chunks.extend(chunk_document(document, config.workspace))
        scan_warnings = [skip.to_json() for skip in scan.warnings()]
        source_reports.append(
            {
                "id": source.id,
                "files_scanned": scan.files_scanned,
                "documents": len(scan.documents),
                "chunks": len(chunks),
                "skipped": len(scan.skipped),
                "warnings": scan_warnings,
            }
        )
        total_files += scan.files_scanned
        total_documents += len(scan.documents)
        total_chunks += len(chunks)
        total_skipped += len(scan.skipped)
        warnings.extend(scan_warnings)
    return {
        "dry_run": True,
        "workspace": config.workspace,
        "sources": source_reports,
        "file_count": total_files,
        "document_count": total_documents,
        "chunk_count": total_chunks,
        "skip_count": total_skipped,
        "warnings": warnings,
    }


def _measure(action):
    tracemalloc.start()
    started = time.perf_counter()
    result = action()
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {
        "elapsed_seconds": elapsed,
        "tracemalloc_current_bytes": current,
        "tracemalloc_peak_bytes": peak,
        "max_rss_kib": _max_rss_kib(),
    }


def _ingest_metrics(report: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    elapsed = max(metrics["elapsed_seconds"], 1e-9)
    return {
        "elapsed_seconds": metrics["elapsed_seconds"],
        "files": report["file_count"],
        "documents": report["document_count"],
        "chunks": report["chunk_count"],
        "files_per_second": report["file_count"] / elapsed,
        "chunks_per_second": report["chunk_count"] / elapsed,
        "tracemalloc_peak_bytes": metrics["tracemalloc_peak_bytes"],
        "max_rss_kib": metrics["max_rss_kib"],
    }


def _summarize_ingest(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dry_run_elapsed_seconds_avg": statistics.mean(
            run["dry_run"]["elapsed_seconds"] for run in runs
        ),
        "index_elapsed_seconds_avg": statistics.mean(
            run["index"]["elapsed_seconds"] for run in runs
        ),
        "index_chunks_per_second_avg": statistics.mean(
            run["index"]["chunks_per_second"] for run in runs
        ),
        "state_bytes_max": max(run["state_bytes"] for run in runs),
    }


def _write_report(output_dir: Path, name: str, report: dict[str, Any]) -> None:
    json_path = output_dir / f"{name}_results.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path = output_dir / f"{name}_results.md"
    md_path.write_text(_markdown_report(report), encoding="utf-8")


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [f"# {report['benchmark']} benchmark", "", "```json"]
    lines.append(json.dumps(report, indent=2, sort_keys=True))
    lines.extend(["```", ""])
    return "\n".join(lines)


def _fixture_manifest(root: Path, files: list[Path], shape: FixtureShape) -> dict[str, Any]:
    entries = []
    total_bytes = 0
    for path in sorted(files):
        data = path.read_bytes()
        total_bytes += len(data)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(data),
                "sha256": _sha256_bytes(data),
            }
        )
    return {
        "schema": "treedb-project-memory-fixture-v1",
        "shape": {
            "file_count": shape.file_count,
            "paragraphs_per_file": shape.paragraphs_per_file,
            "jsonl_rows": shape.jsonl_rows,
            "words_per_paragraph": shape.words_per_paragraph,
        },
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
    }


def _dataset_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": manifest["schema"],
        "shape": manifest["shape"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
    }


def _markdown_fixture(index: int, paragraphs: int, words: int) -> str:
    lines = [f"# Benchmark topic {index}", ""]
    for paragraph in range(paragraphs):
        lines.append(_paragraph(index, paragraph, words))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _paragraph(index: int, paragraph: int, words: int) -> str:
    vocabulary = [
        "TreeDB",
        "project",
        "memory",
        "indexing",
        "retrieval",
        "citation",
        "workspace",
        "chunk",
        "embedding",
        "benchmark",
    ]
    return " ".join(
        vocabulary[(index + paragraph + offset) % len(vocabulary)]
        for offset in range(words)
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1)))
    return ordered[index]


def _max_rss_kib() -> int | None:
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return int(usage / 1024)
    return int(usage)


def temp_output_dir(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
