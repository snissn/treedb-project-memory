# treedb-project-memory

`treedb-project-memory` is a pre-alpha local project-memory tool for user-owned
source material: repositories, folders, docs, notes, exports, and other local
knowledge sources.

The intended product is described in [spec.md](spec.md). This repository is
currently at the source-scanning dry-run stage: it provides package metadata, a
minimal importable Python package, workspace-local YAML config commands, source
scanning, chunking, dry-run index counts, tests, and CI scaffolding for future
implementation work.

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
- Smoke tests and config CLI behavior tests.
- GitHub Actions test workflow.

Not implemented yet:

- Non-dry-run indexing.
- TreeDB or Haystack integration.
- Search, ask, citations, UI, or diagnostics.

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
- `treedb-project-memory doctor --format json` emits parseable config and root
  diagnostics.

Future issues will add TreeDB indexing, retrieval, and richer diagnostics. Until
those issues land, documentation and PRs should not claim those workflows are
implemented.

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
embedding:
  provider: sentence-transformers
  model: all-MiniLM-L6-v2
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

`doctor` currently validates only the workspace config shape and source root
existence. It does not start TreeDB, load Haystack, embed text, or inspect index
state.

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
