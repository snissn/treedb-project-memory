# Limitations

This project is pre-alpha. On-disk state, config schema, public CLI behavior,
and packaging may change without migration guarantees.

Known limitations:

- The CLI does not start or manage a TreeDB service.
- The memory adapter is process-local and does not persist indexed documents
  across separate CLI commands. Benchmark retrieval runs index and search in
  one process for this reason.
- The default config uses the Haystack adapter, which requires optional upstream
  packages and an externally running TreeDB document service for non-dry-run
  indexing.
- The current Haystack adapter rejects `hybrid` retrieval.
- Source filtering is only passed to retrievers that support source metadata
  filters; unsupported combinations fail clearly.
- Benchmark harnesses provide repeatable local smoke evidence, not ANN,
  high-QPS, cloud-scale, or multi-user guarantees.
- Python `tracemalloc` and RSS measurements are allocation and process-memory
  proxies, not full storage-engine memory accounting.
- The local UI is a single-user inspection console, not a hosted or production
  web server.
- There is no hosted service, cloud sync, team workspace, auth system, file
  watcher, editor integration, or connector marketplace.
- There is no public release automation or selected open-source license.

When changing source material, use `treedb-project-memory status` to detect
stale indexed files and `treedb-project-memory index` to refresh. After
rebuilding or replacing an external TreeDB index, use
`treedb-project-memory index --rebuild` so local state and external storage are
made consistent again.
