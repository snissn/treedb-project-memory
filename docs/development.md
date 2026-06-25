# Development Setup

Use Python 3.10 or newer.

```sh
python -m pip install -e ".[dev]"
treedb-project-memory --help
pytest
```

Run the self-contained quickstart smoke:

```sh
scripts/quickstart_smoke.sh
```

Check local Markdown links:

```sh
python scripts/check_markdown_links.py
```

Run benchmark smoke commands:

```sh
treedb-project-memory benchmark ingest --output-dir /tmp/tpm-ingest-bench --files 4 --paragraphs 2 --jsonl-rows 2
treedb-project-memory benchmark retrieval --output-dir /tmp/tpm-retrieval-bench --files 4 --paragraphs 2 --jsonl-rows 2 --repetitions 3
treedb-project-memory benchmark ui-smoke --output-dir /tmp/tpm-ui-smoke
```

For larger local runs, increase the fixture shape and include the generated
`*_results.json`, `*_results.md`, and `fixture-manifest.json` paths in the PR
body. See [benchmarks and scale evidence](benchmarks.md).

## CI

GitHub Actions runs on pull requests and pushes to `main` for Python 3.10,
3.11, and 3.12. CI installs the package in editable mode with dev extras,
checks CLI help, runs the quickstart smoke, checks Markdown links, and runs
tests.

## Test Focus

Current tests cover:

- package bootstrap and CLI help;
- workspace config and source editing;
- source scanning and chunking;
- indexing state and adapter boundaries;
- retrieval, citations, and ask behavior;
- local UI startup, rendering, diagnostics, citations, and trace payloads;
- docs/example config validation.
- generated benchmark fixtures, ingest/retrieval smoke, stale-index diagnostics,
  and indexing progress/cancellation hooks.

Add tests when changing user-visible CLI behavior or config schema. Docs-only
changes should still keep example configs and smoke commands valid.

## Optional Local Checks

For local embedding experiments:

```sh
python -m pip install -e ".[dev,local-embeddings]"
```

For real TreeDB/Haystack indexing, install the upstream TreeDB client and
Haystack integration packages, start the document service externally, and keep
`treedb.adapter: haystack`.
