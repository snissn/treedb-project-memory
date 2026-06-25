import importlib.util
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml

import treedb_project_memory.treedb_adapter as treedb_adapter_module
from treedb_project_memory.config import (
    EmbeddingConfig,
    ProjectConfig,
    SourceConfig,
    TreeDBConfig,
    ValidationError,
    Workspace,
    doctor_report,
    init_workspace,
    read_config,
    write_config,
)
from treedb_project_memory.embedding import DeterministicEmbeddingProvider
from treedb_project_memory.indexing import IndexingError, index_workspace, status_workspace
from treedb_project_memory.treedb_adapter import (
    HaystackTreeDBAdapter,
    IndexDocument,
    InMemoryTreeDBAdapter,
    TreeDBAdapterError,
    create_treedb_adapter,
)


class RecordingAdapter:
    def __init__(self, *, embedding_dimension: int = 32) -> None:
        self.embedding_dimension = embedding_dimension
        self.documents: dict[str, IndexDocument] = {}
        self.upsert_batches: list[list[IndexDocument]] = []
        self.delete_batches: list[list[str]] = []

    def health(self) -> dict[str, Any]:
        return {"ok": True, "adapter": "fake"}

    def count_documents(self) -> int:
        return len(self.documents)

    def upsert_documents(self, documents: list[IndexDocument]) -> int:
        self.upsert_batches.append(list(documents))
        for document in documents:
            self.documents[document.id] = document
        return len(documents)

    def delete_documents(self, document_ids: list[str]) -> int:
        self.delete_batches.append(list(document_ids))
        deleted = 0
        for document_id in document_ids:
            if document_id in self.documents:
                deleted += 1
                del self.documents[document_id]
        return deleted


class BadDimensionProvider:
    provider = "deterministic"
    model = "bad"
    dimension = 7
    batch_size = 32

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _text in texts]


def make_workspace(tmp_path: Path) -> tuple[Workspace, ProjectConfig]:
    workspace = init_workspace(tmp_path, "demo", force=False)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\nbody\n", encoding="utf-8")
    config = read_config(workspace)
    config.sources["docs"] = config.sources.get(
        "docs",
        SourceConfig(
            id="docs",
            type="folder",
            root=str(docs),
            include=["**/*.md"],
            exclude=[],
        ),
    )
    write_config(workspace, config)
    return workspace, read_config(workspace)


def adapter_factory(adapter: RecordingAdapter):
    def factory(_treedb: TreeDBConfig, *, embedding_dimension: int) -> RecordingAdapter:
        assert embedding_dimension == adapter.embedding_dimension
        return adapter

    return factory


def test_default_config_uses_deterministic_embedding_and_haystack_adapter(tmp_path) -> None:
    workspace = init_workspace(tmp_path, "demo", force=False)
    config = yaml.safe_load(workspace.config_path.read_text(encoding="utf-8"))

    assert config["embedding"] == {
        "provider": "deterministic",
        "model": "deterministic-v1",
        "dimension": 32,
        "batch_size": 32,
    }
    assert config["treedb"]["base_url"] == "http://127.0.0.1:7120"
    assert config["treedb"]["index"] == "project_memory"
    assert config["treedb"]["adapter"] == "haystack"


def test_embedding_config_validation_requires_explicit_dimension() -> None:
    with pytest.raises(ValidationError, match="embedding.dimension"):
        ProjectConfig.from_yaml(
            {
                "workspace": "demo",
                "sources": {},
                "embedding": {
                    "provider": "deterministic",
                    "model": "deterministic-v1",
                },
            }
        )


def test_treedb_config_validation_rejects_unknown_adapter() -> None:
    with pytest.raises(ValidationError, match="treedb.adapter"):
        ProjectConfig.from_yaml(
            {
                "workspace": "demo",
                "sources": {},
                "embedding": {
                    "provider": "deterministic",
                    "model": "deterministic-v1",
                    "dimension": 32,
                },
                "treedb": {"adapter": "unsupported"},
            }
        )


def test_doctor_reports_missing_sentence_transformers_dependency(tmp_path, monkeypatch) -> None:
    workspace = init_workspace(tmp_path, "demo", force=False)
    config = read_config(workspace)
    config.embedding = EmbeddingConfig(
        provider="sentence-transformers",
        model="all-MiniLM-L6-v2",
        dimension=384,
    )
    write_config(workspace, config)

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: object, **kwargs: object):
        if name == "sentence_transformers":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    report, exit_code = doctor_report(workspace)

    assert exit_code == 0
    assert report["ok"] is True
    assert report["embedding"]["status"] == "missing_dependency"
    assert any(
        warning["code"] == "missing_embedding_dependency"
        for warning in report["warnings"]
    )


def test_deterministic_embedding_is_stable_and_dimensioned() -> None:
    provider = DeterministicEmbeddingProvider(
        EmbeddingConfig(
            provider="deterministic",
            model="deterministic-v1",
            dimension=8,
        )
    )

    first = provider.embed(["same text"])[0]
    second = provider.embed(["same text"])[0]

    assert first == second
    assert len(first) == 8


def test_index_upserts_chunks_and_writes_local_state(tmp_path) -> None:
    workspace, config = make_workspace(tmp_path)
    adapter = RecordingAdapter()

    report = index_workspace(
        workspace,
        config,
        adapter_factory=adapter_factory(adapter),
    )

    assert report["upserted_chunks"] == 1
    assert report["adapter_document_count"] == 1
    assert len(adapter.upsert_batches) == 1
    document = adapter.upsert_batches[0][0]
    assert document.content == "# Guide\nbody"
    assert document.meta["path"] == "guide.md"
    assert document.meta["embedding_provider"] == "deterministic"
    state_path = workspace.state_dir / "index-state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["embedding_dimension"] == 32
    assert list(state["sources"]["docs"]["files"]) == ["guide.md"]
    assert state["treedb_adapter"] == "haystack"
    assert state["treedb_base_url"] == "http://127.0.0.1:7120"


def test_incremental_index_skips_unchanged_chunks(tmp_path) -> None:
    workspace, config = make_workspace(tmp_path)
    adapter = RecordingAdapter()
    factory = adapter_factory(adapter)
    index_workspace(workspace, config, adapter_factory=factory)

    report = index_workspace(workspace, read_config(workspace), adapter_factory=factory)

    assert report["unchanged_file_count"] == 1
    assert report["changed_file_count"] == 0
    assert report["upserted_chunks"] == 0
    assert len(adapter.upsert_batches) == 1


def test_changed_files_delete_old_chunk_ids_and_upsert_new_chunks(tmp_path) -> None:
    workspace, config = make_workspace(tmp_path)
    adapter = RecordingAdapter()
    factory = adapter_factory(adapter)
    index_workspace(workspace, config, adapter_factory=factory)
    old_ids = sorted(adapter.documents)
    (tmp_path / "docs" / "guide.md").write_text("# Guide\nchanged\n", encoding="utf-8")

    report = index_workspace(workspace, read_config(workspace), adapter_factory=factory)

    assert report["changed_file_count"] == 1
    assert report["deleted_chunks"] == 1
    assert report["upserted_chunks"] == 1
    assert adapter.delete_batches[-1] == old_ids
    assert sorted(adapter.documents) != old_ids


def test_deleted_files_call_adapter_delete_and_remove_state(tmp_path) -> None:
    workspace, config = make_workspace(tmp_path)
    adapter = RecordingAdapter()
    factory = adapter_factory(adapter)
    index_workspace(workspace, config, adapter_factory=factory)
    old_ids = sorted(adapter.documents)
    (tmp_path / "docs" / "guide.md").unlink()

    report = index_workspace(workspace, read_config(workspace), adapter_factory=factory)

    assert report["deleted_file_count"] == 1
    assert report["deleted_chunks"] == 1
    assert adapter.delete_batches[-1] == old_ids
    state = json.loads((workspace.state_dir / "index-state.json").read_text())
    assert state["sources"]["docs"]["files"] == {}


def test_embedding_dimension_mismatch_fails_clearly(tmp_path) -> None:
    workspace, config = make_workspace(tmp_path)
    adapter = RecordingAdapter()

    with pytest.raises(IndexingError, match="dimension mismatch"):
        index_workspace(
            workspace,
            config,
            provider_factory=lambda _config: BadDimensionProvider(),
            adapter_factory=adapter_factory(adapter),
        )


def test_status_reports_local_state_and_optional_adapter_health(tmp_path) -> None:
    workspace, config = make_workspace(tmp_path)
    adapter = RecordingAdapter()
    factory = adapter_factory(adapter)
    index_workspace(workspace, config, adapter_factory=factory)

    report = status_workspace(
        workspace,
        read_config(workspace),
        check_service=True,
        adapter_factory=factory,
    )

    assert report["indexed_file_count"] == 1
    assert report["indexed_chunk_count"] == 1
    assert report["changed_file_count"] == 0
    assert report["treedb"]["adapter_document_count"] == 1
    assert report["treedb"]["health"]["ok"] is True


def test_memory_adapter_is_self_contained_and_validates_dimensions() -> None:
    adapter = InMemoryTreeDBAdapter(
        TreeDBConfig(adapter="memory", index="docs"),
        embedding_dimension=3,
    )
    document = IndexDocument(
        id="chunk-1",
        content="body",
        embedding=[0.1, 0.2, 0.3],
        meta={"path": "guide.md"},
    )

    assert adapter.health()["ok"] is True
    assert adapter.upsert_documents([document]) == 1
    assert adapter.count_documents() == 1
    assert adapter.documents["chunk-1"].meta["path"] == "guide.md"
    with pytest.raises(TreeDBAdapterError, match="embedding dimension"):
        adapter.upsert_documents(
            [IndexDocument(id="bad", content="x", embedding=[0.0], meta={})]
        )
    assert adapter.delete_documents(["chunk-1", "missing"]) == 1
    assert adapter.count_documents() == 0


def test_cli_index_and_status_smoke_with_memory_adapter(tmp_path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from treedb_project_memory.cli import app

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\nbody\n", encoding="utf-8")
    assert runner.invoke(app, ["init", "--workspace", "demo"]).exit_code == 0
    assert runner.invoke(app, ["add", "docs", "--id", "docs"]).exit_code == 0

    config_path = tmp_path / ".treedb-project-memory" / "config.yaml"
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_data["treedb"]["adapter"] = "memory"
    config_path.write_text(yaml.safe_dump(config_data), encoding="utf-8")

    index_result = runner.invoke(app, ["index", "--format", "json"])
    status_result = runner.invoke(app, ["status", "--format", "json"])

    assert index_result.exit_code == 0, index_result.output
    index_payload = json.loads(index_result.output)
    assert index_payload["upserted_chunks"] == 1
    assert index_payload["treedb"]["adapter"] == "memory"
    assert status_result.exit_code == 0, status_result.output
    status_payload = json.loads(status_result.output)
    assert status_payload["indexed_chunk_count"] == 1
    assert status_payload["treedb"]["adapter"] == "memory"


def test_real_treedb_adapter_missing_dependencies_fail_clearly(monkeypatch) -> None:
    real_import_module = treedb_adapter_module.importlib.import_module

    def fake_import_module(name: str):
        if name == "haystack":
            raise ImportError("missing haystack")
        return real_import_module(name)

    monkeypatch.setattr(treedb_adapter_module.importlib, "import_module", fake_import_module)

    with pytest.raises(TreeDBAdapterError, match="optional upstream packages"):
        HaystackTreeDBAdapter(
            TreeDBConfig(base_url="http://127.0.0.1:1", index="docs"),
            embedding_dimension=32,
        )


@pytest.mark.skipif(
    not os.environ.get("TREEDB_PROJECT_MEMORY_REAL_TREEDB_URL"),
    reason="real TreeDB service integration requires TREEDB_PROJECT_MEMORY_REAL_TREEDB_URL",
)
def test_real_treedb_service_adapter_round_trip() -> None:
    url = os.environ["TREEDB_PROJECT_MEMORY_REAL_TREEDB_URL"]
    config = TreeDBConfig(
        adapter="haystack",
        base_url=url,
        index=f"tpm_test_{uuid4().hex}",
    )
    adapter = create_treedb_adapter(config, embedding_dimension=3)
    document = IndexDocument(
        id=f"chunk-{uuid4().hex}",
        content="real integration body",
        embedding=[1.0, 0.0, 0.0],
        meta={"source_id": "integration"},
    )

    assert adapter.health()
    assert adapter.upsert_documents([document]) == 1
    assert adapter.count_documents() >= 1
    adapter.delete_documents([document.id])
