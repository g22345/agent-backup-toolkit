"""File-count and byte limits for collection."""

from __future__ import annotations

from dataclasses import dataclass

from agent_backup_toolkit.config import SourceLimits
from agent_backup_toolkit.errors import PolicyError


@dataclass(slots=True)
class LimitTracker:
    """Track actual staged bytes and reject the first exceeded bound."""

    limits: SourceLimits
    file_count: int = 0
    total_bytes: int = 0

    def preflight_size(self, size_bytes: int) -> None:
        if size_bytes < 0:
            raise PolicyError("Source reported an invalid file size.")
        if size_bytes > self.limits.max_file_bytes:
            raise PolicyError("A source file exceeds max_file_bytes.")

    def add(self, size_bytes: int) -> None:
        self.preflight_size(size_bytes)
        next_count = self.file_count + 1
        next_total = self.total_bytes + size_bytes
        if next_count > self.limits.max_files:
            raise PolicyError("A source exceeds max_files.")
        if next_total > self.limits.max_total_bytes:
            raise PolicyError("A source exceeds max_total_bytes.")
        self.file_count = next_count
        self.total_bytes = next_total
