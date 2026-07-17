"""Collector protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, TypeVar

from agent_backup_toolkit.config import DirectorySource, FileSource, SQLiteSource
from agent_backup_toolkit.models import CollectedFile

SourceT = TypeVar("SourceT", FileSource, DirectorySource, SQLiteSource, contravariant=True)


class Collector(Protocol[SourceT]):
    """Collect one explicitly configured source into protected staging."""

    def collect(self, source: SourceT, staging_root: Path) -> list[CollectedFile]: ...
