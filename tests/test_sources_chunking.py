import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from treedb_project_memory.chunking import chunk_document
from treedb_project_memory.cli import app
from treedb_project_memory.config import SourceConfig
from treedb_project_memory.sources import SourceDocument, scan_source

runner = CliRunner()


def source_config(
    tmp_path: Path,
    *,
    source_type: str = "folder",
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_file_bytes: int = 1_048_576,
    follow_symlinks: bool = False,
    content_field: str | None = None,
) -> SourceConfig:
    return SourceConfig(
        id="src",
        type=source_type,
        root=str(tmp_path),
        include=include or ["**/*"],
        exclude=exclude or [],
        max_file_bytes=max_file_bytes,
        follow_symlinks=follow_symlinks,
        content_field=content_field,
    )


def document(content: str, *, kind: str, path: str = "doc.txt") -> SourceDocument:
    return SourceDocument(
        source_id="src",
        source_type="folder",
        source_root="/tmp/src",
        path=path,
        absolute_path=f"/tmp/src/{path}",
        content=content,
        document_hash="doc-hash",
        document_kind=kind,
        start_line=1,
        end_line=len(content.splitlines()),
    )


def test_include_exclude_matching_and_folder_globs(tmp_path) -> None:
    (tmp_path / "keep.md").write_text("# Keep\n", encoding="utf-8")
    (tmp_path / "skip.md").write_text("# Skip\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "deep.md").write_text("# Deep\n", encoding="utf-8")

    result = scan_source(
        source_config(
            tmp_path,
            include=["**/*.md"],
            exclude=["skip.md"],
        )
    )

    assert [doc.path for doc in result.documents] == ["keep.md", "nested/deep.md"]
    assert {skip.code for skip in result.skipped} == {"excluded", "include_miss"}


def test_repo_sources_always_exclude_git_directory(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("private\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    result = scan_source(source_config(tmp_path, source_type="repo", include=["**/*"]))

    assert [doc.path for doc in result.documents] == ["app.py"]
    assert any(skip.path == ".git" and skip.code == "excluded" for skip in result.skipped)


def test_symlink_policy_is_explicit_and_configurable(tmp_path) -> None:
    (tmp_path / "real.txt").write_text("real\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    (real_dir / "inside.txt").write_text("inside\n", encoding="utf-8")
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real_dir, target_is_directory=True)

    default_result = scan_source(
        source_config(tmp_path, include=["**/*.txt"], follow_symlinks=False)
    )
    followed_result = scan_source(
        source_config(tmp_path, include=["**/*.txt"], follow_symlinks=True)
    )
    root_link_result = scan_source(
        SourceConfig(
            id="root-link",
            type="folder",
            root=str(root_link),
            include=["**/*.txt"],
            exclude=[],
            follow_symlinks=False,
        )
    )

    assert [doc.path for doc in default_result.documents] == [
        "real.txt",
        "real-dir/inside.txt",
    ]
    assert any(skip.path == "link.txt" and skip.code == "symlink" for skip in default_result.skipped)
    assert [doc.path for doc in followed_result.documents] == [
        "link.txt",
        "real.txt",
        "real-dir/inside.txt",
        "root-link/inside.txt",
    ]
    assert root_link_result.documents == []
    assert root_link_result.skipped[0].code == "symlink"


def test_binary_and_large_files_are_skipped_with_warnings(tmp_path) -> None:
    (tmp_path / "binary.bin").write_bytes(b"abc\x00def")
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")

    result = scan_source(
        source_config(tmp_path, include=["**/*"], max_file_bytes=10)
    )

    warning_codes = {skip.code for skip in result.warnings()}
    assert warning_codes == {"binary_file", "large_file"}
    assert result.documents == []


def test_markdown_headings_produce_section_chunks_with_line_ranges() -> None:
    chunks = chunk_document(
        document("# One\nbody\n## Two\nmore\n", kind="markdown", path="guide.md"),
        "workspace",
    )

    assert [chunk.metadata["title"] for chunk in chunks] == ["One", "Two"]
    assert [(chunk.metadata["start_line"], chunk.metadata["end_line"]) for chunk in chunks] == [
        (1, 2),
        (3, 4),
    ]
    assert all(chunk.metadata["chunk_kind"] == "markdown_section" for chunk in chunks)


def test_text_chunk_size_limits_are_enforced() -> None:
    chunks = chunk_document(
        document("abcdefghij\nklmnopqrst\nuvwxyz\n", kind="text"),
        "workspace",
        text_chunk_chars=10,
    )

    assert [len(chunk.content) for chunk in chunks] == [10, 10, 6]
    assert all(len(chunk.content) <= 10 for chunk in chunks)


def test_code_chunks_include_line_ranges() -> None:
    chunks = chunk_document(
        document("a = 1\nb = 2\nc = 3\n", kind="code", path="app.py"),
        "workspace",
        code_chunk_lines=2,
    )

    assert [(chunk.metadata["start_line"], chunk.metadata["end_line"]) for chunk in chunks] == [
        (1, 2),
        (3, 3),
    ]
    assert chunks[0].metadata["language"] == "python"


def test_jsonl_loader_uses_configured_content_field_and_skips_malformed_records(tmp_path) -> None:
    records = "\n".join(
        [
            json.dumps({"body": "first record", "title": "First"}),
            "{bad json",
            json.dumps({"content": "wrong field"}),
            json.dumps(["not", "object"]),
        ]
    )
    (tmp_path / "records.jsonl").write_text(records, encoding="utf-8")

    result = scan_source(
        source_config(
            tmp_path,
            source_type="jsonl",
            include=["**/*.jsonl"],
            content_field="body",
        )
    )
    chunks = chunk_document(result.documents[0], "workspace")

    assert len(result.documents) == 1
    assert result.documents[0].content == "first record"
    assert result.documents[0].start_line == 1
    assert chunks[0].metadata["chunk_kind"] == "json_record"
    assert chunks[0].metadata["jsonl_content_field"] == "body"
    assert chunks[0].metadata["title"] == "First"
    assert {skip.code for skip in result.warnings()} == {
        "malformed_jsonl",
        "missing_content_field",
        "jsonl_record_not_object",
    }


def test_markdown_and_text_source_types_enumerate_files(tmp_path) -> None:
    markdown_file = tmp_path / "note.md"
    text_file = tmp_path / "note.txt"
    markdown_file.write_text("# Note\n", encoding="utf-8")
    text_file.write_text("note\n", encoding="utf-8")

    markdown_result = scan_source(
        SourceConfig(
            id="md",
            type="markdown",
            root=str(markdown_file),
            include=["*"],
            exclude=[],
        )
    )
    text_result = scan_source(
        SourceConfig(
            id="txt",
            type="text",
            root=str(text_file),
            include=["*"],
            exclude=[],
        )
    )

    assert markdown_result.documents[0].document_kind == "markdown"
    assert text_result.documents[0].document_kind == "text"


def test_chunk_ids_are_stable_for_unchanged_content() -> None:
    doc = document("same content\n", kind="text")

    first = chunk_document(doc, "workspace")
    second = chunk_document(doc, "workspace")

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]


def test_index_dry_run_reports_counts_warnings_and_does_not_write_state(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\nbody\n", encoding="utf-8")
    (docs / "code.py").write_text("print('ok')\n", encoding="utf-8")
    (docs / "binary.bin").write_bytes(b"\x00binary")
    assert runner.invoke(app, ["init", "--workspace", "demo"]).exit_code == 0
    add_result = runner.invoke(
        app,
        [
            "add",
            "docs",
            "--id",
            "docs",
            "--type",
            "folder",
            "--include",
            "**/*",
        ],
    )
    assert add_result.exit_code == 0

    result = runner.invoke(app, ["index", "--dry-run", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["file_count"] == 2
    assert payload["chunk_count"] == 2
    assert payload["skip_count"] == 1
    assert payload["warnings"][0]["code"] == "binary_file"
    assert list((tmp_path / ".treedb-project-memory" / "state").iterdir()) == []


def test_index_dry_run_supports_source_filter_and_text_output(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    notes = tmp_path / "notes"
    docs.mkdir()
    notes.mkdir()
    (docs / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (notes / "note.txt").write_text("note\n", encoding="utf-8")
    assert runner.invoke(app, ["init", "--workspace", "demo"]).exit_code == 0
    config_path = tmp_path / ".treedb-project-memory" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["sources"] = {
        "docs": {
            "type": "folder",
            "root": str(docs),
            "include": ["**/*.md"],
            "exclude": [],
        },
        "notes": {
            "type": "folder",
            "root": str(notes),
            "include": ["**/*.txt"],
            "exclude": [],
        },
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = runner.invoke(app, ["index", "--dry-run", "--source", "notes"])

    assert result.exit_code == 0
    assert "Dry-run index summary" in result.output
    assert "Files scanned: 1" in result.output
    assert "notes\tfolder\tfiles=1" in result.output
    assert "docs\tfolder" not in result.output
