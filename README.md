# treedb-project-memory

`treedb-project-memory` is a pre-alpha local project-memory tool for user-owned
source material: repositories, folders, docs, notes, exports, and other local
knowledge sources.

The intended product is described in [spec.md](spec.md). This repository is
currently at the workspace-config stage: it provides package metadata, a
minimal importable Python package, workspace-local YAML config commands, tests,
and CI scaffolding for future implementation work.

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
- Smoke tests and config CLI behavior tests.
- GitHub Actions test workflow.

Not implemented yet:

- Source scanning, chunking, and indexing.
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
- `treedb-project-memory doctor --format json` emits parseable config and root
  diagnostics.

Future issues will add source scanning, indexing, retrieval, and richer
diagnostics. Until those issues land, documentation and PRs should not claim
those workflows are implemented.

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
retrieval:
  default_mode: hybrid
  top_k: 8
embedding:
  provider: sentence-transformers
  model: all-MiniLM-L6-v2
```

Source entries are keyed by stable user-visible IDs. `add --id <id>` preserves
an explicit ID; otherwise the ID is generated from the source path basename.
Supported source types are `repo`, `folder`, `jsonl`, and `file`. Relative roots
are normalized to absolute paths before writing config.

`doctor` currently validates only the workspace config shape and source root
existence. It does not start TreeDB, load Haystack, scan source files, chunk
content, embed text, or inspect index state.

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
