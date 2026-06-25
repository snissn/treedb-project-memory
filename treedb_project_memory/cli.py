import json
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .answers import AnswerError, ask_workspace
from .chunking import chunk_document
from .config import (
    VALID_SOURCE_TYPES,
    WorkspaceError,
    add_source,
    discover_workspace,
    doctor_report,
    init_workspace,
    read_config,
)
from .indexing import IndexingError, index_workspace, status_workspace
from .retrieval import RetrievalError, search_workspace
from .sources import scan_source

app = typer.Typer(
    add_completion=False,
    help=(
        "Local project-memory workspace tooling with source scanning and "
        "TreeDB/Haystack indexing."
    ),
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"treedb-project-memory {__version__}")
        raise typer.Exit()


@app.callback()
def bootstrap(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        help="Show the package version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Show bootstrap CLI help and version information."""


@app.command()
def init(
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        help="Workspace display name to store in config.yaml.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing workspace config.",
    ),
) -> None:
    """Create a workspace-local config and state directory."""
    try:
        created = init_workspace(Path.cwd(), workspace, force)
    except WorkspaceError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Initialized workspace at {created.metadata_dir}")


@app.command()
def add(
    root: Path = typer.Argument(..., help="Local source root or file to add."),
    source_type: Optional[str] = typer.Option(
        None,
        "--type",
        help=f"Source type ({', '.join(sorted(VALID_SOURCE_TYPES))}).",
    ),
    source_id: Optional[str] = typer.Option(
        None,
        "--id",
        help="Stable source ID to store in config.yaml.",
    ),
    include: list[str] = typer.Option(
        [],
        "--include",
        help="Glob to include. Repeat to add multiple patterns.",
    ),
    exclude: list[str] = typer.Option(
        [],
        "--exclude",
        help="Glob to exclude. Repeat to add multiple patterns.",
    ),
    max_file_bytes: Optional[int] = typer.Option(
        None,
        "--max-file-bytes",
        help="Largest source file to read during scanning.",
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Follow symlinked files and directories when scanning this source.",
    ),
    content_field: Optional[str] = typer.Option(
        None,
        "--content-field",
        help="JSONL string field to use as chunk content.",
    ),
) -> None:
    """Add a source entry to the workspace config without indexing it."""
    try:
        workspace = discover_workspace()
        source = add_source(
            workspace,
            root,
            source_type,
            source_id,
            include,
            exclude,
            max_file_bytes=max_file_bytes,
            follow_symlinks=follow_symlinks,
            content_field=content_field,
        )
    except WorkspaceError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Added source {source.id} ({source.type}) -> {source.root}")


@app.command()
def sources(
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """List configured sources."""
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")
    try:
        workspace = discover_workspace()
        config = read_config(workspace)
    except WorkspaceError as exc:
        raise typer.BadParameter(str(exc)) from exc

    payload = {
        "workspace": config.workspace,
        "workspace_root": str(workspace.root),
        "sources": [source.to_json() for source in config.sources.values()],
    }
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not config.sources:
        typer.echo("No sources configured.")
        return
    for source in config.sources.values():
        status = "exists" if source.to_json()["exists"] else "missing"
        typer.echo(f"{source.id}\t{source.type}\t{status}\t{source.root}")


@app.command(name="index")
def index_command(
    source_id: Optional[str] = typer.Option(
        None,
        "--source",
        help="Scan only one configured source ID.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Scan and chunk sources without writing TreeDB or embedding state.",
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Delete prior chunk IDs for selected sources and rebuild local index state.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Alias for --format json.",
    ),
) -> None:
    """Scan configured sources and index changed chunks into TreeDB."""
    if json_output:
        output_format = "json"
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")

    try:
        workspace = discover_workspace()
        config = read_config(workspace)
        if dry_run:
            report = build_dry_run_report(config, source_id)
        else:
            report = index_workspace(
                workspace,
                config,
                source_id=source_id,
                rebuild=rebuild,
            )
    except (WorkspaceError, IndexingError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    if output_format == "json":
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    if dry_run:
        _print_dry_run_report(report)
    else:
        _print_index_report(report)


@app.command()
def status(
    source_id: Optional[str] = typer.Option(
        None,
        "--source",
        help="Report only one configured source ID.",
    ),
    check_service: bool = typer.Option(
        False,
        "--check-service",
        help="Also instantiate the TreeDB adapter and report service health/count.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Alias for --format json.",
    ),
) -> None:
    """Report local source and index state."""
    if json_output:
        output_format = "json"
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")
    try:
        workspace = discover_workspace()
        config = read_config(workspace)
        report = status_workspace(
            workspace,
            config,
            source_id=source_id,
            check_service=check_service,
        )
    except (WorkspaceError, IndexingError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    if output_format == "json":
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    _print_status_report(report)


@app.command()
def search(
    query: str = typer.Argument(..., help="Query text to retrieve from indexed documents."),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="Retrieval mode: semantic, keyword, or hybrid. Defaults to config retrieval.default_mode.",
    ),
    top_k: Optional[int] = typer.Option(
        None,
        "--top-k",
        help="Maximum number of retrieval results. Defaults to config retrieval.top_k.",
    ),
    source_id: Optional[str] = typer.Option(
        None,
        "--source",
        help="Filter to one configured source ID when the selected retriever supports it.",
    ),
    explain: bool = typer.Option(
        False,
        "--explain",
        help="Include retrieval trace details.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Alias for --format json.",
    ),
) -> None:
    """Retrieve cited indexed chunks without requiring an answer generator."""
    if json_output:
        output_format = "json"
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")
    try:
        workspace = discover_workspace()
        config = read_config(workspace)
        results, trace = search_workspace(
            workspace,
            config,
            query=query,
            mode=mode,
            top_k=top_k,
            source_id=source_id,
        )
    except (WorkspaceError, RetrievalError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    payload = {
        "query": query,
        "mode": trace.mode,
        "results": [result.to_json() for result in results],
    }
    if explain:
        payload["trace"] = trace.to_json()
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    _print_search_results(payload, explain=explain)


@app.command()
def ask(
    query: str = typer.Argument(..., help="Question to answer from indexed documents."),
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help="Retrieval mode: semantic, keyword, or hybrid. Defaults to config retrieval.default_mode.",
    ),
    top_k: Optional[int] = typer.Option(
        None,
        "--top-k",
        help="Maximum number of retrieval results. Defaults to config retrieval.top_k.",
    ),
    source_id: Optional[str] = typer.Option(
        None,
        "--source",
        help="Filter to one configured source ID when the selected retriever supports it.",
    ),
    explain: bool = typer.Option(
        False,
        "--explain",
        help="Include retrieval trace details.",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Alias for --format json.",
    ),
) -> None:
    """Answer from retrieved indexed chunks when a generator is configured."""
    if json_output:
        output_format = "json"
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")
    try:
        workspace = discover_workspace()
        config = read_config(workspace)
        answer = ask_workspace(
            workspace,
            config,
            query=query,
            mode=mode,
            top_k=top_k,
            source_id=source_id,
        )
    except (WorkspaceError, RetrievalError, AnswerError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    payload = answer.to_json()
    if not explain:
        payload.pop("trace", None)
    if output_format == "json":
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(answer.answer)
    if answer.citations:
        typer.echo("Citations:")
        for citation in answer.citations:
            typer.echo(f"- {citation['label']}")
    if explain:
        _print_trace(answer.trace.to_json())


@app.command()
def doctor(
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Validate workspace config, optional dependencies, and source roots."""
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be 'text' or 'json'")
    try:
        workspace = discover_workspace()
    except WorkspaceError as exc:
        report = {"ok": False, "errors": [{"code": "workspace_not_found", "message": str(exc)}]}
        if output_format == "json":
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
        else:
            typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1)

    report, exit_code = doctor_report(workspace)
    if output_format == "json":
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        typer.echo("Workspace config OK.")
        for warning in report.get("warnings", []):
            typer.echo(f"WARNING: {warning['message']}", err=True)
    else:
        for error in report["errors"]:
            typer.echo(f"ERROR: {error['message']}", err=True)
        for warning in report.get("warnings", []):
            typer.echo(f"WARNING: {warning['message']}", err=True)
    if exit_code:
        raise typer.Exit(exit_code)


def build_dry_run_report(config, source_id: str | None = None) -> dict:
    if source_id is not None and source_id not in config.sources:
        raise WorkspaceError(f"source ID '{source_id}' is not configured")

    sources = (
        [config.sources[source_id]]
        if source_id is not None
        else list(config.sources.values())
    )
    source_reports = []
    total_files = 0
    total_documents = 0
    total_chunks = 0
    total_skipped = 0
    all_warnings = []

    for source in sources:
        scan = scan_source(source)
        chunks = []
        chunk_kinds: dict[str, int] = {}
        for document in scan.documents:
            document_chunks = chunk_document(document, config.workspace)
            chunks.extend(document_chunks)
            for chunk in document_chunks:
                kind = chunk.metadata["chunk_kind"]
                chunk_kinds[kind] = chunk_kinds.get(kind, 0) + 1

        warnings = [skip.to_json() for skip in scan.warnings()]
        source_report = {
            "id": source.id,
            "type": source.type,
            "root": source.root,
            "files_scanned": scan.files_scanned,
            "documents": len(scan.documents),
            "chunks": len(chunks),
            "skipped": len(scan.skipped),
            "warnings": warnings,
            "chunk_kinds": dict(sorted(chunk_kinds.items())),
        }
        source_reports.append(source_report)
        total_files += scan.files_scanned
        total_documents += len(scan.documents)
        total_chunks += len(chunks)
        total_skipped += len(scan.skipped)
        all_warnings.extend(warnings)

    return {
        "dry_run": True,
        "workspace": config.workspace,
        "sources": source_reports,
        "file_count": total_files,
        "document_count": total_documents,
        "chunk_count": total_chunks,
        "skip_count": total_skipped,
        "warnings": all_warnings,
    }


def _print_dry_run_report(report: dict) -> None:
    typer.echo("Dry-run index summary")
    typer.echo(f"Workspace: {report['workspace']}")
    typer.echo(f"Sources: {len(report['sources'])}")
    typer.echo(f"Files scanned: {report['file_count']}")
    typer.echo(f"Documents: {report['document_count']}")
    typer.echo(f"Chunks: {report['chunk_count']}")
    typer.echo(f"Skipped: {report['skip_count']}")
    typer.echo(f"Warnings: {len(report['warnings'])}")
    for source in report["sources"]:
        typer.echo(
            f"{source['id']}\t{source['type']}\t"
            f"files={source['files_scanned']}\t"
            f"documents={source['documents']}\t"
            f"chunks={source['chunks']}\t"
            f"skipped={source['skipped']}\t"
            f"warnings={len(source['warnings'])}"
        )
    if report["warnings"]:
        typer.echo("Warnings:")
        for warning in report["warnings"]:
            line = f":{warning['line']}" if "line" in warning else ""
            typer.echo(
                f"- {warning['source_id']}:{warning['path']}{line} "
                f"[{warning['code']}] {warning['message']}"
            )


def _print_index_report(report: dict) -> None:
    typer.echo("Index summary")
    typer.echo(f"Workspace: {report['workspace']}")
    typer.echo(f"Sources: {report['source_count']}")
    typer.echo(f"Files scanned: {report['file_count']}")
    typer.echo(f"Documents: {report['document_count']}")
    typer.echo(f"Chunks: {report['chunk_count']}")
    typer.echo(f"Changed files: {report['changed_file_count']}")
    typer.echo(f"Unchanged files: {report['unchanged_file_count']}")
    typer.echo(f"Deleted files: {report['deleted_file_count']}")
    typer.echo(f"Upserted chunks: {report['upserted_chunks']}")
    typer.echo(f"Deleted chunks: {report['deleted_chunks']}")
    typer.echo(f"TreeDB documents: {report['adapter_document_count']}")
    typer.echo(f"State: {report['state_path']}")
    for source in report["sources"]:
        typer.echo(
            f"{source['id']}\t{source['type']}\t"
            f"files={source['files_scanned']}\t"
            f"chunks={source['chunks']}\t"
            f"changed={source['changed_files']}\t"
            f"unchanged={source['unchanged_files']}\t"
            f"deleted={source['deleted_files']}\t"
            f"warnings={len(source['warnings'])}"
        )
    _print_warning_list(report.get("warnings", []))


def _print_status_report(report: dict) -> None:
    typer.echo("Index status")
    typer.echo(f"Workspace: {report['workspace']}")
    typer.echo(f"State: {report['state_path']}")
    typer.echo(f"State exists: {report['state_exists']}")
    typer.echo(f"Indexed files: {report['indexed_file_count']}")
    typer.echo(f"Indexed chunks: {report['indexed_chunk_count']}")
    typer.echo(f"Current files: {report['current_file_count']}")
    typer.echo(f"Current chunks: {report['current_chunk_count']}")
    typer.echo(f"Changed files: {report['changed_file_count']}")
    typer.echo(f"Deleted files: {report['deleted_file_count']}")
    treedb = report["treedb"]
    typer.echo(f"TreeDB adapter: {treedb['adapter']}")
    typer.echo(f"TreeDB index: {treedb['index']}")
    if "adapter_document_count" in treedb:
        typer.echo(f"TreeDB documents: {treedb['adapter_document_count']}")
    for source in report["sources"]:
        typer.echo(
            f"{source['id']}\t{source['type']}\t"
            f"indexed_files={source['indexed_files']}\t"
            f"indexed_chunks={source['indexed_chunks']}\t"
            f"current_files={source['current_files']}\t"
            f"changed={source['changed_files']}\t"
            f"deleted={source['deleted_files']}\t"
            f"warnings={len(source['warnings'])}"
        )
    _print_warning_list(report.get("warnings", []))


def _print_search_results(payload: dict, *, explain: bool) -> None:
    typer.echo(f"Search mode: {payload['mode']}")
    for index, result in enumerate(payload["results"], start=1):
        citation = result.get("citation") or {}
        label = citation.get("label") or result["id"]
        score = "n/a" if result["score"] is None else f"{result['score']:.6g}"
        typer.echo(f"{index}. {result['id']} score={score} citation={label}")
        content = result.get("content", "").strip().replace("\n", " ")
        if content:
            typer.echo(f"   {content[:240]}")
    if explain and "trace" in payload:
        _print_trace(payload["trace"])


def _print_trace(trace: dict) -> None:
    typer.echo("Retrieval trace")
    typer.echo(f"Query: {trace['query']}")
    typer.echo(f"Mode: {trace['mode']}")
    typer.echo(f"Top K: {trace['top_k']}")
    typer.echo(f"Filters: {json.dumps(trace['filters'], sort_keys=True)}")
    typer.echo(f"Document IDs: {', '.join(trace['document_ids'])}")
    typer.echo(f"Scores: {trace['scores']}")
    if trace["citations"]:
        typer.echo("Selected citations:")
        for citation in trace["citations"]:
            typer.echo(f"- {citation['label']}")
    typer.echo(f"Details: {json.dumps(trace['details'], sort_keys=True)}")


def _print_warning_list(warnings: list[dict]) -> None:
    if not warnings:
        return
    typer.echo("Warnings:")
    for warning in warnings:
        if {"source_id", "path", "code", "message"}.issubset(warning):
            line = f":{warning['line']}" if "line" in warning else ""
            typer.echo(
                f"- {warning['source_id']}:{warning['path']}{line} "
                f"[{warning['code']}] {warning['message']}"
            )
        else:
            typer.echo(f"- [{warning.get('code', 'warning')}] {warning.get('message', warning)}")


def main() -> None:
    app()
