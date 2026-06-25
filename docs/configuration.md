# Configuration

Workspaces store configuration in `.treedb-project-memory/config.yaml`.

Create a default config:

```sh
treedb-project-memory init --workspace my-memory
```

Validate it:

```sh
treedb-project-memory doctor --format json
```

## Top-Level Shape

```yaml
workspace: my-memory
sources: {}
retrieval:
  default_mode: hybrid
  top_k: 8
answer_generator:
  provider: null
  max_context_chunks: 4
embedding:
  provider: deterministic
  model: deterministic-v1
  dimension: 32
  batch_size: 32
treedb:
  adapter: haystack
  base_url: http://127.0.0.1:7120
  index: project_memory
  similarity: cosine
  service_lifecycle: external
  timeout_seconds: 30.0
  ensure_index: true
```

Unknown fields are rejected in `embedding`, `retrieval`, `answer_generator`, and
`treedb` blocks.

## Sources

Sources are stored under stable IDs:

```yaml
sources:
  docs:
    type: folder
    root: ./docs
    include:
      - "**/*.md"
      - "**/*.txt"
    exclude: []
    max_file_bytes: 1048576
    follow_symlinks: false
  issues:
    type: jsonl
    root: ./exports/issues.jsonl
    include:
      - "**/*.jsonl"
    exclude: []
    max_file_bytes: 1048576
    follow_symlinks: false
    content_field: body
```

Valid source types are `repo`, `folder`, `markdown`, `text`, `jsonl`, and
`file`.

`max_file_bytes` must be a positive integer. `follow_symlinks` must be a
boolean. `content_field` is used only for JSONL sources.

## Embedding

Valid providers:

- `deterministic`: self-contained provider for tests and smoke runs.
- `sentence-transformers`: local model loaded through the optional
  `sentence-transformers` package.
- `openai-compatible`: remote HTTP embeddings with an OpenAI-compatible API.

OpenAI-compatible example:

```yaml
embedding:
  provider: openai-compatible
  model: text-embedding-3-small
  dimension: 1536
  batch_size: 32
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  timeout_seconds: 60.0
```

The CLI reads the API key from the configured environment variable.

## TreeDB

Valid adapters:

- `haystack`: real TreeDB/Haystack boundary, selected by default.
- `memory`: process-local adapter for smoke tests and in-process tests.

`service_lifecycle` currently supports only `external`; the CLI does not start
or stop TreeDB services.

Valid similarity values are `cosine`, `l2`, and `inner_product`.

## Retrieval

```yaml
retrieval:
  default_mode: keyword
  top_k: 8
```

`default_mode` must be one of `keyword`, `semantic`, or `hybrid`. `top_k` must
be positive.

## Answer Generator

The default is disabled:

```yaml
answer_generator:
  provider: null
  max_context_chunks: 4
```

The implemented generator is `extractive`:

```yaml
answer_generator:
  provider: extractive
  max_context_chunks: 4
```

It formats snippets from retrieved chunks with citations. It is not an LLM
provider.
