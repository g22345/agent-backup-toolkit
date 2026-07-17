"""Schema-versioned, fail-closed configuration handling."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

import yaml
from platformdirs import user_config_path, user_state_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agent_backup_toolkit.errors import ConfigError

LOGICAL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
AGE_RECIPIENT_PATTERN = re.compile(r"^age1[023456789acdefghjklmnpqrstuvwxyz]{20,}$")
GITHUB_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class StrictModel(BaseModel):
    """Reject unknown fields throughout the public configuration schema."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _expanded_path(value: object) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError("path must be a string")
    raw = os.path.expandvars(os.fspath(value))
    if not raw.strip():
        raise ValueError("path must not be empty")
    return Path(raw).expanduser()


def _reject_broad_path(path: Path, *, label: str) -> Path:
    resolved = path.resolve(strict=False)
    home = Path.home().resolve(strict=False)
    if resolved == Path(resolved.anchor) or resolved == home:
        raise ValueError(f"{label} must not be a filesystem root or the home directory")
    return path


class SourceLimits(StrictModel):
    """Bound collection work before any source bytes are staged."""

    max_files: int = Field(default=10_000, ge=1, le=1_000_000)
    max_file_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    max_total_bytes: int = Field(default=1024 * 1024 * 1024, ge=1)

    @model_validator(mode="after")
    def total_can_hold_one_file(self) -> SourceLimits:
        if self.max_total_bytes < self.max_file_bytes:
            raise ValueError("max_total_bytes must be at least max_file_bytes")
        return self


class SourceBase(StrictModel):
    name: str
    path: Path
    required: bool = True
    limits: SourceLimits = Field(default_factory=SourceLimits)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not LOGICAL_NAME_PATTERN.fullmatch(value):
            raise ValueError("name must use lowercase letters, numbers, hyphens, or underscores")
        return value

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> Path:
        return _expanded_path(value)

    @field_validator("path")
    @classmethod
    def reject_broad_source(cls, value: Path) -> Path:
        return _reject_broad_path(value, label="source path")


class FileSource(SourceBase):
    type: Literal["file"]


class DirectorySource(SourceBase):
    type: Literal["directory"]
    include: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude: list[str] = Field(default_factory=list)

    @field_validator("include")
    @classmethod
    def require_include_patterns(cls, value: list[str]) -> list[str]:
        if not value or any(not pattern.strip() for pattern in value):
            raise ValueError("include must contain at least one non-empty pattern")
        return value

    @field_validator("exclude")
    @classmethod
    def reject_empty_exclude_patterns(cls, value: list[str]) -> list[str]:
        if any(not pattern.strip() for pattern in value):
            raise ValueError("exclude patterns must not be empty")
        return value


class SQLiteSource(SourceBase):
    type: Literal["sqlite"]


SourceConfig: TypeAlias = Annotated[
    FileSource | DirectorySource | SQLiteSource,
    Field(discriminator="type"),
]


class DestinationBase(StrictModel):
    type: str


class LocalDestination(DestinationBase):
    type: Literal["local"]
    path: Path

    @field_validator("path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> Path:
        return _expanded_path(value)

    @field_validator("path")
    @classmethod
    def reject_broad_destination(cls, value: Path) -> Path:
        return _reject_broad_path(value, label="destination path")


class GitHubDestination(DestinationBase):
    type: Literal["github"]
    repository: str
    tag_prefix: str = "agent-backup"

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if not GITHUB_REPOSITORY_PATTERN.fullmatch(value):
            raise ValueError("repository must use the owner/name form")
        return value

    @field_validator("tag_prefix")
    @classmethod
    def validate_tag_prefix(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
            raise ValueError("tag_prefix contains unsupported characters")
        return value


class S3Destination(DestinationBase):
    type: Literal["s3"]
    bucket: str
    prefix: str = "agent-backups"
    region: str | None = None
    endpoint_url: str | None = None

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,62}", value):
            raise ValueError("bucket name is invalid")
        return value

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        cleaned = value.strip("/")
        if not cleaned or ".." in cleaned.split("/"):
            raise ValueError("prefix must be a safe non-empty object-key prefix")
        return cleaned


DestinationConfig: TypeAlias = Annotated[
    LocalDestination | GitHubDestination | S3Destination,
    Field(discriminator="type"),
]


class ToolkitConfig(StrictModel):
    schema_version: Literal[1]
    age_recipient: str
    state_dir: Path = Field(default_factory=lambda: user_state_path("agent-backup-toolkit"))
    sources: list[SourceConfig] = Field(min_length=1)
    destination: DestinationConfig

    @field_validator("age_recipient")
    @classmethod
    def validate_age_recipient(cls, value: str) -> str:
        if not AGE_RECIPIENT_PATTERN.fullmatch(value):
            raise ValueError("age_recipient must be a valid age public recipient")
        return value

    @field_validator("state_dir", mode="before")
    @classmethod
    def expand_state_dir(cls, value: object) -> Path:
        return _expanded_path(value)

    @field_validator("state_dir")
    @classmethod
    def reject_broad_state_dir(cls, value: Path) -> Path:
        return _reject_broad_path(value, label="state_dir")

    @model_validator(mode="after")
    def source_names_are_unique(self) -> ToolkitConfig:
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("source names must be unique")
        return self


def default_config_path() -> Path:
    """Return the platform-appropriate user configuration path."""

    return user_config_path("agent-backup-toolkit") / "config.yaml"


def load_config(path: Path) -> ToolkitConfig:
    """Load a YAML configuration without reflecting its contents in failures."""

    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError("Configuration file not found. Run 'agent-backup init' first.") from exc
    except OSError as exc:
        raise ConfigError("Configuration file could not be read safely.") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError("Configuration YAML is invalid.") from exc
    if not isinstance(data, dict):
        raise ConfigError("Configuration must contain a YAML mapping.")

    try:
        return ToolkitConfig.model_validate(data)
    except ValidationError as exc:
        locations = sorted({".".join(str(part) for part in item["loc"]) for item in exc.errors()})
        summary = ", ".join(locations[:5])
        if len(locations) > 5:
            summary += ", ..."
        raise ConfigError(f"Configuration validation failed at: {summary}.") from exc


STARTER_CONFIG = """\
# agent-backup-toolkit v0.1 starter configuration
# Replace AGE_RECIPIENT_HERE with your public age recipient before running doctor.
schema_version: 1
age_recipient: AGE_RECIPIENT_HERE
state_dir: ~/.local/state/agent-backup-toolkit

sources:
  - type: directory
    name: codex-skills
    path: ~/.codex/skills
    include:
      - "**/*.md"
      - "**/*.yaml"
      - "**/*.yml"
    exclude:
      - "**/.git/**"
      - "**/__pycache__/**"
    limits:
      max_files: 10000
      max_file_bytes: 10485760
      max_total_bytes: 536870912

destination:
  type: local
  path: ~/agent-backups
"""


def write_starter_config(path: Path) -> None:
    """Create a starter config atomically and never replace an existing file."""

    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ConfigError("Configuration already exists; refusing to overwrite it.") from exc
    except OSError as exc:
        raise ConfigError("Configuration could not be created safely.") from exc

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(STARTER_CONFIG)
    except OSError as exc:
        raise ConfigError("Configuration could not be written completely.") from exc
