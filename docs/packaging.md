# Packaging

The package is currently installable from a clone:

```sh
python -m pip install -e .
treedb-project-memory --version
```

For development:

```sh
python -m pip install -e ".[dev]"
```

For optional local sentence-transformers embeddings:

```sh
python -m pip install -e ".[local-embeddings]"
```

## Distribution Status

No PyPI release, Homebrew formula, standalone app, or release automation exists
yet. The repository also has no selected open-source license, so do not
redistribute published artifacts until the owner chooses one.

## Dependency Policy

Core dependencies are intentionally small:

- `PyYAML`;
- `typer`.

Optional TreeDB/Haystack packages are not pinned in this project because the
upstream integration is still evolving. The CLI imports those packages only when
the Haystack adapter is selected.

## Fresh-Clone Install Check

From a clean clone:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
treedb-project-memory --help
pytest
scripts/quickstart_smoke.sh
```

Record the transcript in PR evidence when packaging behavior changes.
