"""Consistent SQLite snapshots using SQLite's official backup API."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from agent_backup_toolkit.config import SQLiteSource
from agent_backup_toolkit.errors import CollectionError, PolicyError
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.limits import LimitTracker
from agent_backup_toolkit.policy.paths import normalized_mode, open_source_file, safe_relative_path


class SQLiteCollector:
    """Collect one live SQLite database as a transactionally consistent snapshot."""

    def collect(self, source: SQLiteSource, staging_root: Path) -> list[CollectedFile]:
        staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        source_staging = staging_root / source.name
        source_staging.mkdir(mode=0o700, exist_ok=False)
        opened = open_source_file(source.path, required=source.required)
        if opened is None:
            return []
        descriptor, before = opened
        os.close(descriptor)

        tracker = LimitTracker(source.limits)
        tracker.preflight_size(before.st_size)
        relative_path = safe_relative_path(source.path.name)
        if relative_path.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise PolicyError("A SQLite source must use a recognized database extension.")
        destination = source_staging / relative_path.as_posix()
        source_uri = f"file:{quote(str(source.path.resolve(strict=False)))}?mode=ro"

        try:
            with (
                sqlite3.connect(source_uri, uri=True, timeout=10) as source_connection,
                sqlite3.connect(destination) as destination_connection,
            ):
                source_connection.backup(destination_connection)
                result = destination_connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise CollectionError("SQLite snapshot or integrity validation failed.") from exc

        try:
            destination.chmod(0o600)
            after_source = source.path.lstat()
            size = destination.stat().st_size
        except OSError as exc:
            raise CollectionError("SQLite snapshot metadata could not be verified.") from exc
        if before.st_dev != after_source.st_dev or before.st_ino != after_source.st_ino:
            raise PolicyError("The SQLite source changed identity during collection.")
        if result != ("ok",):
            raise CollectionError("SQLite snapshot failed its integrity check.")
        tracker.add(size)
        return [
            CollectedFile(
                logical_source=source.name,
                relative_path=relative_path,
                staged_path=destination,
                size_bytes=size,
                mode=normalized_mode(before.st_mode),
            )
        ]
