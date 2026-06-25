# Metadata Schema

Indexed chunks carry metadata for retrieval, citation rendering, and incremental
state checks.

## Chunk Metadata

| Field | Meaning |
| --- | --- |
| `workspace_id` | Workspace display name from config. |
| `source_id` | Stable source key from config. |
| `source_type` | Source type such as `repo`, `folder`, or `jsonl`. |
| `source_root` | Configured source root. |
| `path` | Path relative to the source root. |
| `absolute_path` | Absolute path observed during scanning. |
| `chunk_id` | Deterministic chunk/document ID used by the adapter. |
| `chunk_index` | Zero-based chunk index within the source document. |
| `content_hash` | SHA-256 hash of chunk content. |
| `document_hash` | SHA-256 hash of source document content. |
| `chunk_kind` | `markdown_section`, `code`, `json_record`, or `text_block`. |
| `start_line` | First source line included in the chunk. |
| `end_line` | Last source line included in the chunk. |
| `mtime` | Source file mtime when available. |
| `size_bytes` | Source file byte size when available. |
| `language` | Detected language for known code extensions. |
| `title` | Markdown heading or JSONL title when available. |
| `symbol` | Currently mirrors `title` for titled chunks. |
| `jsonl_line` | Source JSONL record line when applicable. |

Non-dry-run indexing also adds:

| Field | Meaning |
| --- | --- |
| `indexed_at` | UTC timestamp for the index operation. |
| `embedding_provider` | Provider used to embed the chunk. |
| `embedding_model` | Embedding model name. |
| `embedding_dimension` | Embedding vector dimension. |

## Citations

Citations are rendered from metadata in this order:

1. `path` with `start_line` and `end_line`, when present;
2. `path`;
3. `source_id`;
4. `chunk_id`;
5. `unknown source`.

Example labels:

```text
guide.md:3-5
notes/meeting.md
issues
```

## Local Index State

`.treedb-project-memory/state/index-state.json` records:

- state version;
- workspace name;
- embedding provider, model, and dimension;
- TreeDB adapter, base URL, index, and similarity;
- per-source file hashes, chunk IDs, chunk counts, size, mtime, and indexed time.

If the state identity does not match current embedding or TreeDB settings,
`index` fails and points to `treedb-project-memory index --rebuild`.
