#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(mktemp -d /tmp/treedb_project_memory_smoke_XXXXXX)}"
mkdir -p "$ROOT/docs" "$ROOT/src"

printf '# Demo Guide\nTreeDB project memory keeps cited chunks.\n' > "$ROOT/docs/guide.md"
printf 'def remember(topic: str) -> str:\n    return f"remember {topic}"\n' > "$ROOT/src/app.py"

cd "$ROOT"

treedb-project-memory init --workspace smoke
treedb-project-memory add docs --id docs --type folder --include '**/*.md'
treedb-project-memory add src --id code --type folder --include '**/*.py'

python - <<'PY'
from pathlib import Path

import yaml

path = Path(".treedb-project-memory/config.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8"))
data["treedb"]["adapter"] = "memory"
data["retrieval"]["default_mode"] = "keyword"
path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
PY

treedb-project-memory doctor --format json > doctor.json
treedb-project-memory sources --format json > sources.json
treedb-project-memory index --dry-run --json > dry-run.json
treedb-project-memory index --json > index.json
treedb-project-memory status --format json > status.json

python - <<'PY'
import json
from pathlib import Path

dry_run = json.loads(Path("dry-run.json").read_text(encoding="utf-8"))
index = json.loads(Path("index.json").read_text(encoding="utf-8"))
status = json.loads(Path("status.json").read_text(encoding="utf-8"))

assert dry_run["file_count"] == 2, dry_run
assert dry_run["chunk_count"] >= 2, dry_run
assert index["upserted_chunks"] >= 2, index
assert status["state_exists"] is True, status
assert status["indexed_file_count"] == 2, status
PY

echo "quickstart smoke OK: $ROOT"
