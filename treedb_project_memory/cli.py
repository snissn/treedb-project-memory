from typing import Optional

import typer

from . import __version__

app = typer.Typer(
    add_completion=False,
    help=(
        "Local project-memory scaffold. Workspace, indexing, retrieval, and "
        "TreeDB/Haystack commands are planned but not implemented yet."
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


def main() -> None:
    app()
