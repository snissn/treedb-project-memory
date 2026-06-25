from __future__ import annotations

import importlib.util
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WORKSPACE_DIR = ".treedb-project-memory"
CONFIG_NAME = "config.yaml"
STATE_DIR = "state"

VALID_SOURCE_TYPES = {"repo", "folder", "markdown", "text", "jsonl", "file"}
VALID_EMBEDDING_PROVIDERS = {
    "deterministic",
    "openai-compatible",
    "sentence-transformers",
}
VALID_TREEDB_SIMILARITIES = {"cosine", "l2", "inner_product"}
VALID_TREEDB_SERVICE_LIFECYCLES = {"external"}
VALID_TREEDB_ADAPTERS = {"haystack", "memory"}

DEFAULT_MAX_FILE_BYTES = 1_048_576
DEFAULT_FOLLOW_SYMLINKS = False
DEFAULT_JSONL_CONTENT_FIELD = "content"
DEFAULT_EMBEDDING_PROVIDER = "deterministic"
DEFAULT_EMBEDDING_MODEL = "deterministic-v1"
DEFAULT_EMBEDDING_DIMENSION = 32
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_EMBEDDING_TIMEOUT_SECONDS = 60.0
DEFAULT_TREEDB_ADAPTER = "haystack"
DEFAULT_TREEDB_BASE_URL = "http://127.0.0.1:7120"
DEFAULT_TREEDB_INDEX = "project_memory"
DEFAULT_TREEDB_SIMILARITY = "cosine"
DEFAULT_TREEDB_TIMEOUT_SECONDS = 30.0
DEFAULT_TREEDB_SERVICE_LIFECYCLE = "external"

DEFAULT_INCLUDE = {
    "repo": ["**/*.py", "**/*.js", "**/*.ts", "**/*.md", "**/*.txt"],
    "folder": ["**/*.md", "**/*.txt"],
    "markdown": ["**/*.md", "**/*.markdown"],
    "text": ["**/*.txt", "**/*.text"],
    "jsonl": ["**/*.jsonl"],
    "file": ["*"],
}
DEFAULT_EXCLUDE = {
    "repo": [".git/**", "node_modules/**", "dist/**", ".venv/**"],
    "folder": [],
    "markdown": [],
    "text": [],
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
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    follow_symlinks: bool = DEFAULT_FOLLOW_SYMLINKS
    content_field: str | None = None

    def to_yaml(self) -> dict[str, Any]:
        payload = {
            "type": self.type,
            "root": self.root,
            "include": list(self.include),
            "exclude": list(self.exclude),
            "max_file_bytes": self.max_file_bytes,
            "follow_symlinks": self.follow_symlinks,
        }
        if self.type == "jsonl":
            payload["content_field"] = self.content_field or DEFAULT_JSONL_CONTENT_FIELD
        return payload

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, **self.to_yaml(), "exists": root_exists(self.root)}


@dataclass
class EmbeddingConfig:
    provider: str = DEFAULT_EMBEDDING_PROVIDER
    model: str = DEFAULT_EMBEDDING_MODEL
    dimension: int = DEFAULT_EMBEDDING_DIMENSION
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_seconds: float | None = None
    device: str | None = None

    def to_yaml(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
            "batch_size": self.batch_size,
        }
        if self.provider == "openai-compatible":
            payload["base_url"] = self.base_url or "https://api.openai.com/v1"
            payload["api_key_env"] = self.api_key_env or "OPENAI_API_KEY"
            payload["timeout_seconds"] = (
                self.timeout_seconds or DEFAULT_EMBEDDING_TIMEOUT_SECONDS
            )
        if self.provider == "sentence-transformers" and self.device:
            payload["device"] = self.device
        return payload

    def to_json(self) -> dict[str, Any]:
        return self.to_yaml()

    def signature(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "dimension": self.dimension,
        }


@dataclass
class TreeDBConfig:
    adapter: str = DEFAULT_TREEDB_ADAPTER
    base_url: str = DEFAULT_TREEDB_BASE_URL
    index: str = DEFAULT_TREEDB_INDEX
    similarity: str = DEFAULT_TREEDB_SIMILARITY
    service_lifecycle: str = DEFAULT_TREEDB_SERVICE_LIFECYCLE
    timeout_seconds: float = DEFAULT_TREEDB_TIMEOUT_SECONDS
    ensure_index: bool = True

    def to_yaml(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "base_url": self.base_url,
            "index": self.index,
            "similarity": self.similarity,
            "service_lifecycle": self.service_lifecycle,
            "timeout_seconds": self.timeout_seconds,
            "ensure_index": self.ensure_index,
        }

    def to_json(self) -> dict[str, Any]:
        return self.to_yaml()


@dataclass
class ProjectConfig:
    workspace: str
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(
        default_factory=lambda: {"default_mode": "hybrid", "top_k": 8}
    )
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    treedb: TreeDBConfig = field(default_factory=TreeDBConfig)

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
            max_file_bytes = validate_max_file_bytes(
                raw_source.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES), source_id
            )
            follow_symlinks = validate_follow_symlinks(
                raw_source.get("follow_symlinks", DEFAULT_FOLLOW_SYMLINKS), source_id
            )
            content_field = validate_content_field(
                raw_source.get("content_field", DEFAULT_JSONL_CONTENT_FIELD),
                source_id,
            )
            sources[source_id] = SourceConfig(
                id=source_id,
                type=source_type,
                root=root,
                include=include,
                exclude=exclude,
                max_file_bytes=max_file_bytes,
                follow_symlinks=follow_symlinks,
                content_field=content_field if source_type == "jsonl" else None,
            )

        retrieval = data.get("retrieval", {})
        if retrieval is None:
            retrieval = {}
        if not isinstance(retrieval, dict):
            raise ValidationError("retrieval must be a mapping")

        return cls(
            workspace=workspace,
            sources=sources,
            retrieval=dict(retrieval),
            embedding=validate_embedding_config(data.get("embedding")),
            treedb=validate_treedb_config(data.get("treedb")),
        )

    def to_yaml(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "sources": {
                source_id: source.to_yaml()
                for source_id, source in sorted(self.sources.items())
            },
            "retrieval": dict(self.retrieval),
            "embedding": self.embedding.to_yaml(),
            "treedb": self.treedb.to_yaml(),
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
    max_file_bytes: int | None = None,
    follow_symlinks: bool = DEFAULT_FOLLOW_SYMLINKS,
    content_field: str | None = None,
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
        max_file_bytes=validate_max_file_bytes(
            max_file_bytes if max_file_bytes is not None else DEFAULT_MAX_FILE_BYTES,
            resolved_id,
        ),
        follow_symlinks=validate_follow_symlinks(follow_symlinks, resolved_id),
        content_field=(
            validate_content_field(
                content_field or DEFAULT_JSONL_CONTENT_FIELD,
                resolved_id,
            )
            if resolved_type == "jsonl"
            else None
        ),
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


def validate_max_file_bytes(value: Any, source_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"sources.{source_id}.max_file_bytes must be a positive integer"
        )
    return value


def validate_follow_symlinks(value: Any, source_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"sources.{source_id}.follow_symlinks must be a boolean")
    return value


def validate_content_field(value: Any, source_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"sources.{source_id}.content_field must be a string")
    return value


def validate_embedding_config(value: Any) -> EmbeddingConfig:
    if value is None:
        return EmbeddingConfig()
    if not isinstance(value, dict):
        raise ValidationError("embedding must be a mapping")
    provider = value.get("provider")
    if provider not in VALID_EMBEDDING_PROVIDERS:
        valid = ", ".join(sorted(VALID_EMBEDDING_PROVIDERS))
        raise ValidationError(f"embedding.provider must be one of: {valid}")
    model = value.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValidationError("embedding.model must be a non-empty string")
    dimension = validate_positive_int(value.get("dimension"), "embedding.dimension")
    batch_size = validate_positive_int(
        value.get("batch_size", DEFAULT_EMBEDDING_BATCH_SIZE),
        "embedding.batch_size",
    )
    base_url = value.get("base_url")
    api_key_env = value.get("api_key_env")
    timeout_seconds = value.get("timeout_seconds")
    device = value.get("device")

    allowed = {"provider", "model", "dimension", "batch_size"}
    if provider == "openai-compatible":
        allowed.update({"base_url", "api_key_env", "timeout_seconds"})
        if base_url is None:
            base_url = "https://api.openai.com/v1"
        if api_key_env is None:
            api_key_env = "OPENAI_API_KEY"
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_EMBEDDING_TIMEOUT_SECONDS
        base_url = validate_url_string(base_url, "embedding.base_url")
        api_key_env = validate_non_empty_string(api_key_env, "embedding.api_key_env")
        timeout_seconds = validate_positive_number(
            timeout_seconds,
            "embedding.timeout_seconds",
        )
    elif provider == "sentence-transformers":
        allowed.add("device")
        if device is not None:
            device = validate_non_empty_string(device, "embedding.device")
    elif base_url is not None or api_key_env is not None or timeout_seconds is not None:
        raise ValidationError(
            "embedding.base_url, embedding.api_key_env, and embedding.timeout_seconds "
            "are only valid for provider openai-compatible"
        )

    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValidationError(f"embedding has unsupported field(s): {', '.join(unknown)}")

    return EmbeddingConfig(
        provider=provider,
        model=model,
        dimension=dimension,
        batch_size=batch_size,
        base_url=base_url,
        api_key_env=api_key_env,
        timeout_seconds=timeout_seconds,
        device=device,
    )


def validate_treedb_config(value: Any) -> TreeDBConfig:
    if value is None:
        return TreeDBConfig()
    if not isinstance(value, dict):
        raise ValidationError("treedb must be a mapping")
    allowed = {
        "adapter",
        "base_url",
        "index",
        "similarity",
        "service_lifecycle",
        "timeout_seconds",
        "ensure_index",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValidationError(f"treedb has unsupported field(s): {', '.join(unknown)}")

    adapter = value.get("adapter", DEFAULT_TREEDB_ADAPTER)
    if adapter not in VALID_TREEDB_ADAPTERS:
        valid = ", ".join(sorted(VALID_TREEDB_ADAPTERS))
        raise ValidationError(f"treedb.adapter must be one of: {valid}")
    base_url = validate_url_string(
        value.get("base_url", DEFAULT_TREEDB_BASE_URL),
        "treedb.base_url",
    )
    index = validate_treedb_index(value.get("index", DEFAULT_TREEDB_INDEX))
    similarity = validate_similarity(
        value.get("similarity", DEFAULT_TREEDB_SIMILARITY)
    )
    service_lifecycle = value.get(
        "service_lifecycle",
        DEFAULT_TREEDB_SERVICE_LIFECYCLE,
    )
    if service_lifecycle not in VALID_TREEDB_SERVICE_LIFECYCLES:
        valid = ", ".join(sorted(VALID_TREEDB_SERVICE_LIFECYCLES))
        raise ValidationError(f"treedb.service_lifecycle must be one of: {valid}")
    timeout_seconds = validate_positive_number(
        value.get("timeout_seconds", DEFAULT_TREEDB_TIMEOUT_SECONDS),
        "treedb.timeout_seconds",
    )
    ensure_index = value.get("ensure_index", True)
    if not isinstance(ensure_index, bool):
        raise ValidationError("treedb.ensure_index must be a boolean")

    return TreeDBConfig(
        adapter=adapter,
        base_url=base_url,
        index=index,
        similarity=similarity,
        service_lifecycle=service_lifecycle,
        timeout_seconds=timeout_seconds,
        ensure_index=ensure_index,
    )


def validate_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def validate_positive_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive number")
    return float(value)


def validate_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a non-empty string")
    return value


def validate_url_string(value: Any, field_name: str) -> str:
    url = validate_non_empty_string(value, field_name)
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValidationError(f"{field_name} must start with http:// or https://")
    return url.rstrip("/")


def validate_treedb_index(value: Any) -> str:
    index = validate_non_empty_string(value, "treedb.index")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", index):
        raise ValidationError(
            "treedb.index must start with a letter or digit and contain only letters, digits, dots, underscores, or dashes"
        )
    return index


def validate_similarity(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("treedb.similarity must be a string")
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "cosine": "cosine",
        "l2": "l2",
        "euclidean": "l2",
        "inner_product": "inner_product",
        "innerproduct": "inner_product",
        "dot_product": "inner_product",
        "dotproduct": "inner_product",
    }
    if normalized not in aliases:
        valid = ", ".join(sorted(VALID_TREEDB_SIMILARITIES))
        raise ValidationError(f"treedb.similarity must be one of: {valid}")
    return aliases[normalized]


def infer_source_type(root: str) -> str:
    path = Path(root)
    if path.suffix == ".jsonl":
        return "jsonl"
    if path.suffix in {".md", ".markdown"}:
        return "markdown"
    if path.suffix in {".txt", ".text"}:
        return "text"
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
                "warnings": [],
                "sources": [],
            },
            1,
        )
    except WorkspaceError as exc:
        return (
            {
                "ok": False,
                "workspace_root": str(workspace.root),
                "config_path": str(workspace.config_path),
                "errors": [{"code": "workspace_error", "message": str(exc)}],
                "warnings": [],
                "sources": [],
            },
            1,
        )

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
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

    embedding = _embedding_doctor(config.embedding)
    treedb = _treedb_doctor(config.treedb)
    warnings.extend(embedding["warnings"])
    warnings.extend(treedb["warnings"])

    report = {
        "ok": not errors,
        "workspace": config.workspace,
        "workspace_root": str(workspace.root),
        "config_path": str(workspace.config_path),
        "state_dir": str(workspace.state_dir),
        "embedding": embedding,
        "treedb": treedb,
        "sources": sources,
        "errors": errors,
        "warnings": warnings,
    }
    return report, 0 if report["ok"] else 1


def _embedding_doctor(config: EmbeddingConfig) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    status = "available"
    if config.provider == "sentence-transformers":
        if importlib.util.find_spec("sentence_transformers") is None:
            status = "missing_dependency"
            warnings.append(
                {
                    "code": "missing_embedding_dependency",
                    "message": (
                        "embedding provider 'sentence-transformers' requires the "
                        "optional sentence-transformers package; install "
                        "'treedb-project-memory[local-embeddings]' or configure "
                        "embedding.provider: deterministic for tests"
                    ),
                }
            )
    elif config.provider == "openai-compatible":
        api_key_env = config.api_key_env or "OPENAI_API_KEY"
        if not os.environ.get(api_key_env):
            status = "missing_api_key"
            warnings.append(
                {
                    "code": "missing_embedding_api_key",
                    "message": (
                        f"embedding provider 'openai-compatible' requires ${api_key_env} "
                        "to be set before indexing"
                    ),
                }
            )
    return {
        "provider": config.provider,
        "model": config.model,
        "dimension": config.dimension,
        "batch_size": config.batch_size,
        "status": status,
        "warnings": warnings,
    }


def _treedb_doctor(config: TreeDBConfig) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    if config.adapter == "memory":
        return {
            **config.to_json(),
            "dependencies": {},
            "health": {
                "ok": True,
                "adapter": "memory",
                "message": "self-contained in-memory adapter selected",
            },
            "warnings": warnings,
        }

    dependency_status: dict[str, bool] = {
        "haystack": _module_available("haystack"),
        "treedb_client": _module_available("treedb_client"),
        "treedb_haystack": _module_available(
            "haystack_integrations.document_stores.treedb"
        ),
    }
    missing = [name for name, present in dependency_status.items() if not present]
    if missing:
        warnings.append(
            {
                "code": "missing_treedb_dependency",
                "message": (
                    "TreeDB/Haystack indexing requires optional packages not found: "
                    + ", ".join(missing)
                    + ". Install upstream treedb-client and treedb-haystack without "
                    "adding private paths to this package."
                ),
            }
        )

    health: dict[str, Any] = {"ok": False}
    try:
        request = urllib.request.Request(
            f"{config.base_url}/v1/health",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(
            request,
            timeout=min(config.timeout_seconds, 1.0),
        ) as response:
            health = {"ok": 200 <= response.status < 300, "status_code": response.status}
    except (OSError, urllib.error.URLError, ValueError) as exc:
        warnings.append(
            {
                "code": "treedb_service_unreachable",
                "message": (
                    f"TreeDB document service was not reachable at {config.base_url}: {exc}"
                ),
            }
        )

    return {
        **config.to_json(),
        "dependencies": dependency_status,
        "health": health,
        "warnings": warnings,
    }


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False
