# Source Types

Use `treedb-project-memory add <root>` to add sources. The CLI infers a source
type from the root when `--type` is omitted.

```sh
treedb-project-memory add docs --id docs --type folder --include '**/*.md'
```

## Supported Types

| Type | Intended input | Default include | Default exclude |
| --- | --- | --- | --- |
| `repo` | Local source repository | `**/*.py`, `**/*.js`, `**/*.ts`, `**/*.md`, `**/*.txt` | `.git/**`, `node_modules/**`, `dist/**`, `.venv/**` |
| `folder` | General docs or notes folder | `**/*.md`, `**/*.txt` | none |
| `markdown` | Markdown file or folder | `**/*.md`, `**/*.markdown` | none |
| `text` | Text file or folder | `**/*.txt`, `**/*.text` | none |
| `jsonl` | JSONL export file or folder | `**/*.jsonl` | none |
| `file` | Single readable file | `*` | none |

`repo` sources always receive `.git/**` in their default excludes.

## Include And Exclude Rules

Include patterns select candidate files. Exclude patterns override includes.

```sh
treedb-project-memory add . \
  --id app \
  --type repo \
  --include '**/*.py' \
  --include '**/*.md' \
  --exclude '.venv/**'
```

Symlinks are skipped by default and reported as warnings. Use
`--follow-symlinks` only for sources where following links is intentional.

Files larger than `max_file_bytes` are skipped with warnings. Binary and
non-UTF-8 files are also skipped.

## JSONL

JSONL sources use a top-level string field as content:

```sh
treedb-project-memory add exports/issues.jsonl \
  --id issues \
  --type jsonl \
  --content-field body
```

Blank lines are ignored. Malformed JSON records, non-object records, and records
missing a non-empty string content field are skipped with warnings.

JSONL metadata preserves fields such as `title` when available, plus a
`jsonl_line` value for citation/debugging.
