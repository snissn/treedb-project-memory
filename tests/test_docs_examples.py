from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from treedb_project_memory.config import ProjectConfig

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIGS = sorted(ROOT.glob("examples/*/.treedb-project-memory/config.yaml"))
DOC_FILES = [
    ROOT / "README.md",
    ROOT / "docs" / "concepts.md",
    ROOT / "docs" / "configuration.md",
    ROOT / "docs" / "source-types.md",
    ROOT / "docs" / "metadata-schema.md",
    ROOT / "docs" / "local-service.md",
    ROOT / "docs" / "ui.md",
    ROOT / "docs" / "development.md",
    ROOT / "docs" / "packaging.md",
    ROOT / "docs" / "limitations.md",
    *sorted((ROOT / "examples").glob("**/*.md")),
]


def test_example_configs_validate() -> None:
    assert EXAMPLE_CONFIGS
    for path in EXAMPLE_CONFIGS:
        config = ProjectConfig.from_yaml(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert config.sources


def test_example_dry_runs_produce_chunks() -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for path in EXAMPLE_CONFIGS:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "treedb_project_memory",
                "index",
                "--dry-run",
                "--json",
            ],
            cwd=path.parents[1],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        payload = yaml.safe_load(result.stdout)
        assert payload["file_count"] > 0
        assert payload["chunk_count"] > 0


def test_docs_do_not_use_private_demo_paths() -> None:
    banned = [
        "/Users/michaelseiler",
        "/Users/snissn",
        "gomap",
    ]
    for path in DOC_FILES:
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{needle!r} found in {path}"
