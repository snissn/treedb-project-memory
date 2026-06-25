from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .citations import render_citations
from .config import AnswerGeneratorConfig, ProjectConfig, Workspace
from .retrieval import RetrievalTrace, SearchResult, search_workspace


class AnswerError(Exception):
    """Raised when answer generation cannot complete."""


class AnswerGenerator(Protocol):
    def generate(self, query: str, results: list[SearchResult]) -> str:
        """Generate an answer grounded in retrieved results."""


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[dict[str, Any]]
    results: list[SearchResult]
    trace: RetrievalTrace

    def to_json(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": list(self.citations),
            "results": [result.to_json() for result in self.results],
            "trace": self.trace.to_json(),
        }


@dataclass
class ExtractiveAnswerGenerator:
    config: AnswerGeneratorConfig

    def generate(self, query: str, results: list[SearchResult]) -> str:
        del query
        selected = results[: self.config.max_context_chunks]
        if not selected:
            return "No indexed context matched the query."
        snippets = []
        for result in selected:
            first_line = result.content.strip().splitlines()[0] if result.content.strip() else ""
            citation = render_citations([result.citation]) if result.citation else result.id
            snippets.append(f"{first_line} [{citation}]")
        return " ".join(snippets)


GeneratorFactory = Any


def ask_workspace(
    workspace: Workspace,
    config: ProjectConfig,
    *,
    query: str,
    mode: str | None = None,
    top_k: int | None = None,
    source_id: str | None = None,
    generator_factory: GeneratorFactory = None,
    **search_kwargs: Any,
) -> AnswerResult:
    generator = _create_generator(config, generator_factory)
    results, trace = search_workspace(
        workspace,
        config,
        query=query,
        mode=mode,
        top_k=top_k,
        source_id=source_id,
        **search_kwargs,
    )
    answer = generator.generate(query, results)
    citations = [
        result.citation.to_json()
        for result in results
        if result.citation is not None
    ]
    return AnswerResult(answer=answer, citations=citations, results=results, trace=trace)


def _create_generator(
    config: ProjectConfig,
    generator_factory: GeneratorFactory,
) -> AnswerGenerator:
    if generator_factory is not None:
        return generator_factory(config.answer_generator)
    if config.answer_generator.provider is None:
        raise AnswerError(
            "ask requires answer_generator.provider to be configured; "
            "search works without an answer generator"
        )
    if config.answer_generator.provider == "extractive":
        return ExtractiveAnswerGenerator(config.answer_generator)
    raise AnswerError(
        f"unsupported answer generator provider: {config.answer_generator.provider}"
    )
