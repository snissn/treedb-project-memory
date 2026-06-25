from __future__ import annotations

import json
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .answers import AnswerError, ask_workspace
from .config import WorkspaceError, discover_workspace, doctor_report, read_config
from .indexing import IndexingError, status_workspace
from .retrieval import RetrievalError, search_workspace


class UIServerError(Exception):
    """Raised when the local UI server cannot start."""


@dataclass(frozen=True)
class UISettings:
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = False
    check_service: bool = False


class MemoryConsoleBackend:
    """Thin UI boundary over the same workspace services used by the CLI."""

    def status(self, *, check_service: bool = False) -> dict[str, Any]:
        try:
            workspace = discover_workspace()
            config = read_config(workspace)
            status = status_workspace(
                workspace,
                config,
                check_service=check_service,
            )
            doctor, doctor_exit = doctor_report(workspace)
        except (WorkspaceError, IndexingError) as exc:
            return {
                "ok": False,
                "error": {"code": "status_unavailable", "message": str(exc)},
                "status": None,
                "doctor": None,
            }
        return {
            "ok": doctor_exit == 0 and not status.get("warnings"),
            "status": status,
            "doctor": doctor,
            "error": None,
        }

    def doctor(self) -> dict[str, Any]:
        try:
            workspace = discover_workspace()
        except WorkspaceError as exc:
            return {
                "ok": False,
                "workspace_root": None,
                "errors": [{"code": "workspace_not_found", "message": str(exc)}],
                "warnings": [],
                "sources": [],
            }
        report, _exit_code = doctor_report(workspace)
        return report

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        query = _required_string(request, "query")
        mode = _optional_string(request.get("mode"))
        source_id = _optional_string(request.get("source_id"))
        top_k = _optional_positive_int(request.get("top_k"), "top_k")
        workspace = discover_workspace()
        config = read_config(workspace)
        results, trace = search_workspace(
            workspace,
            config,
            query=query,
            mode=mode,
            top_k=top_k,
            source_id=source_id,
        )
        return {
            "query": query,
            "mode": trace.mode,
            "results": [result.to_json() for result in results],
            "trace": trace.to_json(),
        }

    def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        query = _required_string(request, "query")
        mode = _optional_string(request.get("mode"))
        source_id = _optional_string(request.get("source_id"))
        top_k = _optional_positive_int(request.get("top_k"), "top_k")
        workspace = discover_workspace()
        config = read_config(workspace)
        answer = ask_workspace(
            workspace,
            config,
            query=query,
            mode=mode,
            top_k=top_k,
            source_id=source_id,
        )
        return answer.to_json()


def create_ui_server(
    settings: UISettings,
    *,
    backend: MemoryConsoleBackend | None = None,
) -> ThreadingHTTPServer:
    backend = backend or MemoryConsoleBackend()
    handler = _handler_factory(backend, settings)
    try:
        server = ThreadingHTTPServer((settings.host, settings.port), handler)
    except OSError as exc:
        raise UIServerError(str(exc)) from exc
    server.daemon_threads = True
    return server


def serve_ui(settings: UISettings) -> str:
    server = create_ui_server(settings)
    url = server_url(server)
    if settings.open_browser:
        threading.Timer(0.15, webbrowser.open, args=(url,)).start()
    print(f"treedb-project-memory UI listening on {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url


def server_url(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    return f"http://{host}:{port}/"


def _handler_factory(
    backend: MemoryConsoleBackend,
    settings: UISettings,
) -> type[BaseHTTPRequestHandler]:
    class MemoryConsoleHandler(BaseHTTPRequestHandler):
        server_version = "TreeDBProjectMemoryUI/0.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_bytes(
                    HTTPStatus.OK,
                    INDEX_HTML.encode("utf-8"),
                    "text/html; charset=utf-8",
                )
                return
            if parsed.path == "/static/app.css":
                self._send_bytes(
                    HTTPStatus.OK,
                    APP_CSS.encode("utf-8"),
                    "text/css; charset=utf-8",
                )
                return
            if parsed.path == "/static/app.js":
                self._send_bytes(
                    HTTPStatus.OK,
                    APP_JS.encode("utf-8"),
                    "application/javascript; charset=utf-8",
                )
                return
            if parsed.path == "/api/status":
                params = parse_qs(parsed.query)
                check_service = _truthy(params.get("check_service", [""])[0])
                self._send_json(
                    HTTPStatus.OK,
                    backend.status(check_service=check_service or settings.check_service),
                )
                return
            if parsed.path == "/api/doctor":
                self._send_json(HTTPStatus.OK, backend.doctor())
                return
            if parsed.path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "service": "ui"})
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": {"code": "not_found", "message": parsed.path}},
            )

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/search":
                self._handle_action(backend.search)
                return
            if parsed.path == "/api/ask":
                self._handle_action(backend.ask)
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": {"code": "not_found", "message": parsed.path}},
            )

        def _handle_action(self, action: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
            try:
                request = self._read_json_body()
                started = time.perf_counter()
                payload = action(request)
                payload = {
                    "ok": True,
                    "elapsed_seconds": time.perf_counter() - started,
                    **payload,
                }
                self._send_json(HTTPStatus.OK, payload)
            except (WorkspaceError, RetrievalError, AnswerError, IndexingError, ValueError) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": {
                            "code": exc.__class__.__name__,
                            "message": str(exc),
                        },
                    },
                )

        def _read_json_body(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Content-Length must be an integer") from exc
            if length <= 0:
                return {}
            if length > 64_000:
                raise ValueError("request body is too large")
            data = self.rfile.read(length)
            try:
                payload = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"request JSON is invalid: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("request JSON must be an object")
            return payload

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self._send_bytes(status, body, "application/json; charset=utf-8")

        def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

    return MemoryConsoleHandler


def _required_string(request: dict[str, Any], field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string values must be strings")
    value = value.strip()
    return value or None


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TreeDB Project Memory Console</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">Local workspace</p>
        <h1>Memory Console</h1>
      </div>
      <div class="topbar-actions">
        <button id="refresh" type="button">Refresh</button>
        <span id="connection" class="status-pill">Starting</span>
      </div>
    </header>

    <section class="overview" aria-label="Workspace status">
      <div>
        <span class="metric-label">Workspace</span>
        <strong id="workspace-name">-</strong>
      </div>
      <div>
        <span class="metric-label">Sources</span>
        <strong id="source-count">0</strong>
      </div>
      <div>
        <span class="metric-label">Indexed chunks</span>
        <strong id="chunk-count">0</strong>
      </div>
      <div>
        <span class="metric-label">Warnings</span>
        <strong id="warning-count">0</strong>
      </div>
    </section>

    <section class="workbench">
      <aside class="sidebar" aria-label="Sources and diagnostics">
        <div class="section-head">
          <h2>Sources</h2>
          <span id="state-path"></span>
        </div>
        <div id="sources" class="source-list"></div>
        <div class="section-head compact">
          <h2>Doctor</h2>
          <span id="doctor-ok"></span>
        </div>
        <div id="doctor" class="doctor"></div>
      </aside>

      <section class="main-panel" aria-label="Search and ask">
        <nav class="tabs" aria-label="Console mode">
          <button class="tab is-active" type="button" data-mode="search">Search</button>
          <button class="tab" type="button" data-mode="ask">Ask</button>
        </nav>

        <form id="query-form" class="query-form">
          <label for="query">Query</label>
          <textarea id="query" name="query" rows="3" required placeholder="Find indexed source material"></textarea>
          <div class="controls">
            <label>Mode
              <select id="mode" name="mode">
                <option value="">Config default</option>
                <option value="keyword">Keyword</option>
                <option value="semantic">Semantic</option>
                <option value="hybrid">Hybrid</option>
              </select>
            </label>
            <label>Top K
              <input id="top-k" name="top_k" type="number" min="1" placeholder="default">
            </label>
            <label>Source
              <select id="source-filter" name="source_id">
                <option value="">All sources</option>
              </select>
            </label>
            <button id="submit" type="submit">Run</button>
          </div>
        </form>

        <div id="error" class="error" hidden></div>
        <div id="answer" class="answer" hidden></div>
        <div id="results" class="results"></div>
        <details id="trace-wrap" class="trace" open>
          <summary>Retrieval trace</summary>
          <pre id="trace"></pre>
        </details>
      </section>
    </section>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
"""


APP_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f4;
  --panel: #ffffff;
  --ink: #17201b;
  --muted: #65726b;
  --line: #d9ded8;
  --accent: #146c5f;
  --accent-ink: #ffffff;
  --warning: #8a4a00;
  --error: #a12626;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-width: 320px;
  background: var(--bg);
  color: var(--ink);
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button, input, select, textarea {
  font: inherit;
}

.shell {
  min-height: 100vh;
  padding: 18px;
}

.topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 14px;
}

.eyebrow {
  margin: 0 0 3px;
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
}

h1, h2, p { margin-top: 0; }
h1 { margin-bottom: 0; font-size: 30px; line-height: 1.05; }
h2 { margin-bottom: 0; font-size: 15px; }

.topbar-actions, .controls, .tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

button {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--panel);
  color: var(--ink);
  min-height: 34px;
  padding: 6px 10px;
  cursor: pointer;
}

button:hover, .tab.is-active {
  border-color: var(--accent);
  color: var(--accent);
}

#submit {
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}

.status-pill {
  min-width: 86px;
  text-align: center;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 6px 10px;
  color: var(--muted);
  background: var(--panel);
}

.status-pill.is-ok { color: var(--accent); }
.status-pill.is-error { color: var(--error); }

.overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 14px 0;
  border: 1px solid var(--line);
  background: var(--line);
}

.overview > div {
  background: var(--panel);
  padding: 12px;
  min-height: 72px;
}

.metric-label {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}

.overview strong {
  display: block;
  overflow-wrap: anywhere;
  font-size: 20px;
  line-height: 1.1;
}

.workbench {
  display: grid;
  grid-template-columns: minmax(260px, 360px) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.sidebar, .main-panel {
  background: var(--panel);
  border: 1px solid var(--line);
}

.sidebar {
  min-height: 540px;
  padding: 14px;
}

.main-panel {
  min-height: 540px;
  padding: 14px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: baseline;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.section-head span {
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.compact { margin-top: 18px; }

.source-list {
  display: grid;
  gap: 8px;
  padding: 12px 0;
}

.source {
  border-bottom: 1px solid var(--line);
  padding-bottom: 8px;
}

.source strong, .result strong {
  display: block;
  overflow-wrap: anywhere;
}

.source span, .source small, .result small, .doctor small {
  color: var(--muted);
  overflow-wrap: anywhere;
}

.tabs {
  border-bottom: 1px solid var(--line);
  padding-bottom: 10px;
}

.query-form {
  display: grid;
  gap: 10px;
  margin: 14px 0;
}

.query-form label {
  display: grid;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
}

textarea, input, select {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--ink);
  padding: 8px;
}

textarea {
  min-height: 92px;
  resize: vertical;
}

.controls {
  display: grid;
  grid-template-columns: minmax(120px, 160px) minmax(90px, 120px) minmax(160px, 1fr) auto;
  align-items: end;
}

.error, .answer {
  border-left: 3px solid var(--accent);
  background: #f7faf8;
  padding: 10px 12px;
  margin-bottom: 12px;
  overflow-wrap: anywhere;
}

.error {
  border-left-color: var(--error);
  color: var(--error);
  background: #fff8f7;
}

.result {
  border-top: 1px solid var(--line);
  padding: 12px 0;
}

.result p {
  margin: 7px 0;
  line-height: 1.45;
}

.citation-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.citation {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 7px;
  color: var(--accent);
  font-size: 12px;
  text-decoration: none;
}

.citation:hover {
  border-color: var(--accent);
}

.trace {
  margin-top: 14px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

pre {
  max-height: 320px;
  overflow: auto;
  background: #101513;
  color: #dce8df;
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.4;
}

.doctor {
  display: grid;
  gap: 8px;
  padding-top: 10px;
}

.doctor-item {
  border-bottom: 1px solid var(--line);
  padding-bottom: 8px;
}

@media (max-width: 860px) {
  .shell { padding: 12px; }
  .overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .workbench { grid-template-columns: 1fr; }
  .sidebar, .main-panel { min-height: auto; }
  .controls { grid-template-columns: 1fr 1fr; }
  #submit { grid-column: 1 / -1; }
}
"""


APP_JS = """
const state = { mode: "search", lastStatus: null };

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  $(id).textContent = value == null || value === "" ? "-" : String(value);
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function appendText(parent, tag, text, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text == null ? "" : String(text);
  parent.appendChild(node);
  return node;
}

function appendCitationLink(parent, citation) {
  const link = document.createElement("a");
  link.className = "citation";
  link.href = citationHref(citation);
  link.textContent = citation.label || citation.path || citation.chunk_id || "citation";
  link.setAttribute("aria-label", `Citation ${link.textContent}`);
  parent.appendChild(link);
  return link;
}

function citationHref(citation) {
  const params = new URLSearchParams();
  if (citation.source_id) params.set("source", citation.source_id);
  if (citation.path) params.set("path", citation.path);
  if (citation.start_line != null) params.set("line", String(citation.start_line));
  if (citation.end_line != null) params.set("end", String(citation.end_line));
  if (citation.chunk_id) params.set("chunk", citation.chunk_id);
  return `#citation?${params.toString()}`;
}

async function requestJson(url, options = {}) {
  const { allowNotOkPayload = false, ...fetchOptions } = options;
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...fetchOptions,
  });
  const payload = await response.json();
  if (!response.ok || (!allowNotOkPayload && payload.ok === false)) {
    const message = payload.error && payload.error.message ? payload.error.message : "Request failed";
    throw new Error(message);
  }
  return payload;
}

function setConnection(ok, label) {
  const node = $("connection");
  node.textContent = label;
  node.classList.toggle("is-ok", ok);
  node.classList.toggle("is-error", !ok);
}

async function loadStatus() {
  try {
    const payload = await requestJson("/api/status", { allowNotOkPayload: true });
    state.lastStatus = payload.status;
    renderStatus(payload);
    setConnection(true, "Ready");
  } catch (error) {
    renderStatus({ ok: false, error: { message: error.message }, status: null, doctor: null });
    setConnection(false, "Error");
  }
}

function renderStatus(payload) {
  const status = payload.status || {};
  const doctor = payload.doctor || {};
  setText("workspace-name", status.workspace || doctor.workspace || "Unavailable");
  setText("source-count", status.source_count || 0);
  setText("chunk-count", status.indexed_chunk_count || 0);
  setText("warning-count", status.warning_count || (doctor.warnings || []).length || 0);
  setText("state-path", status.state_path || "");
  setText("doctor-ok", doctor.ok === true ? "OK" : "Needs attention");
  renderSources(status.sources || doctor.sources || []);
  renderDoctor(payload.error, doctor);
  renderSourceFilter(status.sources || doctor.sources || []);
}

function renderSources(sources) {
  const target = $("sources");
  clear(target);
  if (!sources.length) {
    appendText(target, "small", "No configured sources.");
    return;
  }
  for (const source of sources) {
    const row = document.createElement("div");
    row.className = "source";
    appendText(row, "strong", source.id || "source");
    appendText(row, "span", `${source.type || "unknown"} - ${source.root || ""}`);
    appendText(row, "small", `indexed ${source.indexed_chunks || 0} chunks, changed ${source.changed_files || 0}, warnings ${(source.warnings || []).length}`);
    target.appendChild(row);
  }
}

function renderSourceFilter(sources) {
  const select = $("source-filter");
  const current = select.value;
  clear(select);
  const all = document.createElement("option");
  all.value = "";
  all.textContent = "All sources";
  select.appendChild(all);
  for (const source of sources) {
    const option = document.createElement("option");
    option.value = source.id || "";
    option.textContent = source.id || "source";
    select.appendChild(option);
  }
  select.value = current;
}

function renderDoctor(error, doctor) {
  const target = $("doctor");
  clear(target);
  if (error && error.message) {
    const item = document.createElement("div");
    item.className = "doctor-item";
    appendText(item, "strong", "Workspace error");
    appendText(item, "small", error.message);
    target.appendChild(item);
    return;
  }
  const rows = [...(doctor.errors || []), ...(doctor.warnings || [])];
  if (!rows.length) {
    appendText(target, "small", "No diagnostics reported.");
    return;
  }
  for (const row of rows) {
    const item = document.createElement("div");
    item.className = "doctor-item";
    appendText(item, "strong", row.code || "diagnostic");
    appendText(item, "small", row.message || "");
    target.appendChild(item);
  }
}

function requestPayload() {
  const topKRaw = $("top-k").value.trim();
  return {
    query: $("query").value,
    mode: $("mode").value || null,
    top_k: topKRaw ? Number(topKRaw) : null,
    source_id: $("source-filter").value || null,
  };
}

async function runQuery(event) {
  event.preventDefault();
  $("submit").disabled = true;
  $("submit").textContent = "Running";
  $("error").hidden = true;
  try {
    const endpoint = state.mode === "ask" ? "/api/ask" : "/api/search";
    const payload = await requestJson(endpoint, {
      method: "POST",
      body: JSON.stringify(requestPayload()),
    });
    renderResponse(payload);
    setConnection(true, `${Math.round((payload.elapsed_seconds || 0) * 1000)} ms`);
  } catch (error) {
    showError(error.message);
    setConnection(false, "Error");
  } finally {
    $("submit").disabled = false;
    $("submit").textContent = "Run";
  }
}

function showError(message) {
  const node = $("error");
  node.textContent = message;
  node.hidden = false;
}

function renderResponse(payload) {
  $("answer").hidden = !payload.answer;
  $("answer").textContent = payload.answer || "";
  renderResults(payload.results || []);
  $("trace").textContent = JSON.stringify(payload.trace || {}, null, 2);
}

function renderResults(results) {
  const target = $("results");
  clear(target);
  if (!results.length) {
    appendText(target, "small", "No results.");
    return;
  }
  for (const result of results) {
    const row = document.createElement("article");
    row.className = "result";
    const score = result.score == null ? "n/a" : Number(result.score).toPrecision(4);
    appendText(row, "strong", `${result.id}  score=${score}`);
    appendText(row, "p", result.content || "");
    const citations = document.createElement("div");
    citations.className = "citation-row";
    if (result.citation && result.citation.label) {
      appendCitationLink(citations, result.citation);
    }
    row.appendChild(citations);
    target.appendChild(row);
  }
}

function setMode(mode) {
  state.mode = mode;
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("is-active", button.dataset.mode === mode);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("refresh").addEventListener("click", loadStatus);
  $("query-form").addEventListener("submit", runQuery);
  for (const button of document.querySelectorAll(".tab")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }
  loadStatus();
});
"""
