import json
import socket
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any

from typer.testing import CliRunner

from treedb_project_memory.answers import AnswerError
from treedb_project_memory.cli import app
from treedb_project_memory.ui import APP_JS, UISettings, create_ui_server, server_url


runner = CliRunner()


class FixtureBackend:
    def status(self, *, check_service: bool = False) -> dict[str, Any]:
        assert check_service is False
        return {
            "ok": True,
            "status": {
                "workspace": "demo",
                "workspace_root": "/tmp/demo",
                "state_path": "/tmp/demo/.treedb-project-memory/state/index-state.json",
                "state_exists": True,
                "source_count": 1,
                "indexed_chunk_count": 2,
                "warning_count": 0,
                "sources": [
                    {
                        "id": "docs",
                        "type": "folder",
                        "root": "/tmp/demo/docs",
                        "indexed_chunks": 2,
                        "changed_files": 0,
                        "warnings": [],
                    }
                ],
            },
            "doctor": {
                "ok": True,
                "errors": [],
                "warnings": [],
                "sources": [],
            },
            "error": None,
        }

    def doctor(self) -> dict[str, Any]:
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "sources": [],
        }

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        assert request["query"] == "TreeDB citations"
        return {
            "query": request["query"],
            "mode": "keyword",
            "results": [
                {
                    "id": "chunk-a",
                    "score": 2.0,
                    "content": "TreeDB stores cited local memory.",
                    "metadata": {"source_id": "docs", "path": "guide.md"},
                    "citation": {
                        "source_id": "docs",
                        "path": "guide.md",
                        "start_line": 3,
                        "end_line": 5,
                        "title": None,
                        "chunk_id": "chunk-a",
                        "label": "guide.md:3-5",
                    },
                }
            ],
            "trace": {
                "query": request["query"],
                "mode": "keyword",
                "filters": {},
                "top_k": 1,
                "document_ids": ["chunk-a"],
                "scores": [2.0],
                "citations": [{"label": "guide.md:3-5"}],
                "details": {"adapter": "memory"},
            },
        }

    def ask(self, _request: dict[str, Any]) -> dict[str, Any]:
        raise AnswerError(
            "ask requires answer_generator.provider to be configured; "
            "search works without an answer generator"
        )


@contextmanager
def running_server(backend):
    server = create_ui_server(UISettings(port=0), backend=backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server_url(server)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_ui_server_starts_and_serves_health() -> None:
    with running_server(FixtureBackend()) as base_url:
        payload = get_json(base_url + "api/health")

    assert payload == {"ok": True, "service": "ui"}


def test_source_status_page_renders() -> None:
    with running_server(FixtureBackend()) as base_url:
        with urllib.request.urlopen(base_url, timeout=5) as response:
            html = response.read().decode("utf-8")
        payload = get_json(base_url + "api/status")

    assert response.status == 200
    assert "Memory Console" in html
    assert "Search" in html
    assert "Doctor" in html
    assert payload["status"]["workspace"] == "demo"
    assert payload["status"]["sources"][0]["id"] == "docs"


def test_search_submit_uses_fixture_backend_and_returns_citations() -> None:
    with running_server(FixtureBackend()) as base_url:
        status, payload = post_json(
            base_url + "api/search",
            {"query": "TreeDB citations", "mode": "keyword", "top_k": 1},
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["results"][0]["id"] == "chunk-a"
    assert payload["results"][0]["citation"]["label"] == "guide.md:3-5"


def test_ask_missing_generator_returns_clear_error() -> None:
    with running_server(FixtureBackend()) as base_url:
        status, payload = post_json(
            base_url + "api/ask",
            {"query": "What stores memory?", "mode": "keyword"},
        )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "AnswerError"
    assert "answer_generator.provider" in payload["error"]["message"]
    assert "search works without an answer generator" in payload["error"]["message"]


def test_citation_rendering_uses_text_content_not_html_injection() -> None:
    assert ".textContent =" in APP_JS
    assert ".innerHTML" not in APP_JS
    assert 'document.createElement("a")' in APP_JS
    assert "new URLSearchParams()" in APP_JS
    assert "#citation?" in APP_JS


def test_trace_response_renders_trace_contract() -> None:
    with running_server(FixtureBackend()) as base_url:
        _status, payload = post_json(
            base_url + "api/search",
            {"query": "TreeDB citations", "mode": "keyword", "top_k": 1},
        )

    assert payload["trace"]["document_ids"] == ["chunk-a"]
    assert payload["trace"]["scores"] == [2.0]
    assert payload["trace"]["citations"][0]["label"] == "guide.md:3-5"


def test_ui_server_startup_does_not_require_external_network(monkeypatch) -> None:
    def fail_connect(*_args, **_kwargs):
        raise AssertionError("external connect should not happen during UI startup")

    monkeypatch.setattr(socket, "create_connection", fail_connect)
    server = create_ui_server(UISettings(port=0), backend=FixtureBackend())
    try:
        assert server.server_address[1] > 0
    finally:
        server.server_close()


def test_ui_command_is_registered() -> None:
    result = runner.invoke(app, ["ui", "--help"])

    assert result.exit_code == 0
    assert "Start the local memory console web UI" in result.output
