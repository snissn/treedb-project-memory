# treedb-project-memory

`treedb-project-memory` is a pre-alpha local project-memory tool for user-owned
source material: repositories, folders, docs, notes, exports, and other local
knowledge sources.

The intended product is described in [spec.md](spec.md). This repository now
contains the bounded indexing and retrieval MVP: workspace-local YAML config
commands, source scanning, chunking, deterministic test embeddings, optional
embedding providers, a TreeDB/Haystack adapter boundary, a self-contained memory
adapter for smoke tests, incremental local index state, cited search, optional
ask, retrieval traces, and status/doctor diagnostics.

## Status

This project is pre-alpha. Public APIs, command behavior, configuration files,
storage formats, and packaging details may change without compatibility
guarantees.

Implemented now:

- Python package skeleton: `treedb_project_memory`.
- CLI entry point: `treedb-project-memory`.
- Help and version output.
- Workspace initialization with `.treedb-project-memory/config.yaml`.
- Source manifest editing with `add`.
- Source listing with text and JSON output.
- Config-only `doctor` diagnostics with text and JSON output.
- Source scanning and chunking for repo, folder, markdown, text, file, and JSONL
  sources.
- `index --dry-run` counts scanned files, source documents, chunks, skipped
  entries, and warnings without writing TreeDB or embedding state.
- Non-dry-run `index` embeds changed chunks, upserts them through the configured
  adapter, deletes stale chunk IDs, and writes
  `.treedb-project-memory/state/index-state.json`.
- `status` reports local index state and optionally checks the configured
  adapter.
- `search` retrieves cited indexed chunks without requiring an answer generator.
- `ask` uses retrieval plus a configured answer generator, or fails clearly when
  no generator is configured.
- Retrieval traces show query mode, filters, document IDs, scores, citations,
  and adapter timing details through `--explain`.
- Deterministic embeddings for self-contained CI and smoke tests.
- Optional local `sentence-transformers` embeddings through a dynamic import.
- Optional OpenAI-compatible remote embeddings through direct HTTP calls.
- Optional real TreeDB/Haystack indexing through dynamic imports of upstream
  Haystack and TreeDB packages.
- Explicit `treedb.adapter: memory` for self-contained non-persistent smoke
  runs.
- Smoke tests and config CLI behavior tests.
- GitHub Actions test workflow.

Not implemented yet:

- UI or advanced diagnostics.
- Retrieval/search emulation, ANN claims, or high-QPS performance claims.

## Development Setup

Use Python 3.10 or newer.

```sh
python -m pip install -e ".[dev]"
treedb-project-memory --help
pytest
```

For the issue #2 required editable install check without dev extras:

```sh
python -m pip install -e .
```

## CLI Contract

The current CLI guarantees:

- `treedb-project-memory --help` exits successfully and describes the current
  workspace tooling.
- `treedb-project-memory --version` prints the installed package version.
- `treedb-project-memory init` creates `.treedb-project-memory/config.yaml` and
  `.treedb-project-memory/state/` in the current directory.
- `treedb-project-memory init --force` overwrites an existing config.
- `treedb-project-memory add <root>` adds a source entry to the workspace config
  without scanning or indexing file contents.
- `treedb-project-memory sources --format json` emits parseable configured
  source data.
- `treedb-project-memory index --dry-run` scans configured sources, chunks
  readable content, reports counts, and leaves `.treedb-project-memory/state/`
  untouched.
- `treedb-project-memory index --dry-run --json` emits the same dry-run summary
  as parseable JSON.
- `treedb-project-memory index` embeds changed chunks and upserts them through
  the configured adapter. Unchanged files are skipped from persisted chunk
  hashes. Deleted files produce adapter delete calls for stale chunk IDs.
- `treedb-project-memory status --format json` emits parseable local source and
  index state.
- `treedb-project-memory status --check-service` also instantiates the selected
  adapter and reports health/count when available.
- `treedb-project-memory search <query>` retrieves cited chunks from the
  configured index without requiring an answer generator.
- `treedb-project-memory search <query> --json --explain` emits parseable
  results plus the retrieval trace.
- `treedb-project-memory ask <question>` requires
  `answer_generator.provider` to be configured. Without it, the command fails
  clearly and points users to `search`.
- `treedb-project-memory ask <question> --json --explain` emits the generated
  cited answer, supporting results, and retrieval trace.
- `treedb-project-memory doctor --format json` emits parseable config, root,
  optional dependency, and configured service diagnostics.

Future issues will add UI workflows, packaging polish, scale evidence, and
richer inspection commands. Until those issues land, documentation and PRs
should not claim those workflows are implemented.

Example config:

```yaml
workspace: my-project
sources:
  docs:
    type: folder
    root: /Users/me/docs
    include:
      - "**/*.md"
      - "**/*.txt"
    exclude: []
    max_file_bytes: 1048576
    follow_symlinks: false
  issues:
    type: jsonl
    root: /Users/me/exports/issues.jsonl
    include:
      - "**/*.jsonl"
    exclude: []
    max_file_bytes: 1048576
    follow_symlinks: false
    content_field: body
retrieval:
  default_mode: hybrid
  top_k: 8
answer_generator:
  provider: null
  max_context_chunks: 4
embedding:
  provider: deterministic
  model: deterministic-v1
  dimension: 32
  batch_size: 32
treedb:
  adapter: haystack
  base_url: http://127.0.0.1:7120
  index: project_memory
  similarity: cosine
  service_lifecycle: external
  timeout_seconds: 30.0
  ensure_index: true
```

Source entries are keyed by stable user-visible IDs. `add --id <id>` preserves an
explicit ID; otherwise the ID is generated from the source path basename.
Supported source types are `repo`, `folder`, `markdown`, `text`, `jsonl`, and
`file`. Relative roots are normalized to absolute paths before writing config.
`repo` sources always exclude `.git/**`, even if the config omits that exclude.

Scanner policy:

- Include patterns select candidate files; exclude patterns override includes.
- Symlinks are skipped by default and produce warnings. Set
  `follow_symlinks: true` for a source to follow them.
- Files larger than `max_file_bytes` are skipped with warnings.
- Binary or non-UTF-8 files are skipped with warnings.
- JSONL uses a top-level string `content_field` value as record content. Blank
  lines are ignored. Malformed records, non-object records, and records missing a
  non-empty string content field are skipped with warnings.

Chunks carry citation-ready metadata: workspace ID, source ID/type/root,
relative and absolute paths, deterministic chunk ID, content and document hashes,
chunk kind, chunk index, line range, size, mtime, language when detected, and
titles for heading or JSONL metadata when available. Dry-run metadata is
deterministic and does not include an `indexed_at` timestamp because no index
write occurs.

## Indexing Adapters

The default `treedb.adapter: haystack` path is the real TreeDB/Haystack boundary.
It imports optional upstream packages only when selected:

- `haystack.Document`
- `haystack.document_stores.types.DuplicatePolicy`
- `haystack_integrations.document_stores.treedb.TreeDBDocumentStore`

Those packages are not mandatory dependencies of this project. If they are
missing, `doctor` reports warnings and non-dry-run indexing fails clearly instead
of silently falling back.

For self-contained local smoke tests, set:

```yaml
treedb:
  adapter: memory
```

The memory adapter validates document dimensions and exercises the same
upsert/delete boundary, but it does not persist documents across CLI processes.
The local index state file is still written, so `status` can report source/index
freshness after the smoke run.

## Search, Ask, And Retrieval Traces

Retrieval modes are explicit:

- `keyword` scores text matches without embedding the query.
- `semantic` embeds the query and asks the adapter for vector retrieval.
- `hybrid` combines keyword and semantic scoring only where the selected adapter
  supports it.

Unsupported modes, filters, and adapter combinations fail clearly. In
particular, this project does not silently fetch every document and filter or
rank it client-side to emulate unsupported TreeDB/Haystack capabilities. Source
filtering is only passed to retrievers that support source metadata filters.

Search does not require an answer generator:

```sh
treedb-project-memory search "workspace indexing" --mode keyword --explain
```

JSON output is available for automation:

```sh
treedb-project-memory search "workspace indexing" --mode keyword --json --explain
```

`ask` uses the same retrieval path, then calls the configured answer generator.
The default config intentionally has no generator:

```text
ask requires answer_generator.provider to be configured; search works without an answer generator
```

For self-contained local use and tests, the deterministic `extractive` generator
can be enabled:

```yaml
answer_generator:
  provider: extractive
  max_context_chunks: 4
```

The extractive generator formats snippets from retrieved chunks with citations.
It is not a hosted LLM default.

`--explain` includes the query, retrieval mode, filters, requested `top_k`,
retrieved document IDs, scores, selected citations, adapter/index identity,
whether query embeddings were used, and retrieval elapsed seconds.

The real `treedb.adapter: haystack` path imports TreeDB/Haystack components only
when selected. Retrieval requires upstream retriever components. If those
components are missing or a selected mode is unsupported, commands fail with an
explicit capability error instead of falling back.

Example dry run:

```sh
treedb-project-memory index --dry-run
```

```text
Dry-run index summary
Workspace: my-project
Sources: 2
Files scanned: 3
Documents: 4
Chunks: 5
Skipped: 1
Warnings: 1
docs    folder  files=2 documents=2 chunks=3 skipped=0 warnings=0
issues  jsonl   files=1 documents=2 chunks=2 skipped=1 warnings=1
Warnings:
- issues:issues.jsonl:3 [malformed_jsonl] malformed JSONL record skipped at line 3: ...
```

`doctor` validates workspace config shape, source root existence, optional
embedding dependencies for the selected provider, optional TreeDB/Haystack
dependencies for the selected adapter, and external TreeDB service reachability
for the real adapter. It does not embed text or mutate index state.

## Repo Contract

- Work on topic branches; do not push implementation work directly to `main`.
- Keep PRs focused on the linked issue and parent tracker.
- Include tests for behavior changes, or state why tests do not apply.
- Include performance evidence only for performance-sensitive changes. This
  bootstrap scaffold has no benchmark requirement.
- Request AI reviews only after the PR body, local checks, and code are mature
  enough for review.
- If CI exists, use latest-head CI when claiming mergeability.

## License

No open-source license has been selected yet. Do not publish or redistribute this
package until the repository owner chooses a license.
