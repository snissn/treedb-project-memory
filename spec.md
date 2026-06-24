# treedb-project-memory Development Spec

## Summary

Build `treedb-project-memory` as a local, customizable project-memory tool for
user-owned source material: repos, folders, docs, notes, exports, and other
local knowledge sources. The tool should let an individual clone or install it,
declare the sources they care about in a workspace-local config file, index
those sources into local TreeDB-backed document storage, and query them through
Haystack retrieval and answer pipelines with citations back to the original
files.

The product should not assume a global registry of projects. It should not make
`gomap` a privileged public example. `gomap` can be an internal dogfood source,
but the public framing is generic: bring your own sources, build local memory,
ask source-backed questions, and keep the data under your control.

Initial repository state: this repo starts empty. This document is the first
committed planning artifact and should be treated as the implementation tracker
until issues or milestones are created.

## Product Thesis

Developers and technical teams increasingly depend on local, scattered context:
source repos, markdown notes, implementation plans, design docs, benchmark
outputs, issue exports, support notes, and ad hoc research folders. Existing AI
workflows often lose that context between sessions or require copying sensitive
data into hosted systems.

`treedb-project-memory` should provide a durable local memory layer:

- Users explicitly choose local sources.
- The tool chunks, embeds, and indexes those sources into local TreeDB storage.
- Haystack pipelines expose retrieval, hybrid search, and answer generation.
- Responses cite exact source files, paths, symbols, and line ranges when known.
- Workspaces are portable and configurable without a central service.

TreeDB supplies the durable local document/index substrate. Haystack supplies the
pipeline and component model. The user-facing value is project memory that can be
installed, customized, inspected, rebuilt, and used across many projects.

## Goals

- Provide a local CLI that can initialize a workspace, add sources, index them,
  inspect index state, and ask questions.
- Support generic source types in the MVP: repo, folder, markdown/text docs, and
  structured JSONL/CSV exports where feasible.
- Store a workspace-local manifest that defines all sources and retrieval
  defaults. Avoid any global project registry.
- Use TreeDB as the local document store through the existing TreeDB Haystack
  integration path.
- Use Haystack pipelines for indexing, retrieval, and answer construction.
- Preserve source metadata needed for citations, filtering, incremental rebuilds,
  and user trust.
- Make the tool installable and usable by other people without editing internal
  source code.
- Include docs, examples, and tests that make the development path clear.
- Keep examples neutral and generic. Include `gomap` only as optional dogfood or
  a private/local example, not as the canonical scenario.

## Non-Goals

- No hosted service in the MVP.
- No global registry of known projects or shared central catalog.
- No team account model, cloud sync, remote auth, or multi-tenant server.
- No automatic code execution, code modification, or autonomous agent workflow.
- No claim that the exact dense-vector TreeDB path is an ANN/high-QPS production
  search engine until benchmarks justify it.
- No silent client-side scan to emulate unsupported TreeDB operations.
- No public demo that depends on private `gomap` knowledge.
- No complex migration framework while the underlying TreeDB APIs and formats are
  still pre-alpha.

## Product Principles

- Local first: the default workflow runs on a developer machine and uses local
  files plus a local TreeDB service.
- User-owned sources: users decide what gets indexed by editing workspace config.
- Workspace scoped: config, state, and source identity belong to the workspace.
- Generic by default: any repo or folder can be added; no source is special.
- Source-backed answers: answers should cite files, paths, and line ranges when
  the index has that metadata.
- Honest retrieval: if a query mode or filter is unsupported, report that clearly
  instead of fabricating a degraded result.
- Rebuildable state: users should be able to delete the index and rebuild from
  source manifests.
- Inspectable internals: provide commands to list sources, chunks, index stats,
  and retrieval traces.
- Small sharp MVP: start with reliable local ingest and query before broad
  connector expansion.

## Target Users

- Individual developers who want persistent memory across one or more repos.
- AI-heavy engineers who want local cited context for Codex, Haystack, notebooks,
  and scripts.
- Technical leads who maintain design docs, plans, and source trees across
  multiple projects.
- Researchers or builders who keep markdown notes, exports, and code examples in
  local folders.

## User Workflows

### Initialize a Workspace

```sh
treedb-project-memory init
```

Expected behavior:

- Create `.treedb-project-memory/config.yaml`.
- Create `.treedb-project-memory/state/` for local metadata.
- Record default embedding and retrieval settings.
- Offer a safe default TreeDB service location.
- Do not scan or index anything until sources are added.

### Add Local Sources

```sh
treedb-project-memory add ~/dev/my-app
treedb-project-memory add ~/notes/research --type folder
treedb-project-memory add ~/exports/issues.jsonl --type jsonl
```

Expected behavior:

- Add source entries to workspace config.
- Infer source type when possible, but let users override it.
- Generate stable source IDs from paths, with explicit rename support later.
- Store include/exclude defaults appropriate for source type.
- Do not maintain a hidden global registry.

### Edit Workspace Config

Example config:

```yaml
workspace: local-memory

sources:
  app:
    type: repo
    root: ~/dev/my-app
    include:
      - "**/*.py"
      - "**/*.ts"
      - "**/*.md"
    exclude:
      - ".git/**"
      - "node_modules/**"
      - "dist/**"
      - ".venv/**"

  research:
    type: folder
    root: ~/notes/research
    include:
      - "**/*.md"
      - "**/*.txt"

retrieval:
  default_mode: hybrid
  top_k: 8

embedding:
  provider: sentence-transformers
  model: all-MiniLM-L6-v2
```

### Index Sources

```sh
treedb-project-memory index
treedb-project-memory index --source app
treedb-project-memory index --changed
treedb-project-memory index --rebuild
```

Expected behavior:

- Scan configured sources.
- Chunk files according to source type.
- Compute content hashes and metadata fingerprints.
- Embed chunks.
- Upsert Haystack documents into TreeDB.
- Persist index state for incremental rebuilds.
- Print source counts, chunk counts, skip counts, and errors.

### Ask Questions

```sh
treedb-project-memory ask "Where is authentication initialized?"
treedb-project-memory ask "What benchmark evidence exists for insert throughput?" --source notes
treedb-project-memory ask "Which files define the API client?" --mode keyword
```

Expected behavior:

- Build a Haystack retrieval pipeline from workspace defaults.
- Retrieve relevant chunks from TreeDB.
- Optionally pass retrieved context to a configured answer generator.
- Return concise answers with citations.
- Show retrieval trace with `--explain`.
- Let users choose semantic, keyword, or hybrid retrieval modes.

### Inspect Memory

```sh
treedb-project-memory sources
treedb-project-memory status
treedb-project-memory search "checkpoint profile"
treedb-project-memory show <chunk-id>
treedb-project-memory doctor
```

Expected behavior:

- Show configured source list and whether roots exist.
- Show index freshness and changed-file counts.
- Show TreeDB service health and document counts.
- Inspect raw retrieved chunks for trust/debugging.
- Diagnose missing embedding dependencies and service configuration.

### Optional Local UI

```sh
treedb-project-memory ui
```

Expected behavior:

- Start a local web UI.
- Show sources, indexing status, search, ask, and cited results.
- Provide retrieval trace views for debugging.
- Avoid marketing-page framing. The first screen should be the working memory
  console.

## Current Technical Evidence

The implementation should build on existing local TreeDB/Haystack work, but this
repo should own the product wrapper and workspace UX.

Known upstream building blocks:

- `clients/python/treedb_haystack` in `snissn/gomap` provides a Haystack
  `TreeDBDocumentStore` plus TreeDB-backed embedding, keyword, and hybrid
  retrievers.
- `clients/python/treedb_client` provides the Python client layer without a
  Haystack dependency.
- `cmd/treedb-document-service` provides the TreeDB document service used by the
  client and document store.
- Existing examples cover basic ingest/retrieve, keyword and hybrid retrieval,
  and code-search metadata patterns.

Important constraints:

- The exact dense-vector route is a correctness/MVP path. Treat it as local
  project-memory retrieval, not as a large-scale ANN claim.
- Metadata filters are useful where supported by the TreeDB document service.
  The product should expose capability information clearly rather than
  pretending every filter is available in every retrieval mode.
- TreeDB is pre-alpha, so storage/API changes may require rebuilds. The product
  should be explicit that local indexes are rebuildable artifacts derived from
  source material.

## Terminology

- Workspace: a local directory containing `.treedb-project-memory/config.yaml`
  and associated local state.
- Source: a user-declared repo, folder, file, export, or connector input.
- Document: a Haystack document persisted into TreeDB.
- Chunk: a searchable unit derived from a source document.
- Citation: a pointer from a retrieved chunk back to source path, optional line
  range, symbol, commit, and source ID.
- Index state: local metadata used for incremental indexing and diagnostics.
- Retrieval trace: the list of queries, filters, document IDs, scores, and
  selected citations used to produce an answer.

## Proposed Repository Layout

```text
treedb-project-memory/
  spec.md
  README.md
  pyproject.toml

  treedb_project_memory/
    __init__.py
    cli.py
    config.py
    workspace.py
    diagnostics.py

    sources/
      __init__.py
      base.py
      repo.py
      folder.py
      markdown.py
      text.py
      jsonl.py

    chunking/
      __init__.py
      base.py
      code.py
      markdown.py
      text.py

    indexing/
      __init__.py
      pipeline.py
      embeddings.py
      schema.py
      state.py

    retrieval/
      __init__.py
      pipeline.py
      semantic.py
      keyword.py
      hybrid.py
      answer.py
      citations.py

    treedb/
      __init__.py
      service.py
      document_store.py
      capabilities.py

    ui/
      __init__.py
      app.py
      static/
      templates/

  examples/
    simple-repo/
    docs-folder/
    multi-source-memory/

  docs/
    concepts.md
    configuration.md
    source-types.md
    metadata-schema.md
    local-service.md
    development.md

  tests/
    unit/
    integration/
    fixtures/
```

This layout is a target, not a requirement for the first commit. It should guide
implementation unless a simpler structure proves more natural once code exists.

## Metadata Schema

Every indexed chunk should carry enough metadata for citations, filtering,
incremental rebuilds, and future UI inspection.

Required metadata:

- `workspace_id`: stable workspace identifier.
- `source_id`: key from workspace config.
- `source_type`: repo, folder, markdown, text, jsonl, or future connector type.
- `source_root`: normalized root path for the source.
- `path`: path relative to source root when possible.
- `absolute_path`: absolute path for local citation opening.
- `chunk_id`: stable ID derived from source ID, path, chunk position, and hash.
- `content_hash`: hash of chunk content.
- `document_hash`: hash of full source document content.
- `chunk_kind`: code, markdown_section, text_block, json_record, or similar.
- `start_line`: first source line when known.
- `end_line`: last source line when known.
- `indexed_at`: timestamp for current index write.

Recommended metadata:

- `language`: detected language for code files.
- `symbol`: function, class, heading, or object name when known.
- `repo_commit`: git commit for repo sources.
- `repo_branch`: git branch for repo sources.
- `mtime`: source modification time.
- `size_bytes`: source file size.
- `embedding_model`: embedding model used for the chunk.
- `embedding_dimension`: embedding vector dimension.
- `title`: heading or display title.
- `tags`: user-declared labels from config.

Schema rules:

- Metadata keys should be stable and documented.
- Unknown optional fields should be omitted rather than filled with fake values.
- User paths should remain local; no telemetry or remote reporting in MVP.
- Chunk IDs should be deterministic across rebuilds when content and config have
  not changed.

## Architecture

### Layer 1: Workspace and Config

Responsibilities:

- Locate workspace root.
- Read and validate config.
- Expand paths and environment variables.
- Persist local state.
- Support explicit workspace selection with `--workspace`.

Implementation notes:

- Use a typed config model such as Pydantic if the project already depends on it
  for clear validation errors.
- Keep config human-editable YAML.
- Maintain state separately from config so users can version config without
  committing local index internals.

### Layer 2: Source Adapters

Responsibilities:

- Enumerate source documents.
- Apply include/exclude rules.
- Normalize paths.
- Provide source metadata.
- Detect changed/deleted files for incremental indexing.

MVP adapters:

- Repo source: local git repository, excluding `.git` and common generated dirs.
- Folder source: arbitrary local directory.
- Markdown/text source: files or folders with markdown and plain text.
- JSONL source: records with configurable content and metadata fields.

Later adapters:

- GitHub issue export files.
- Google Docs export folders.
- Slack or Discord export folders.
- Benchmark artifact directories.
- User-defined Python source adapter plugins.

### Layer 3: Chunking

Responsibilities:

- Convert source documents into chunks.
- Preserve line ranges.
- Use source-aware chunking where useful.
- Avoid giant chunks that degrade embeddings and answers.

MVP behavior:

- Markdown: split by headings, then by size.
- Text: split by paragraphs and size.
- Code: line-aware chunks with optional symbol detection through lightweight
  heuristics.
- JSONL: one record per document or per configured content field.

Future behavior:

- Tree-sitter powered symbol chunking.
- Language-server assisted references.
- Notebook and rich document chunking.

### Layer 4: Embeddings

Responsibilities:

- Convert chunks into dense vectors.
- Track embedding model and dimension.
- Support deterministic rebuilds.
- Surface missing model/provider dependencies clearly.

MVP provider options:

- Local sentence-transformers default for offline use.
- OpenAI-compatible embedding provider as an optional configured backend.

Rules:

- Store embedding provider/model in config and chunk metadata.
- Refuse to mix incompatible embedding dimensions in one TreeDB document index
  unless a migration/rebuild flow is explicit.
- Provide `doctor` output when dependencies are missing.

### Layer 5: TreeDB Storage

Responsibilities:

- Start or connect to the TreeDB document service.
- Create/open the document index for the workspace.
- Upsert and delete documents.
- Expose capability information for retrieval/filter modes.
- Keep TreeDB service details behind a small local adapter.

Implementation options:

- Depend on existing `treedb-client` and `treedb-haystack` packages if they are
  published or locally installable.
- If upstream packages are not published yet, document a development install path
  and keep this repo's adapter thin.

Rules:

- Treat TreeDB indexes as rebuildable local artifacts.
- Do not hide TreeDB failures behind empty search results.
- Include service lifecycle diagnostics in `doctor`.

### Layer 6: Haystack Pipelines

Responsibilities:

- Build indexing pipelines from source documents to TreeDB documents.
- Build semantic, keyword, and hybrid retrieval pipelines.
- Build optional answer-generation pipelines over retrieved context.

MVP pipelines:

- Indexing: source documents -> chunking -> embedding -> TreeDB upsert.
- Search: query -> retriever -> cited results.
- Ask: query -> retriever -> answer generator -> cited answer.

Rules:

- Search should work without an LLM generator.
- Ask can require a configured generator.
- Retrieval mode should be explicit in output and traces.

### Layer 7: CLI

Responsibilities:

- Provide the primary user interface.
- Keep commands scriptable.
- Print concise human-readable output by default.
- Provide JSON output for automation where helpful.

MVP commands:

- `init`
- `add`
- `sources`
- `index`
- `status`
- `search`
- `ask`
- `show`
- `doctor`

### Layer 8: UI

Responsibilities:

- Provide a local visual console for people who prefer browsing memory.
- Show sources, status, search, ask, citations, and retrieval trace.
- Avoid being a marketing landing page.

MVP UI can follow CLI maturity. Do not block CLI usefulness on UI completion.

## Milestone Plan

### M0: Repository Bootstrap and Development Contract

Outcome:

- Repo has enough structure for others to install, run tests, and understand the
  product direction.

Tasks:

- [ ] Commit `spec.md`.
- [ ] Add `README.md` with concise product overview and current status.
- [ ] Add `pyproject.toml` with package metadata, CLI entry point, and dev
  dependencies.
- [ ] Add `LICENSE` if the project is intended to be public/open source.
- [ ] Add `.gitignore`.
- [ ] Add minimal package skeleton.
- [ ] Add test runner config.
- [ ] Add CI for lint/type/test once code exists.

Evidence required:

- `python -m pip install -e .` succeeds.
- `treedb-project-memory --help` succeeds.
- `pytest` succeeds, even if tests are initially skeletal.

PR policy:

- This initial spec commit may go directly to `main` because the user explicitly
  authorized commit and push on a new empty repo.
- After bootstrap, use topic branches and PRs for substantive implementation.

### M1: Workspace Config and CLI Skeleton

Outcome:

- A user can initialize a workspace, add sources, list sources, and validate
  config without indexing content yet.

Tasks:

- [ ] Implement workspace discovery.
- [ ] Implement config model and YAML read/write.
- [ ] Implement `init`.
- [ ] Implement `add`.
- [ ] Implement `sources`.
- [ ] Implement `doctor` with config-only checks.
- [ ] Add JSON output option for `sources` and `doctor`.
- [ ] Document config format.

Tests:

- [ ] `init` creates expected files.
- [ ] `init` refuses to overwrite config unless `--force`.
- [ ] `add` stores normalized source entries.
- [ ] `add` handles explicit source IDs.
- [ ] Config validation reports missing roots and invalid glob fields.
- [ ] Workspace discovery works from nested directories.

Evidence required:

- CLI transcript in PR description.
- Unit tests for config round trip.
- No TreeDB service required for this milestone.

### M2: Source Scanning and Chunking

Outcome:

- The tool can scan configured sources and produce normalized chunks with
  citation metadata.

Tasks:

- [ ] Define source adapter interface.
- [ ] Implement repo adapter.
- [ ] Implement folder adapter.
- [ ] Implement markdown/text loaders.
- [ ] Implement JSONL loader with configurable content field.
- [ ] Implement include/exclude rules.
- [ ] Implement chunk data model.
- [ ] Implement markdown chunker with heading metadata.
- [ ] Implement text chunker.
- [ ] Implement simple code chunker with line ranges.
- [ ] Implement `index --dry-run` to show files/chunks without writing TreeDB.

Tests:

- [ ] Include/exclude behavior.
- [ ] Symlink behavior, with explicit policy.
- [ ] Binary file skipping.
- [ ] Markdown heading line ranges.
- [ ] Text chunk size limits.
- [ ] Code chunk line ranges.
- [ ] JSONL malformed-record handling.
- [ ] Stable chunk IDs for unchanged content.

Evidence required:

- `treedb-project-memory index --dry-run` on fixture sources.
- Output includes file count, chunk count, skipped count, and warnings.

### M3: Embedding and TreeDB Indexing

Outcome:

- The tool can embed chunks and write them into a local TreeDB document index
  through the TreeDB Haystack integration.

Tasks:

- [ ] Add embedding provider abstraction.
- [ ] Implement local sentence-transformers provider.
- [ ] Add optional OpenAI-compatible embedding provider.
- [ ] Integrate `treedb-client` and `treedb-haystack`.
- [ ] Implement TreeDB service config.
- [ ] Implement create/open index flow.
- [ ] Implement chunk upsert.
- [ ] Implement deleted-file cleanup.
- [ ] Persist index state for incremental indexing.
- [ ] Implement `status` with document and chunk counts.

Tests:

- [ ] Embedding config validation.
- [ ] Embedding dimension consistency.
- [ ] Fake TreeDB client upsert tests.
- [ ] Incremental indexing skips unchanged chunks.
- [ ] Deleted source files delete indexed chunks.
- [ ] Real-service integration test gated behind env var.

Evidence required:

- CLI transcript indexing a fixture repo.
- `status` output after indexing.
- Integration evidence with local TreeDB service when available.

### M4: Retrieval, Search, Ask, and Citations

Outcome:

- A user can search and ask questions over indexed sources and receive cited
  results.

Tasks:

- [ ] Implement retrieval mode selection: semantic, keyword, hybrid.
- [ ] Implement `search`.
- [ ] Implement `ask`.
- [ ] Implement citation formatting.
- [ ] Implement retrieval trace model.
- [ ] Add `--explain` output.
- [ ] Add source filtering where supported.
- [ ] Add graceful capability reporting where filters/modes are unsupported.
- [ ] Add optional answer generator provider configuration.

Tests:

- [ ] Search returns stable fixture citations.
- [ ] Search works without answer generator.
- [ ] Ask fails clearly when generator is not configured.
- [ ] Retrieval trace includes query, mode, filters, IDs, and scores.
- [ ] Unsupported mode/filter combinations return explicit errors.
- [ ] Citation rendering handles missing line ranges.

Evidence required:

- Example queries against fixture sources.
- At least one search-only transcript.
- At least one ask transcript with cited answer when generator is configured.

### M5: Local UI and Operational Diagnostics

Outcome:

- A user can run a local UI and inspect memory without using only terminal
  commands.

Tasks:

- [ ] Choose UI stack that fits Python packaging and local execution.
- [ ] Implement source list view.
- [ ] Implement index status view.
- [ ] Implement search view with citations.
- [ ] Implement ask view with cited answers.
- [ ] Implement retrieval trace view.
- [ ] Implement basic error/doctor panel.
- [ ] Add smoke tests for UI startup.

Tests:

- [ ] UI server starts.
- [ ] Source/status page renders.
- [ ] Search form submits against fixture/fake backend.
- [ ] Citation links render safely.
- [ ] No blocking dependency on external network for UI startup.

Evidence required:

- Screenshot or browser smoke output.
- CLI transcript for `treedb-project-memory ui`.

### M6: Documentation, Examples, and Packaging

Outcome:

- Other people can understand, install, configure, and use the tool.

Tasks:

- [ ] Expand README with install, quickstart, and current limitations.
- [ ] Write `docs/concepts.md`.
- [ ] Write `docs/configuration.md`.
- [ ] Write `docs/source-types.md`.
- [ ] Write `docs/metadata-schema.md`.
- [ ] Write `docs/local-service.md`.
- [ ] Add neutral example workspaces.
- [ ] Add development setup docs.
- [ ] Decide whether to publish to PyPI, keep local install only, or provide a
  git install path for the initial release.

Tests:

- [ ] Quickstart commands are exercised in CI or a scripted smoke test.
- [ ] Example configs validate.
- [ ] Docs do not reference private paths as required steps.

Evidence required:

- Fresh-clone install transcript.
- Quickstart transcript from an empty temp workspace.

### M7: Performance, Scale, and Hardening

Outcome:

- The tool has measured behavior on realistic local sources and clear limits.

Tasks:

- [ ] Add ingest benchmark harness.
- [ ] Add retrieval benchmark harness.
- [ ] Measure indexing throughput on small, medium, and large source sets.
- [ ] Measure query latency by retrieval mode.
- [ ] Measure TreeDB storage footprint.
- [ ] Add cancellation/progress handling for long indexing runs.
- [ ] Add better stale-index diagnostics.
- [ ] Add backup/rebuild guidance.

Metrics to collect:

- Files scanned per second.
- Chunks produced per second.
- Embeddings produced per second.
- TreeDB upserts per second.
- End-to-end indexing time.
- Search p50/p95 latency.
- Ask p50/p95 latency excluding and including generator time.
- Local index size on disk.
- Peak memory during indexing.

Evidence required:

- Benchmark command lines.
- Hardware and dataset description.
- Before/after comparisons for optimizations.
- Clear caveats for TreeDB pre-alpha behavior.

## Testing Strategy

### Unit Tests

Required coverage:

- Config validation.
- Workspace discovery.
- Source ID generation.
- Include/exclude matching.
- File scanning.
- Binary/large-file skip policy.
- Chunk ID stability.
- Metadata schema construction.
- Citation formatting.
- Retrieval trace formatting.

### Integration Tests

Required coverage:

- CLI `init -> add -> dry-run index`.
- CLI indexing with fake TreeDB client.
- CLI search with fake retriever.
- Optional real TreeDB document service test behind an explicit environment
  variable.
- Incremental indexing with changed/deleted files.

### UI Tests

Required if UI is implemented:

- UI starts locally.
- Source/status page renders.
- Search flow renders cited results.
- Ask flow handles missing generator configuration clearly.

### Regression Fixtures

Include small fixtures:

- A tiny Python repo.
- A tiny TypeScript repo.
- Markdown docs with nested headings.
- Plain text notes.
- JSONL records.
- Files that should be excluded.
- Binary file that should be skipped.

## Benchmark and Evidence Policy

Every performance-sensitive PR should include:

- Exact command lines.
- Dataset/source description.
- Machine description.
- Before and after results when changing behavior.
- Notes on TreeDB version or local checkout.
- Explicit caveats when results are from small fixtures.

Benchmarks should not block early MVP functionality unless a regression is
obvious and user-facing. Once the tool has a baseline, material regressions in
indexing throughput or query latency should block release PRs.

## Security and Privacy

Default behavior:

- No telemetry.
- No remote upload.
- No source indexing outside configured sources.
- No automatic inclusion of home directory, SSH keys, env files, or secrets.
- Conservative default excludes for `.env`, key files, dependency directories,
  build output, and VCS internals.

Required safeguards:

- Print configured sources before indexing.
- Provide `--dry-run`.
- Support explicit excludes.
- Document how embedding providers handle data.
- Warn when configured embedding or answer providers send content to a remote
  API.
- Avoid logging full sensitive document content in normal command output.

## Configuration Design

Config should be explicit but not noisy.

Required top-level fields:

- `workspace`
- `sources`

Optional top-level fields:

- `retrieval`
- `embedding`
- `answering`
- `treedb`
- `ui`
- `defaults`

Example extended config:

```yaml
workspace: local-memory

defaults:
  exclude:
    - ".git/**"
    - "node_modules/**"
    - "dist/**"
    - ".venv/**"
    - "**/.env"
    - "**/*.pem"

sources:
  app:
    type: repo
    root: ~/dev/my-app
    include:
      - "**/*.py"
      - "**/*.md"
    tags:
      - product
      - backend

retrieval:
  default_mode: hybrid
  top_k: 8

embedding:
  provider: sentence-transformers
  model: all-MiniLM-L6-v2

answering:
  provider: none

treedb:
  service_url: http://127.0.0.1:8080
  index_name: local-memory
```

## CLI Design Details

Command output should be concise, scriptable, and source-backed.

`init`:

- Creates config and state directories.
- Refuses to overwrite unless `--force`.
- Has `--workspace-name`.

`add`:

- Accepts path and optional `--id`.
- Accepts `--type`.
- Accepts repeated `--include` and `--exclude`.
- Validates root exists.

`index`:

- Supports `--source`.
- Supports `--dry-run`.
- Supports `--changed`.
- Supports `--rebuild`.
- Supports `--json`.
- Prints summary counts.

`search`:

- Accepts query text.
- Supports `--mode`.
- Supports `--source`.
- Supports `--top-k`.
- Supports `--json`.
- Supports `--explain`.

`ask`:

- Accepts query text.
- Supports the same retrieval flags as `search`.
- Requires answer generator configuration unless a retrieval-only answer format
  is explicitly selected.

`doctor`:

- Checks config.
- Checks source roots.
- Checks Python dependencies.
- Checks embedding provider availability.
- Checks TreeDB service availability.
- Checks index metadata consistency.

## Public Demo Direction

The public demo should make the tool feel broadly useful:

- Index a small sample app plus a docs folder.
- Ask architecture and onboarding questions.
- Show citations into source files and docs.
- Add a second source and ask a cross-source question.
- Show `--explain` so users trust the retrieval path.

Good demo prompts:

- "Where is request authentication handled?"
- "What are the main setup steps for a new developer?"
- "Which docs describe deployment?"
- "What code paths mention retries?"
- "Summarize the local testing workflow with citations."

Avoid making the primary demo about limitations, benchmark caveats, or one
private repo. Limitations belong in docs and diagnostics, not in the headline
experience.

## PR and Review Policy After Bootstrap

This repo starts with a direct initial spec commit. After bootstrap, use PRs for
implementation work.

Every implementation PR should include:

- Goal.
- User-facing behavior.
- Implementation summary.
- Tests run.
- Evidence, transcripts, or screenshots where relevant.
- Known limitations.

Before requesting AI review:

- PR should be internally coherent.
- Tests should pass locally when feasible.
- Obvious TODOs should be resolved or explicitly scoped out.
- PR body should describe the real behavior, not just file changes.

Before merge:

- Branch should be up to date with latest `main` when CI or branch protection
  exists.
- CI should pass.
- AI review comments should be resolved or deliberately deferred with rationale.
- User-facing docs should be updated for behavior changes.

## Completion Criteria for MVP

The MVP is complete when:

- A fresh clone can install the package locally.
- `treedb-project-memory init` creates a workspace.
- A user can add at least one repo and one docs/folder source.
- `index --dry-run` reports accurate chunking.
- `index` writes chunks into TreeDB through the Haystack integration.
- `status` reports indexed source state.
- `search` returns cited results.
- `ask` either returns a cited generated answer or clearly explains missing
  answer-generator configuration.
- Incremental re-indexing handles unchanged, changed, and deleted files.
- Docs explain setup, config, source types, TreeDB service requirements, and
  limitations.
- Tests cover the critical config, chunking, indexing, and retrieval behavior.

## Open Questions

- Should the first release depend on published TreeDB Python packages, local path
  installs from `snissn/gomap`, or vendored temporary adapters?
- What TreeDB document service lifecycle should the CLI own: connect only, start
  a local subprocess, or support both?
- Which embedding provider should be the default for the first public demo?
- Should answer generation be included in MVP, or should the first milestone
  ship search plus retrieval traces before LLM answering?
- Should the UI be part of MVP or a follow-up once CLI workflows are stable?
- How should workspaces handle moving source roots across machines?

## Deferred Follow-Ups

- Packaged binary or standalone app.
- PyPI release.
- Homebrew formula.
- File watcher for continuous indexing.
- Tree-sitter chunking.
- Rich notebook/document parsing.
- User-defined source adapter plugin API.
- Shared team workspaces.
- Cloud backup/sync.
- Hosted demo.
- Advanced permission model.
- Editor integrations.
- Codex-specific adapter or skill once the generic tool is useful.

## Immediate Next Steps

1. Add repository bootstrap files: `README.md`, `pyproject.toml`, `.gitignore`,
   package skeleton, and tests.
2. Implement M1 workspace config and CLI skeleton.
3. Add fixture sources and `index --dry-run`.
4. Wire TreeDB/Haystack indexing behind a thin adapter.
5. Build cited search before adding any UI polish.
6. Dogfood on multiple local source sets, including but not limited to `gomap`.
