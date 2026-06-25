# Concepts

`treedb-project-memory` is organized around one local workspace. A workspace is
any directory that contains `.treedb-project-memory/config.yaml`.

## Workspace

The workspace config stores:

- a display name;
- source entries keyed by stable source IDs;
- embedding provider settings;
- TreeDB adapter settings;
- retrieval defaults;
- optional answer-generator settings.

Commands discover the workspace by walking up from the current directory until
they find `.treedb-project-memory/config.yaml`.

## Sources

A source is a repo, folder, single file, markdown/text collection, or JSONL
export. `treedb-project-memory add` records source configuration only. It does
not read file content or write index state.

Source roots may be edited manually, but the CLI writes normalized absolute
paths when adding a source. Relative roots are currently resolved from the
process current working directory, not automatically from the discovered
workspace root. The checked-in examples use relative roots so they can be
copied and inspected without depending on one machine; run those example
commands from the example workspace root.

## Documents And Chunks

Scanning turns readable UTF-8 source files into source documents. Chunking then
turns documents into citation-ready chunks:

- markdown is split by headings and size limits;
- code is split by line and size limits;
- plain text is split by paragraph and size limits;
- JSONL creates one source document per valid record.

Each chunk receives deterministic IDs and metadata for source ID, path, line
range, content hash, document hash, and language when detected.

## Index State

Non-dry-run `index` writes `.treedb-project-memory/state/index-state.json`. That
file records which source files were indexed, their document hashes, chunk IDs,
embedding identity, and TreeDB adapter identity.

The state file is local bookkeeping. The chunk content and embeddings are stored
through the selected adapter. If embedding or TreeDB settings change, run:

```sh
treedb-project-memory index --rebuild
```

after rebuilding the backing TreeDB index.

## Retrieval And Answers

`search` retrieves chunks and renders citations. It does not require an answer
generator.

`ask` runs retrieval first, then calls the configured answer generator. The
default config has no answer generator, so `ask` fails clearly until one is
configured.

Retrieval modes are:

- `keyword`;
- `semantic`;
- `hybrid`.

The selected adapter must support the selected mode. The current Haystack
adapter supports `keyword` and `semantic`; it rejects `hybrid` instead of
silently emulating it client-side.
