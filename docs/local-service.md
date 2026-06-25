# Local TreeDB Service

The default config uses:

```yaml
treedb:
  adapter: haystack
  base_url: http://127.0.0.1:7120
  index: project_memory
  similarity: cosine
  service_lifecycle: external
  timeout_seconds: 30.0
  ensure_index: true
```

`service_lifecycle: external` means the CLI expects a TreeDB document service to
already be running. It does not start, stop, install, or supervise the service.

## Optional Dependencies

The Haystack adapter imports optional upstream packages only when selected:

- `haystack.Document`;
- `haystack.document_stores.types.DuplicatePolicy`;
- `haystack_integrations.document_stores.treedb.TreeDBDocumentStore`;
- TreeDB retriever components for `search`.

Those packages are intentionally not hard dependencies of this project. If they
are missing, `doctor` reports warnings and indexing or retrieval fails clearly.

## Retrieval Modes

With the current Haystack adapter:

- `keyword` uses the TreeDB BM25 retriever when available;
- `semantic` embeds the query and uses the TreeDB embedding retriever when
  available;
- `hybrid` is rejected because this adapter does not implement a combined
  retriever.

The CLI does not silently scan all documents client-side to fake unsupported
retrieval modes or filters.

## Memory Adapter

Use the memory adapter for self-contained smoke runs:

```yaml
treedb:
  adapter: memory
  base_url: http://127.0.0.1:7120
  index: project_memory
  similarity: cosine
  service_lifecycle: external
  timeout_seconds: 30.0
  ensure_index: true
```

It validates embedding dimensions and the same upsert/delete/search interface,
but its documents live only inside the current Python process. A separate CLI
command creates a new empty memory adapter. Use the real Haystack adapter for
persistent indexing and later CLI retrieval.
