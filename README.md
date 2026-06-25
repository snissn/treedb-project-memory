# treedb-project-memory

`treedb-project-memory` is a pre-alpha local project-memory CLI for user-owned
repositories, folders, docs, notes, and exports. It scans configured local
sources, chunks text with citation metadata, embeds changed chunks, and writes
them through a TreeDB/Haystack adapter boundary.

The product direction is described in [spec.md](spec.md). Current behavior is
documented in [docs/](docs/concepts.md) and intentionally avoids private demo
paths or a global project registry.

## Status

Implemented now:

- workspace-local `.treedb-project-memory/config.yaml`;
- `init`, `add`, `sources`, `doctor`, `index`, `status`, `search`, and `ask`
  CLI commands;
- source scanning for repo, folder, markdown, text, file, and JSONL sources;
- deterministic test embeddings, optional local `sentence-transformers`
  embeddings, and optional OpenAI-compatible embeddings;
- TreeDB/Haystack adapter integration through optional upstream packages;
- self-contained `memory` adapter for smoke tests and in-process tests;
- cited search, optional extractive ask, and retrieval traces.

Not implemented yet:

- local UI docs or workflows;
- hosted service, cloud sync, team accounts, or editor integration;
- ANN/high-QPS performance claims;
- public release automation.

This project is pre-alpha. CLI behavior, config schema, storage state, and
packaging may change before a stable release.

## Quickstart

Use Python 3.10 or newer.

```sh
git clone https://github.com/snissn/treedb-project-memory.git
cd treedb-project-memory
python -m pip install -e ".[dev]"
treedb-project-memory --help
```

Create a workspace and add local sources:

```sh
mkdir -p /tmp/tpm-demo/docs
printf '# Demo\nTreeDB project memory keeps cited chunks.\n' > /tmp/tpm-demo/docs/guide.md
cd /tmp/tpm-demo
treedb-project-memory init --workspace demo
treedb-project-memory add docs --id docs --type folder --include '**/*.md'
treedb-project-memory doctor
treedb-project-memory index --dry-run
```

For a self-contained indexing smoke run, switch the generated config to the
process-local memory adapter:

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

Then run:

```sh
treedb-project-memory index
treedb-project-memory status --format json
```

The `memory` adapter is not persistent across separate CLI invocations, so it is
not a replacement for the real TreeDB/Haystack path when you want a later
`search` command to retrieve indexed chunks. For first real search, install the
upstream TreeDB/Haystack integration, run the TreeDB document service, keep
`treedb.adapter: haystack`, and use a supported retrieval mode:

```sh
treedb-project-memory search "cited chunks" --mode keyword --explain
```

`ask` uses the same retrieval path and requires an answer generator:

```yaml
answer_generator:
  provider: extractive
  max_context_chunks: 4
```

```sh
treedb-project-memory ask "What does the guide say?" --mode keyword --explain
```

## Documentation

- [Concepts](docs/concepts.md)
- [Configuration](docs/configuration.md)
- [Source types](docs/source-types.md)
- [Metadata schema](docs/metadata-schema.md)
- [Local TreeDB service](docs/local-service.md)
- [Development setup](docs/development.md)
- [Packaging](docs/packaging.md)
- [Limitations](docs/limitations.md)

Neutral examples live under [examples/](examples/README.md).

## Validation

```sh
pytest
scripts/quickstart_smoke.sh
python scripts/check_markdown_links.py
```

The smoke script exercises a clean temporary workspace with generic sample
content. It validates init/add/doctor/dry-run/index/status behavior without
requiring private repositories or optional TreeDB services.

## Repo Contract

- Work on topic branches; do not push implementation work directly to `main`.
- Keep PRs focused on the linked issue and parent tracker.
- Include tests for behavior changes, or state why tests do not apply.
- Include performance evidence only for performance-sensitive changes.
- Request AI reviews only after the PR body, local checks, and code are mature
  enough for review.
- If CI exists, use latest-head CI when claiming mergeability.

## License

No open-source license has been selected yet. Do not publish or redistribute this
package until the repository owner chooses a license.
