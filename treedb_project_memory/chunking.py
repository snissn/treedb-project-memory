from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sources import CODE_EXTENSIONS, SourceDocument

DEFAULT_MARKDOWN_CHUNK_CHARS = 1600
DEFAULT_TEXT_CHUNK_CHARS = 1200
DEFAULT_CODE_CHUNK_CHARS = 2400
DEFAULT_CODE_CHUNK_LINES = 80
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

LANGUAGE_BY_EXTENSION = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".lua": "lua",
    ".php": "php",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    content: str
    metadata: dict[str, Any]

    def to_json(self, include_content: bool = False) -> dict[str, Any]:
        payload = {"chunk_id": self.chunk_id, "metadata": dict(self.metadata)}
        if include_content:
            payload["content"] = self.content
        return payload


@dataclass(frozen=True)
class LineSpan:
    content: str
    start_line: int
    end_line: int
    title: str | None = None


def chunk_document(
    document: SourceDocument,
    workspace_id: str,
    *,
    markdown_chunk_chars: int = DEFAULT_MARKDOWN_CHUNK_CHARS,
    text_chunk_chars: int = DEFAULT_TEXT_CHUNK_CHARS,
    code_chunk_chars: int = DEFAULT_CODE_CHUNK_CHARS,
    code_chunk_lines: int = DEFAULT_CODE_CHUNK_LINES,
) -> list[Chunk]:
    if not document.content:
        return []

    if document.document_kind == "markdown":
        spans = _markdown_spans(document.content, markdown_chunk_chars)
        return _build_chunks(document, workspace_id, "markdown_section", spans)
    if document.document_kind == "code":
        spans = _code_spans(document.content, code_chunk_chars, code_chunk_lines)
        return _build_chunks(document, workspace_id, "code", spans)
    if document.document_kind == "jsonl":
        spans = _text_spans(document.content, text_chunk_chars, document.start_line or 1)
        return _build_chunks(document, workspace_id, "json_record", spans)

    spans = _text_spans(document.content, text_chunk_chars, document.start_line or 1)
    return _build_chunks(document, workspace_id, "text_block", spans)


def _build_chunks(
    document: SourceDocument,
    workspace_id: str,
    chunk_kind: str,
    spans: list[LineSpan],
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for index, span in enumerate(spans):
        content_hash = _sha256(span.content)
        chunk_id = _chunk_id(document, index, content_hash)
        metadata = _metadata(
            document=document,
            workspace_id=workspace_id,
            chunk_id=chunk_id,
            content_hash=content_hash,
            chunk_kind=chunk_kind,
            start_line=span.start_line,
            end_line=span.end_line,
            chunk_index=index,
        )
        if span.title:
            metadata["title"] = span.title
            metadata["symbol"] = span.title
        chunks.append(Chunk(chunk_id=chunk_id, content=span.content, metadata=metadata))
    return chunks


def _metadata(
    *,
    document: SourceDocument,
    workspace_id: str,
    chunk_id: str,
    content_hash: str,
    chunk_kind: str,
    start_line: int,
    end_line: int,
    chunk_index: int,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "workspace_id": workspace_id,
        "source_id": document.source_id,
        "source_type": document.source_type,
        "source_root": document.source_root,
        "path": document.path,
        "absolute_path": document.absolute_path,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
        "document_hash": document.document_hash,
        "chunk_kind": chunk_kind,
        "start_line": start_line,
        "end_line": end_line,
    }
    if document.mtime is not None:
        metadata["mtime"] = document.mtime
    if document.size_bytes is not None:
        metadata["size_bytes"] = document.size_bytes

    language = _language_for_path(document.path)
    if language:
        metadata["language"] = language
    for key, value in document.metadata.items():
        if value is not None:
            metadata[key] = value
    return metadata


def _markdown_spans(content: str, max_chars: int) -> list[LineSpan]:
    lines = content.splitlines()
    if not lines:
        return []

    sections: list[tuple[list[tuple[int, str]], str | None]] = []
    current: list[tuple[int, str]] = []
    current_title: str | None = None
    for line_no, line in enumerate(lines, start=1):
        heading = HEADING_RE.match(line)
        if heading and current:
            sections.append((current, current_title))
            current = []
        if heading:
            current_title = heading.group(2)
        current.append((line_no, line))
    if current:
        sections.append((current, current_title))

    spans: list[LineSpan] = []
    for section_lines, title in sections:
        for span in _line_limited_spans(section_lines, max_chars):
            spans.append(
                LineSpan(
                    content=span.content,
                    start_line=span.start_line,
                    end_line=span.end_line,
                    title=title,
                )
            )
    return spans


def _text_spans(content: str, max_chars: int, start_line: int = 1) -> list[LineSpan]:
    lines = list(enumerate(content.splitlines(), start=start_line))
    if not lines:
        return []

    paragraphs: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for line_no, line in lines:
        if line.strip():
            current.append((line_no, line))
            continue
        if current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)

    if not paragraphs:
        return []

    spans: list[LineSpan] = []
    current_lines: list[tuple[int, str]] = []
    for paragraph in paragraphs:
        paragraph_text = _join_lines(paragraph)
        if len(paragraph_text) > max_chars:
            if current_lines:
                spans.extend(_line_limited_spans(current_lines, max_chars))
                current_lines = []
            spans.extend(_line_limited_spans(paragraph, max_chars))
            continue

        candidate = current_lines + ([(-1, "")] if current_lines else []) + paragraph
        candidate_text = _join_lines(candidate)
        if current_lines and len(candidate_text) > max_chars:
            spans.extend(_line_limited_spans(current_lines, max_chars))
            current_lines = list(paragraph)
        else:
            if current_lines:
                current_lines.append((-1, ""))
            current_lines.extend(paragraph)
    if current_lines:
        spans.extend(_line_limited_spans(current_lines, max_chars))
    return spans


def _code_spans(content: str, max_chars: int, max_lines: int) -> list[LineSpan]:
    lines = list(enumerate(content.splitlines(), start=1))
    if not lines:
        return []

    spans: list[LineSpan] = []
    current: list[tuple[int, str]] = []
    for line in lines:
        candidate = current + [line]
        if current and (
            len(candidate) > max_lines or len(_join_lines(candidate)) > max_chars
        ):
            spans.extend(_line_limited_spans(current, max_chars))
            current = [line]
        else:
            current = candidate
    if current:
        spans.extend(_line_limited_spans(current, max_chars))
    return spans


def _line_limited_spans(
    lines: list[tuple[int, str]],
    max_chars: int,
) -> list[LineSpan]:
    spans: list[LineSpan] = []
    current: list[tuple[int, str]] = []
    for line_no, line in lines:
        if line_no == -1:
            if current and len(_join_lines(current + [(line_no, line)])) <= max_chars:
                current.append((line_no, line))
            continue
        if len(line) > max_chars:
            if current:
                spans.append(_span_from_lines(current))
                current = []
            for start in range(0, len(line), max_chars):
                spans.append(LineSpan(line[start : start + max_chars], line_no, line_no))
            continue

        candidate = current + [(line_no, line)]
        if current and len(_join_lines(candidate)) > max_chars:
            spans.append(_span_from_lines(current))
            current = [(line_no, line)]
        else:
            current = candidate
    if current:
        spans.append(_span_from_lines(current))
    return [span for span in spans if span.content]


def _span_from_lines(lines: list[tuple[int, str]]) -> LineSpan:
    real_lines = [(line_no, line) for line_no, line in lines if line_no != -1]
    return LineSpan(
        content=_join_lines(lines).strip("\n"),
        start_line=real_lines[0][0],
        end_line=real_lines[-1][0],
    )


def _join_lines(lines: list[tuple[int, str]]) -> str:
    return "\n".join(line for _line_no, line in lines)


def _language_for_path(path: str) -> str | None:
    suffix = Path(path).suffix
    if suffix not in CODE_EXTENSIONS:
        return None
    return LANGUAGE_BY_EXTENSION.get(suffix)


def _chunk_id(document: SourceDocument, chunk_index: int, content_hash: str) -> str:
    seed = "\n".join(
        [
            document.source_id,
            document.path,
            document.document_kind,
            str(document.start_line),
            str(document.end_line),
            str(chunk_index),
            content_hash,
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
