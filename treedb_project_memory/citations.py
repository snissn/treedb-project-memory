from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Citation:
    source_id: str | None
    path: str | None
    start_line: int | None = None
    end_line: int | None = None
    title: str | None = None
    chunk_id: str | None = None

    def label(self) -> str:
        location = self.path or self.source_id or self.chunk_id or "unknown source"
        if self.start_line is not None and self.end_line is not None:
            return f"{location}:{self.start_line}-{self.end_line}"
        if self.start_line is not None:
            return f"{location}:{self.start_line}"
        return location

    def to_json(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "title": self.title,
            "chunk_id": self.chunk_id,
            "label": self.label(),
        }


def citation_from_metadata(metadata: dict[str, Any], document_id: str) -> Citation | None:
    source_id = _optional_str(metadata.get("source_id"))
    path = _optional_str(metadata.get("path"))
    title = _optional_str(metadata.get("title") or metadata.get("symbol"))
    chunk_id = _optional_str(metadata.get("chunk_id")) or document_id
    if source_id is None and path is None and title is None and chunk_id is None:
        return None
    return Citation(
        source_id=source_id,
        path=path,
        start_line=_optional_int(metadata.get("start_line")),
        end_line=_optional_int(metadata.get("end_line")),
        title=title,
        chunk_id=chunk_id,
    )


def render_citations(citations: list[Citation]) -> str:
    if not citations:
        return ""
    return "; ".join(citation.label() for citation in citations)


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
