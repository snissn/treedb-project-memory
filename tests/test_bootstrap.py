from typer.testing import CliRunner

import treedb_project_memory
from treedb_project_memory.cli import app


def test_package_import_smoke() -> None:
    assert treedb_project_memory.__version__


def test_cli_help_smoke() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Local project-memory scaffold" in result.output
    assert "Show the package version and exit." in result.output


def test_cli_version_smoke() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "treedb-project-memory" in result.output
