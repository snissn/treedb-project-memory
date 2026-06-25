# Benchmarks And Scale Evidence

The benchmark harnesses are part of the product CLI so users can repeat the same
local workflows that normal indexing and retrieval use. They are intended for
MVP-scale evidence and regression checks, not for cloud-scale or ANN/high-QPS
claims.

## Fixture Strategy

Generate a deterministic local fixture:

```sh
treedb-project-memory benchmark fixture /tmp/tpm-fixture \
  --files 24 \
  --paragraphs 6 \
  --jsonl-rows 12
```

The command writes Markdown files, one JSONL source, and
`fixture-manifest.json` with file sizes and SHA-256 checksums. Benchmark commands
validate that manifest before measuring so fixture drift is caught early.

## Ingest Benchmark

```sh
treedb-project-memory benchmark ingest \
  --output-dir /tmp/tpm-ingest-bench \
  --files 24 \
  --paragraphs 6 \
  --jsonl-rows 12 \
  --runs 1
```

The ingest harness creates a temporary workspace, configures the deterministic
embedding provider and process-local memory adapter, then measures:

- dry-run scanning and chunking;
- non-dry-run scan, chunk, embed, upsert, and state-write behavior;
- files per second and chunks per second;
- `tracemalloc` peak bytes and process RSS proxy;
- workspace metadata and local state footprint.

Artifacts:

- `/tmp/tpm-ingest-bench/ingest_results.json`
- `/tmp/tpm-ingest-bench/ingest_results.md`
- `/tmp/tpm-ingest-bench/fixture/fixture-manifest.json`

## Retrieval Benchmark

```sh
treedb-project-memory benchmark retrieval \
  --output-dir /tmp/tpm-retrieval-bench \
  --files 24 \
  --paragraphs 6 \
  --jsonl-rows 12 \
  --repetitions 10 \
  --query "TreeDB project memory indexing citations"
```

The retrieval harness indexes the generated fixture, then runs keyword search
through the same retrieval service boundary used by `search`. It reports p50 and
p95 latency, result counts, the last retrieval trace, and local state footprint.

Artifacts:

- `/tmp/tpm-retrieval-bench/retrieval_results.json`
- `/tmp/tpm-retrieval-bench/retrieval_results.md`

## UI Smoke

```sh
treedb-project-memory benchmark ui-smoke --output-dir /tmp/tpm-ui-smoke
```

This starts the dependency-free local UI on an ephemeral loopback port and
measures the static shell and health endpoint response. It is a startup and
responsiveness smoke, not a browser rendering benchmark.

Artifacts:

- `/tmp/tpm-ui-smoke/ui-smoke_results.json`
- `/tmp/tpm-ui-smoke/ui-smoke_results.md`

## Regression Gate

For performance-sensitive PRs, include the exact benchmark commands, hardware
context, fixture shape, artifacts, and before/after table in the PR body. A
material regression in runtime, throughput, latency, allocation proxy, storage
footprint, rebuild overhead, or UI smoke latency should block mergeability until
it is fixed or explicitly accepted with evidence.

For harness-only changes, use an explicit no-optimization baseline table:

| Area | Baseline | Candidate | Assessment |
| --- | --- | --- | --- |
| Ingest harness | Not present | Reports scan/chunk/embed/upsert metrics | Adds measurement, no product optimization claimed |
| Retrieval harness | Not present | Reports keyword p50/p95 and trace | Adds measurement, no product optimization claimed |
| UI smoke | Not present | Reports local startup/static response | Adds smoke coverage, no browser performance claim |

## Current Limits And Caveats

- The default benchmark fixture is intentionally small enough for CI and local
  smoke runs. Increase `--files`, `--paragraphs`, and `--jsonl-rows` for local
  scale experiments.
- The memory adapter is process-local, so benchmark retrieval indexes and
  searches in one CLI process. Persistent TreeDB/Haystack service benchmarks
  should use a real service-specific command transcript and artifact path.
- `tracemalloc` measures Python allocations that it can trace; it is an
  allocation proxy, not total process memory.
- RSS is process-level and platform-dependent.
- There is no ANN, high-QPS, hosted, or multi-user scale claim in this MVP.
- The UI smoke does not validate browser paint time or JavaScript interactivity
  under load.

## Rebuild And Backup Guidance

Before destructive rebuild experiments, copy the workspace metadata directory
and any external TreeDB index or service data managed outside this package:

```sh
cp -a .treedb-project-memory ".treedb-project-memory.backup.$(date +%Y%m%d_%H%M%S)"
```

Use `status` to check for stale local state:

```sh
treedb-project-memory status --format json
```

If files changed or were deleted, refresh the index:

```sh
treedb-project-memory index --progress
```

If the external TreeDB index was rebuilt, deleted, or created with a different
embedding or TreeDB configuration, rebuild local state against that service:

```sh
treedb-project-memory index --rebuild --progress
```

The `status` diagnostics report `stale_index` warnings for changed or deleted
files and `index_state_config_mismatch` when local state was built with a
different embedding or TreeDB configuration.
