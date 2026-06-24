import json
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import (
    VALID_SOURCE_TYPES,
    WorkspaceError,
    add_source,
    discover_workspace,
    doctor_report,
    init_workspace,
    read_config,
)

app = typer.Typer(
    add_completion=False,
    help=(
        "Local project-memory workspace tooling. Indexing, retrieval, and "
        "TreeDB/Haystack integration are planned but not implemented yet."
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
) -> None:
    """Add a source entry to the workspace config without indexing it."""
    try:
        workspace = discover_workspace()
        source = add_source(workspace, root, source_type, source_id, include, exclude)
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


@app.command()
def doctor(
    output_format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Validate workspace config and source root existence."""
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
    else:
        for error in report["errors"]:
            typer.echo(f"ERROR: {error['message']}", err=True)
    if exit_code:
        raise typer.Exit(exit_code)


def main() -> None:
    app()
