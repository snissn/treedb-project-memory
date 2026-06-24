from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKSPACE_DIR = ".treedb-project-memory"
CONFIG_NAME = "config.yaml"
STATE_DIR = "state"

VALID_SOURCE_TYPES = {"repo", "folder", "jsonl", "file"}
DEFAULT_INCLUDE = {
    "repo": ["**/*.py", "**/*.js", "**/*.ts", "**/*.md", "**/*.txt"],
    "folder": ["**/*.md", "**/*.txt"],
    "jsonl": ["*.jsonl"],
    "file": ["*"],
}
DEFAULT_EXCLUDE = {
    "repo": [".git/**", "node_modules/**", "dist/**", ".venv/**"],
    "folder": [],
    "jsonl": [],
    "file": [],
}


class WorkspaceError(Exception):
    """Base error for workspace config operations."""


class ValidationError(WorkspaceError):
    """Raised when config content does not match the workspace schema."""


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def metadata_dir(self) -> Path:
        return self.root / WORKSPACE_DIR

    @property
    def config_path(self) -> Path:
        return self.metadata_dir / CONFIG_NAME

    @property
    def state_dir(self) -> Path:
        return self.metadata_dir / STATE_DIR


@dataclass
class SourceConfig:
    id: str
    type: str
    root: str
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)

    def to_yaml(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "root": self.root,
            "include": list(self.include),
            "exclude": list(self.exclude),
        }

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, **self.to_yaml(), "exists": root_exists(self.root)}


@dataclass
class ProjectConfig:
    workspace: str
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(
        default_factory=lambda: {"default_mode": "hybrid", "top_k": 8}
    )
    embedding: dict[str, Any] = field(
        default_factory=lambda: {
            "provider": "sentence-transformers",
            "model": "all-MiniLM-L6-v2",
        }
    )

    @classmethod
    def default(cls, workspace: str) -> "ProjectConfig":
        return cls(workspace=workspace)

    @classmethod
    def from_yaml(cls, data: Any) -> "ProjectConfig":
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValidationError("config must be a YAML mapping")

        workspace = data.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValidationError("workspace must be a non-empty string")

        sources_data = data.get("sources", {})
        if sources_data is None:
            sources_data = {}
        if not isinstance(sources_data, dict):
            raise ValidationError("sources must be a mapping of source IDs")

        sources: dict[str, SourceConfig] = {}
        for source_id, raw_source in sources_data.items():
            source_id = validate_source_id(source_id)
            if not isinstance(raw_source, dict):
                raise ValidationError(f"sources.{source_id} must be a mapping")
            source_type = raw_source.get("type")
            if source_type not in VALID_SOURCE_TYPES:
                valid = ", ".join(sorted(VALID_SOURCE_TYPES))
                raise ValidationError(
                    f"sources.{source_id}.type must be one of: {valid}"
                )
            root = raw_source.get("root")
            if not isinstance(root, str) or not root.strip():
                raise ValidationError(f"sources.{source_id}.root must be a string")
            include = validate_globs(raw_source.get("include", []), source_id, "include")
            exclude = validate_globs(raw_source.get("exclude", []), source_id, "exclude")
            sources[source_id] = SourceConfig(
                id=source_id,
                type=source_type,
                root=root,
                include=include,
                exclude=exclude,
            )

        retrieval = data.get("retrieval", {})
        embedding = data.get("embedding", {})
        if retrieval is None:
            retrieval = {}
        if embedding is None:
            embedding = {}
        if not isinstance(retrieval, dict):
            raise ValidationError("retrieval must be a mapping")
        if not isinstance(embedding, dict):
            raise ValidationError("embedding must be a mapping")

        return cls(
            workspace=workspace,
            sources=sources,
            retrieval=dict(retrieval),
            embedding=dict(embedding),
        )

    def to_yaml(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "sources": {
                source_id: source.to_yaml()
                for source_id, source in sorted(self.sources.items())
            },
            "retrieval": dict(self.retrieval),
            "embedding": dict(self.embedding),
        }


def discover_workspace(start: Path | None = None) -> Workspace:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        workspace = Workspace(path)
        if workspace.config_path.exists():
            return workspace
    raise WorkspaceError(
        f"no workspace found from {current}; run 'treedb-project-memory init'"
    )


def init_workspace(root: Path, workspace_name: str | None, force: bool) -> Workspace:
    workspace = Workspace(root.resolve())
    if workspace.config_path.exists() and not force:
        raise WorkspaceError(
            f"workspace already exists at {workspace.config_path}; use --force to overwrite"
        )
    workspace.metadata_dir.mkdir(parents=True, exist_ok=True)
    workspace.state_dir.mkdir(parents=True, exist_ok=True)
    config = ProjectConfig.default(workspace_name or workspace.root.name or "local-memory")
    write_config(workspace, config)
    return workspace


def read_config(workspace: Workspace) -> ProjectConfig:
    try:
        data = yaml.safe_load(workspace.config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(f"config not found at {workspace.config_path}") from exc
    except yaml.YAMLError as exc:
        raise ValidationError(f"config YAML is invalid: {exc}") from exc
    return ProjectConfig.from_yaml(data)


def write_config(workspace: Workspace, config: ProjectConfig) -> None:
    workspace.config_path.write_text(
        yaml.safe_dump(config.to_yaml(), sort_keys=False),
        encoding="utf-8",
    )


def add_source(
    workspace: Workspace,
    root: Path,
    source_type: str | None,
    source_id: str | None,
    include: list[str],
    exclude: list[str],
) -> SourceConfig:
    config = read_config(workspace)
    normalized_root = normalize_root(root)
    resolved_type = source_type or infer_source_type(normalized_root)
    if resolved_type not in VALID_SOURCE_TYPES:
        valid = ", ".join(sorted(VALID_SOURCE_TYPES))
        raise ValidationError(f"source type must be one of: {valid}")

    resolved_id = validate_source_id(source_id or source_id_from_root(normalized_root))
    if resolved_id in config.sources:
        raise WorkspaceError(f"source ID '{resolved_id}' already exists")

    source = SourceConfig(
        id=resolved_id,
        type=resolved_type,
        root=normalized_root,
        include=include or list(DEFAULT_INCLUDE[resolved_type]),
        exclude=exclude or list(DEFAULT_EXCLUDE[resolved_type]),
    )
    validate_globs(source.include, source.id, "include")
    validate_globs(source.exclude, source.id, "exclude")
    config.sources[source.id] = source
    write_config(workspace, config)
    return source


def normalize_root(root: Path) -> str:
    expanded = root.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return str(expanded.resolve(strict=False))


def source_id_from_root(root: str) -> str:
    name = Path(root).name or "source"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-._").lower()
    return slug or "source"


def validate_source_id(source_id: Any) -> str:
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValidationError("source ID must be a non-empty string")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source_id):
        raise ValidationError(
            f"source ID '{source_id}' must start with a letter or digit and contain only letters, digits, dots, underscores, or dashes"
        )
    return source_id


def validate_globs(value: Any, source_id: str, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"sources.{source_id}.{field_name} must be a list of strings")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(
                f"sources.{source_id}.{field_name} entries must be non-empty strings"
            )
    return list(value)


def infer_source_type(root: str) -> str:
    path = Path(root)
    if path.suffix == ".jsonl":
        return "jsonl"
    if (path / ".git").exists():
        return "repo"
    if path.exists() and path.is_file():
        return "file"
    return "folder"


def root_exists(root: str) -> bool:
    return Path(root).expanduser().exists()


def doctor_report(workspace: Workspace) -> tuple[dict[str, Any], int]:
    try:
        config = read_config(workspace)
    except ValidationError as exc:
        return (
            {
                "ok": False,
                "workspace_root": str(workspace.root),
                "config_path": str(workspace.config_path),
                "errors": [{"code": "invalid_config", "message": str(exc)}],
                "sources": [],
            },
            1,
        )

    errors: list[dict[str, str]] = []
    sources = []
    for source in config.sources.values():
        exists = root_exists(source.root)
        if not exists:
            errors.append(
                {
                    "code": "missing_root",
                    "source_id": source.id,
                    "message": f"source '{source.id}' root does not exist: {source.root}",
                }
            )
        sources.append({**source.to_json(), "exists": exists})

    report = {
        "ok": not errors,
        "workspace": config.workspace,
        "workspace_root": str(workspace.root),
        "config_path": str(workspace.config_path),
        "state_dir": str(workspace.state_dir),
        "sources": sources,
        "errors": errors,
    }
    return report, 0 if report["ok"] else 1
