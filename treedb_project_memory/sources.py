from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .config import DEFAULT_JSONL_CONTENT_FIELD, SourceConfig

BINARY_SAMPLE_BYTES = 8192
CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class SourceSkip:
    source_id: str
    path: str
    code: str
    message: str
    warning: bool = False
    absolute_path: str | None = None
    line: int | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "warning": self.warning,
        }
        if self.absolute_path is not None:
            payload["absolute_path"] = self.absolute_path
        if self.line is not None:
            payload["line"] = self.line
        return payload


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    source_type: str
    source_root: str
    path: str
    absolute_path: str
    content: str
    document_hash: str
    document_kind: str
    start_line: int | None = 1
    end_line: int | None = None
    mtime: float | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceScanResult:
    source: SourceConfig
    files_scanned: int = 0
    documents: list[SourceDocument] = field(default_factory=list)
    skipped: list[SourceSkip] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return sum(1 for skip in self.skipped if skip.warning)

    def warnings(self) -> list[SourceSkip]:
        return [skip for skip in self.skipped if skip.warning]


def scan_source(source: SourceConfig) -> SourceScanResult:
    """Enumerate source documents for a configured source without indexing."""
    result = SourceScanResult(source=source)
    root = Path(source.root).expanduser()
    if not root.exists():
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=".",
                code="missing_root",
                message=f"source root does not exist: {source.root}",
                warning=True,
                absolute_path=str(root),
            )
        )
        return result
    if root.is_symlink() and not source.follow_symlinks:
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=".",
                code="symlink",
                message=f"symlink source root skipped by policy: {source.root}",
                warning=True,
                absolute_path=str(root),
            )
        )
        return result

    if root.is_file() or (root.is_symlink() and not root.is_dir()):
        _scan_candidate_file(source, root, root.name, result)
        return result

    for dirpath, dirnames, filenames in os.walk(root, followlinks=source.follow_symlinks):
        current = Path(dirpath)
        _filter_directories(source, root, current, dirnames, result)
        for filename in sorted(filenames):
            path = current / filename
            rel_path = _relative_path(path, root)
            _scan_candidate_file(source, path, rel_path, result)
    return result


def _filter_directories(
    source: SourceConfig,
    root: Path,
    current: Path,
    dirnames: list[str],
    result: SourceScanResult,
) -> None:
    dirnames.sort()
    for dirname in sorted(list(dirnames)):
        path = current / dirname
        rel_path = _relative_path(path, root)
        if _is_excluded(source, rel_path):
            dirnames.remove(dirname)
            result.skipped.append(
                SourceSkip(
                    source_id=source.id,
                    path=rel_path,
                    code="excluded",
                    message=f"directory excluded by source rules: {rel_path}",
                    absolute_path=str(path),
                )
            )
            continue
        if path.is_symlink() and not source.follow_symlinks:
            dirnames.remove(dirname)
            result.skipped.append(
                SourceSkip(
                    source_id=source.id,
                    path=rel_path,
                    code="symlink",
                    message=f"symlink directory skipped by policy: {rel_path}",
                    warning=True,
                    absolute_path=str(path),
                )
            )


def _scan_candidate_file(
    source: SourceConfig,
    path: Path,
    rel_path: str,
    result: SourceScanResult,
) -> None:
    if path.is_symlink() and not source.follow_symlinks:
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="symlink",
                message=f"symlink file skipped by policy: {rel_path}",
                warning=True,
                absolute_path=str(path),
            )
        )
        return
    if _is_excluded(source, rel_path):
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="excluded",
                message=f"file excluded by source rules: {rel_path}",
                absolute_path=str(path),
            )
        )
        return
    if not _is_included(source, rel_path):
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="include_miss",
                message=f"file did not match source include rules: {rel_path}",
                absolute_path=str(path),
            )
        )
        return

    try:
        stat = path.stat()
    except OSError as exc:
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="stat_failed",
                message=f"could not stat source file: {exc}",
                warning=True,
                absolute_path=str(path),
            )
        )
        return

    if stat.st_size > source.max_file_bytes:
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="large_file",
                message=(
                    f"file exceeds max_file_bytes={source.max_file_bytes}: "
                    f"{stat.st_size} bytes"
                ),
                warning=True,
                absolute_path=str(path),
            )
        )
        return

    try:
        raw = path.read_bytes()
    except OSError as exc:
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="read_failed",
                message=f"could not read source file: {exc}",
                warning=True,
                absolute_path=str(path),
            )
        )
        return

    if _looks_binary(raw):
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="binary_file",
                message=f"binary file skipped: {rel_path}",
                warning=True,
                absolute_path=str(path),
            )
        )
        return

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        result.skipped.append(
            SourceSkip(
                source_id=source.id,
                path=rel_path,
                code="binary_file",
                message=f"non-UTF-8 file skipped: {exc}",
                warning=True,
                absolute_path=str(path),
            )
        )
        return

    result.files_scanned += 1
    if _document_kind(source, rel_path) == "jsonl":
        _load_jsonl_documents(source, path, rel_path, content, stat, result)
        return

    result.documents.append(
        SourceDocument(
            source_id=source.id,
            source_type=source.type,
            source_root=source.root,
            path=rel_path,
            absolute_path=str(path),
            content=content,
            document_hash=_sha256(content),
            document_kind=_document_kind(source, rel_path),
            start_line=1 if content else None,
            end_line=_line_count(content) if content else None,
            mtime=stat.st_mtime,
            size_bytes=stat.st_size,
        )
    )


def _load_jsonl_documents(
    source: SourceConfig,
    path: Path,
    rel_path: str,
    content: str,
    stat: os.stat_result,
    result: SourceScanResult,
) -> None:
    content_field = source.content_field or DEFAULT_JSONL_CONTENT_FIELD
    for line_no, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            result.skipped.append(
                SourceSkip(
                    source_id=source.id,
                    path=rel_path,
                    code="malformed_jsonl",
                    message=f"malformed JSONL record skipped at line {line_no}: {exc.msg}",
                    warning=True,
                    absolute_path=str(path),
                    line=line_no,
                )
            )
            continue
        if not isinstance(record, dict):
            result.skipped.append(
                SourceSkip(
                    source_id=source.id,
                    path=rel_path,
                    code="jsonl_record_not_object",
                    message=f"JSONL record at line {line_no} is not an object",
                    warning=True,
                    absolute_path=str(path),
                    line=line_no,
                )
            )
            continue
        record_content = record.get(content_field)
        if not isinstance(record_content, str) or not record_content.strip():
            result.skipped.append(
                SourceSkip(
                    source_id=source.id,
                    path=rel_path,
                    code="missing_content_field",
                    message=(
                        f"JSONL record at line {line_no} has no non-empty "
                        f"string field '{content_field}'"
                    ),
                    warning=True,
                    absolute_path=str(path),
                    line=line_no,
                )
            )
            continue
        result.documents.append(
            SourceDocument(
                source_id=source.id,
                source_type=source.type,
                source_root=source.root,
                path=rel_path,
                absolute_path=str(path),
                content=record_content,
                document_hash=_sha256(record_content),
                document_kind="jsonl",
                start_line=line_no,
                end_line=line_no,
                mtime=stat.st_mtime,
                size_bytes=stat.st_size,
                metadata={
                    "jsonl_content_field": content_field,
                    "jsonl_line": line_no,
                    "title": record.get("title")
                    if isinstance(record.get("title"), str)
                    else None,
                },
            )
        )


def _is_included(source: SourceConfig, rel_path: str) -> bool:
    if not source.include:
        return True
    return any(_matches(rel_path, pattern) for pattern in source.include)


def _is_excluded(source: SourceConfig, rel_path: str) -> bool:
    excludes = list(source.exclude)
    if source.type == "repo" and ".git/**" not in excludes:
        excludes.append(".git/**")
    return any(_matches(rel_path, pattern) for pattern in excludes)


def _matches(rel_path: str, pattern: str) -> bool:
    path = rel_path.replace(os.sep, "/").strip("/")
    glob = pattern.replace(os.sep, "/").strip("/")
    if not glob:
        return False
    if glob.endswith("/**"):
        base = glob[:-3].rstrip("/")
        if path == base or path.startswith(f"{base}/"):
            return True
    if fnmatch.fnmatchcase(path, glob):
        return True
    if glob.startswith("**/") and fnmatch.fnmatchcase(path, glob[3:]):
        return True
    if "/" not in glob and fnmatch.fnmatchcase(PurePosixPath(path).name, glob):
        return True
    try:
        return PurePosixPath(path).match(glob)
    except ValueError:
        return False


def _looks_binary(raw: bytes) -> bool:
    sample = raw[:BINARY_SAMPLE_BYTES]
    return b"\x00" in sample


def _document_kind(source: SourceConfig, rel_path: str) -> str:
    if source.type == "jsonl" or Path(rel_path).suffix == ".jsonl":
        return "jsonl"
    if source.type == "markdown" or Path(rel_path).suffix in {".md", ".markdown"}:
        return "markdown"
    if source.type == "text" or Path(rel_path).suffix in {".txt", ".text"}:
        return "text"
    if Path(rel_path).suffix in CODE_EXTENSIONS:
        return "code"
    return "text"


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _line_count(content: str) -> int:
    if not content:
        return 0
    return len(content.splitlines()) or 1


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
