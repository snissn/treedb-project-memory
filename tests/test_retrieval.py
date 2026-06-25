import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from treedb_project_memory.answers import AnswerError, ask_workspace
from treedb_project_memory.citations import Citation, citation_from_metadata
from treedb_project_memory.cli import app
from treedb_project_memory.config import (
    AnswerGeneratorConfig,
    ProjectConfig,
    SourceConfig,
    TreeDBConfig,
    Workspace,
    init_workspace,
    read_config,
    write_config,
)
from treedb_project_memory.embedding import DeterministicEmbeddingProvider
from treedb_project_memory.retrieval import RetrievalError, search_workspace
from treedb_project_memory.treedb_adapter import (
    IndexDocument,
    InMemoryTreeDBAdapter,
    RetrievalRequest,
    TreeDBAdapterError,
)


runner = CliRunner()


def make_config(tmp_path: Path) -> tuple[Workspace, ProjectConfig]:
    workspace = init_workspace(tmp_path, "demo", force=False)
    docs = tmp_path / "docs"
    docs.mkdir()
    config = read_config(workspace)
    config.sources["docs"] = SourceConfig(
        id="docs",
        type="folder",
        root=str(docs),
        include=["**/*.md"],
        exclude=[],
    )
    config.treedb = TreeDBConfig(adapter="memory")
    config.retrieval.default_mode = "keyword"
    write_config(workspace, config)
    return workspace, read_config(workspace)


def fixture_adapter(config: ProjectConfig) -> InMemoryTreeDBAdapter:
    provider = DeterministicEmbeddingProvider(config.embedding)
    adapter = InMemoryTreeDBAdapter(
        config.treedb,
        embedding_dimension=config.embedding.dimension,
    )
    texts = [
        "TreeDB stores project memory chunks with citations.",
        "The quickstart explains workspace setup and indexing.",
        "Unrelated notes about packaging.",
    ]
    vectors = provider.embed(texts)
    adapter.upsert_documents(
        [
            IndexDocument(
                id="chunk-a",
                content=texts[0],
                embedding=vectors[0],
                meta={
                    "source_id": "docs",
                    "path": "guide.md",
                    "start_line": 3,
                    "end_line": 5,
                    "chunk_id": "chunk-a",
                    "title": "Guide",
                },
            ),
            IndexDocument(
                id="chunk-b",
                content=texts[1],
                embedding=vectors[1],
                meta={
                    "source_id": "docs",
                    "path": "quickstart.md",
                    "chunk_id": "chunk-b",
                },
            ),
            IndexDocument(
                id="chunk-c",
                content=texts[2],
                embedding=vectors[2],
                meta={
                    "source_id": "notes",
                    "path": "notes.md",
                    "start_line": 1,
                    "end_line": 1,
                    "chunk_id": "chunk-c",
                },
            ),
        ]
    )
    return adapter


def adapter_factory(adapter: InMemoryTreeDBAdapter):
    def factory(_treedb: TreeDBConfig, *, embedding_dimension: int):
        assert embedding_dimension == adapter.embedding_dimension
        return adapter

    return factory


def test_search_returns_stable_fixture_citations(tmp_path) -> None:
    workspace, config = make_config(tmp_path)
    adapter = fixture_adapter(config)

    results, _trace = search_workspace(
        workspace,
        config,
        query="TreeDB citations",
        mode="keyword",
        adapter_factory=adapter_factory(adapter),
    )

    assert [result.id for result in results[:2]] == ["chunk-a", "chunk-b"]
    assert results[0].citation is not None
    assert results[0].citation.label() == "guide.md:3-5"
    assert results[1].citation is not None
    assert results[1].citation.label() == "quickstart.md"


def test_search_works_without_answer_generator(tmp_path) -> None:
    workspace, config = make_config(tmp_path)
    adapter = fixture_adapter(config)
    assert config.answer_generator.provider is None

    results, _trace = search_workspace(
        workspace,
        config,
        query="workspace setup",
        mode="keyword",
        adapter_factory=adapter_factory(adapter),
    )

    assert results[0].id == "chunk-b"


def test_ask_fails_clearly_when_no_generator_is_configured(tmp_path) -> None:
    workspace, config = make_config(tmp_path)
    adapter = fixture_adapter(config)

    with pytest.raises(AnswerError, match="answer_generator.provider"):
        ask_workspace(
            workspace,
            config,
            query="What stores project memory?",
            adapter_factory=adapter_factory(adapter),
        )


class FakeGenerator:
    def generate(self, query: str, results: list[Any]) -> str:
        return f"{query}: {results[0].id}"


def test_ask_returns_cited_answer_with_fake_generator(tmp_path) -> None:
    workspace, config = make_config(tmp_path)
    config.answer_generator = AnswerGeneratorConfig(provider="extractive")
    adapter = fixture_adapter(config)

    answer = ask_workspace(
        workspace,
        config,
        query="What stores project memory?",
        mode="keyword",
        adapter_factory=adapter_factory(adapter),
        generator_factory=lambda _config: FakeGenerator(),
    )

    assert answer.answer == "What stores project memory?: chunk-a"
    assert answer.citations[0]["label"] == "guide.md:3-5"


def test_retrieval_trace_includes_query_mode_filters_ids_scores_and_citations(tmp_path) -> None:
    workspace, config = make_config(tmp_path)
    adapter = fixture_adapter(config)

    _results, trace = search_workspace(
        workspace,
        config,
        query="TreeDB citations",
        mode="keyword",
        source_id="docs",
        top_k=2,
        adapter_factory=adapter_factory(adapter),
    )
    payload = trace.to_json()

    assert payload["query"] == "TreeDB citations"
    assert payload["mode"] == "keyword"
    assert payload["filters"] == {"source_id": "docs"}
    assert payload["top_k"] == 2
    assert payload["document_ids"] == ["chunk-a", "chunk-b"]
    assert payload["scores"] == [2.0, 0.0]
    assert payload["citations"][0]["label"] == "guide.md:3-5"
    assert payload["details"]["adapter"] == "memory"


def test_semantic_and_hybrid_modes_use_embeddings(tmp_path) -> None:
    workspace, config = make_config(tmp_path)
    adapter = fixture_adapter(config)

    semantic_results, semantic_trace = search_workspace(
        workspace,
        config,
        query="project memory chunks",
        mode="semantic",
        adapter_factory=adapter_factory(adapter),
    )
    hybrid_results, hybrid_trace = search_workspace(
        workspace,
        config,
        query="project memory chunks",
        mode="hybrid",
        adapter_factory=adapter_factory(adapter),
    )

    assert semantic_results
    assert hybrid_results
    assert semantic_trace.details["embedding_used"] is True
    assert hybrid_trace.details["embedding_used"] is True


class UnsupportedFilterAdapter:
    def search_documents(self, request: RetrievalRequest):
        if request.filters:
            raise TreeDBAdapterError("source filtering is not supported by this retriever")
        return []


def test_unsupported_mode_and_filter_combinations_return_explicit_errors(tmp_path) -> None:
    workspace, config = make_config(tmp_path)

    with pytest.raises(RetrievalError, match="mode must be one of"):
        search_workspace(workspace, config, query="x", mode="unknown")

    with pytest.raises(RetrievalError, match="source filtering is not supported"):
        search_workspace(
            workspace,
            config,
            query="x",
            mode="keyword",
            source_id="docs",
            adapter_factory=lambda _treedb, *, embedding_dimension: UnsupportedFilterAdapter(),
        )


def test_citation_rendering_handles_missing_line_ranges() -> None:
    citation = citation_from_metadata(
        {"source_id": "docs", "path": "README.md", "chunk_id": "chunk"},
        "chunk",
    )

    assert isinstance(citation, Citation)
    assert citation.label() == "README.md"
    assert citation.to_json()["start_line"] is None


def test_json_output_is_parseable_for_search_results_and_traces(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace, config = make_config(tmp_path)
    adapter = fixture_adapter(config)

    def fake_search_workspace(*args, **kwargs):
        return search_workspace(
            workspace,
            config,
            *args[2:],
            adapter_factory=adapter_factory(adapter),
            **kwargs,
        )

    monkeypatch.setattr("treedb_project_memory.cli.search_workspace", fake_search_workspace)

    result = runner.invoke(app, ["search", "TreeDB citations", "--json", "--explain"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["results"][0]["citation"]["label"] == "guide.md:3-5"
    assert payload["trace"]["document_ids"][0] == "chunk-a"
