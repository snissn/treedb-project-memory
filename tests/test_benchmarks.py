import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from treedb_project_memory.benchmarks import (
    FixtureShape,
    generate_fixture_dataset,
    validate_fixture_dataset,
)
from treedb_project_memory.cli import app


runner = CliRunner()


def test_generated_fixture_manifest_validates_dataset(tmp_path) -> None:
    manifest = generate_fixture_dataset(
        tmp_path,
        FixtureShape(file_count=3, paragraphs_per_file=2, jsonl_rows=2, words_per_paragraph=8),
    )

    validated = validate_fixture_dataset(tmp_path)

    assert manifest["schema"] == "treedb-project-memory-fixture-v1"
    assert validated["file_count"] == 4
    assert Path(tmp_path / "docs" / "topic-0000.md").is_file()
    assert Path(tmp_path / "records" / "notes.jsonl").is_file()


def test_fixture_generation_refuses_to_delete_unowned_existing_dirs(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "important.md").write_text("do not delete\n", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to replace"):
        generate_fixture_dataset(tmp_path, FixtureShape(file_count=1))

    assert (docs / "important.md").read_text(encoding="utf-8") == "do not delete\n"


def test_benchmark_fixture_cli_outputs_json_and_manifest(tmp_path) -> None:
    output = tmp_path / "fixture"

    result = runner.invoke(
        app,
        [
            "benchmark",
            "fixture",
            str(output),
            "--files",
            "2",
            "--paragraphs",
            "1",
            "--jsonl-rows",
            "1",
            "--words",
            "8",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["file_count"] == 3
    assert (output / "fixture-manifest.json").is_file()


def test_benchmark_ingest_cli_smoke_writes_reports(tmp_path) -> None:
    output = tmp_path / "ingest"

    result = runner.invoke(
        app,
        [
            "benchmark",
            "ingest",
            "--output-dir",
            str(output),
            "--files",
            "2",
            "--paragraphs",
            "1",
            "--jsonl-rows",
            "1",
            "--words",
            "8",
            "--runs",
            "1",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["benchmark"] == "ingest"
    assert payload["runs"][0]["index"]["chunks"] > 0
    assert payload["runs"][0]["progress_events"][-1]["stage"] == "done"
    assert (output / "ingest_results.json").is_file()
    assert (output / "ingest_results.md").is_file()


def test_benchmark_retrieval_cli_smoke_reports_latency(tmp_path) -> None:
    output = tmp_path / "retrieval"

    result = runner.invoke(
        app,
        [
            "benchmark",
            "retrieval",
            "--output-dir",
            str(output),
            "--files",
            "2",
            "--paragraphs",
            "1",
            "--jsonl-rows",
            "1",
            "--words",
            "8",
            "--repetitions",
            "2",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["benchmark"] == "retrieval"
    assert payload["measurements"][0]["result_count_min"] > 0
    assert payload["measurements"][0]["p95_seconds"] >= 0
    assert (output / "retrieval_results.json").is_file()


def test_benchmark_ui_smoke_cli_starts_server_and_writes_reports(tmp_path) -> None:
    output = tmp_path / "ui"

    result = runner.invoke(
        app,
        ["benchmark", "ui-smoke", "--output-dir", str(output), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["benchmark"] == "ui-smoke"
    assert payload["requests"]["api/health"]["status"] == 200
    assert payload["requests"]["root"]["status"] == 200
    assert (output / "ui-smoke_results.json").is_file()
