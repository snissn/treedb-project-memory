# Limitations

This project is pre-alpha. On-disk state, config schema, public CLI behavior,
and packaging may change without migration guarantees.

Known limitations:

- The CLI does not start or manage a TreeDB service.
- The memory adapter is process-local and does not persist indexed documents
  across separate CLI commands.
- The default config uses the Haystack adapter, which requires optional upstream
  packages and an externally running TreeDB document service for non-dry-run
  indexing.
- The current Haystack adapter rejects `hybrid` retrieval.
- Source filtering is only passed to retrievers that support source metadata
  filters; unsupported combinations fail clearly.
- There are no ANN/high-QPS claims or scale guarantees yet.
- There is no local UI documentation or workflow in this docs set.
- There is no hosted service, cloud sync, team workspace, auth system, file
  watcher, editor integration, or connector marketplace.
- There is no public release automation or selected open-source license.
