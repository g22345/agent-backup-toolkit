"""Small immutable models shared by collection and policy layers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class CollectedFile:
    """One safely staged file and the metadata needed for a manifest."""

    logical_source: str
    relative_path: PurePosixPath
    staged_path: Path
    size_bytes: int
    mode: int

    def __post_init__(self) -> None:
        if self.relative_path.is_absolute() or ".." in self.relative_path.parts:
            raise ValueError("relative_path must stay within its logical source")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """A redacted secret-scan result that never stores the matched value."""

    logical_source: str
    relative_file: str
    rule_id: str
    line_number: int | None = None
