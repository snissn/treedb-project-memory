# treedb-project-memory

`treedb-project-memory` is a pre-alpha local project-memory tool for user-owned
source material: repositories, folders, docs, notes, exports, and other local
knowledge sources.

The intended product is described in [spec.md](spec.md). This repository is
currently at the bootstrap stage: it provides package metadata, a minimal
importable Python package, a help/version-level CLI entry point, tests, and CI
scaffolding for future implementation work.

## Status

This project is pre-alpha. Public APIs, command behavior, configuration files,
storage formats, and packaging details may change without compatibility
guarantees.

Implemented now:

- Python package skeleton: `treedb_project_memory`.
- CLI entry point: `treedb-project-memory`.
- Help and version output.
- Smoke tests for importability and CLI help.
- GitHub Actions test workflow.

Not implemented yet:

- Workspace config commands such as `init` and `add`.
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

The bootstrap CLI only guarantees:

- `treedb-project-memory --help` exits successfully and describes the current
  scaffold.
- `treedb-project-memory --version` prints the installed package version.

Future issues will add workspace, source, indexing, retrieval, and diagnostics
commands. Until those issues land, documentation and PRs should not claim those
workflows are implemented.

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
