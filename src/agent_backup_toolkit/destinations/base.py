"""Shared immutable destination contract."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

DestinationKind = Literal["local", "github", "s3"]


def prepared_filename(backup_id: str) -> str:
    return f"{backup_id}.prepared.json"


def final_filename(backup_id: str) -> str:
    return f"{backup_id}.final.json"


class DestinationAdapter(Protocol):
    """One-way publish followed by complete read-back verification hooks."""

    @property
    def destination_type(self) -> DestinationKind: ...

    def preflight(self) -> None: ...

    def publish_prepared(self, backup_id: str, content: bytes) -> None: ...

    def publish_artifact(self, backup_id: str, filename: str, source_path: Path) -> None: ...

    def read_artifact(self, backup_id: str, filename: str, output_path: Path) -> None: ...

    def publish_final(self, backup_id: str, content: bytes) -> None: ...

    def read_final(self, backup_id: str) -> bytes: ...

    def list_backup_ids(self) -> tuple[str, ...]: ...
