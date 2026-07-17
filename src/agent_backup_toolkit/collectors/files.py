"""Regular-file and directory collectors using no-follow descriptors."""

from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path, PurePosixPath

from agent_backup_toolkit.config import DirectorySource, FileSource
from agent_backup_toolkit.errors import CollectionError, PolicyError
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.limits import LimitTracker
from agent_backup_toolkit.policy.paths import (
    ensure_eligible_text,
    normalized_mode,
    open_child,
    open_source_directory,
    open_source_file,
    safe_relative_path,
)


def _matches(value: PurePosixPath, patterns: list[str], *, directory: bool = False) -> bool:
    candidate = value.as_posix()
    candidates = [candidate]
    if directory:
        candidates.append(f"{candidate}/__entry__")
    for pattern in patterns:
        variants = [pattern]
        if pattern.startswith("**/"):
            variants.append(pattern[3:])
        if any(fnmatch.fnmatchcase(item, variant) for item in candidates for variant in variants):
            return True
    return False


def _copy_descriptor(
    descriptor: int,
    destination: Path,
    *,
    max_file_bytes: int,
) -> int:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        output = os.open(destination, flags, 0o600)
    except OSError as exc:
        raise CollectionError("A protected staging file could not be created.") from exc

    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_file_bytes:
                raise PolicyError("A source file exceeds max_file_bytes while being read.")
            view = memoryview(chunk)
            while view:
                written = os.write(output, view)
                if written == 0:
                    raise CollectionError("A protected staging write made no progress.")
                view = view[written:]
        os.fsync(output)
    except OSError as exc:
        raise CollectionError("A source file could not be staged completely.") from exc
    finally:
        os.close(output)
    return total


class FileCollector:
    """Collect regular text files and bounded directory trees."""

    def collect(
        self,
        source: FileSource | DirectorySource,
        staging_root: Path,
    ) -> list[CollectedFile]:
        staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        source_staging = staging_root / source.name
        source_staging.mkdir(mode=0o700, exist_ok=False)
        tracker = LimitTracker(source.limits)
        if isinstance(source, FileSource):
            return self._collect_file(source, source_staging, tracker)
        return self._collect_directory(source, source_staging, tracker)

    def _collect_file(
        self,
        source: FileSource,
        source_staging: Path,
        tracker: LimitTracker,
    ) -> list[CollectedFile]:
        opened = open_source_file(source.path, required=source.required)
        if opened is None:
            return []
        descriptor, metadata = opened
        relative_path = safe_relative_path(source.path.name)
        destination = source_staging / relative_path.as_posix()
        try:
            tracker.preflight_size(metadata.st_size)
            size = _copy_descriptor(
                descriptor,
                destination,
                max_file_bytes=source.limits.max_file_bytes,
            )
        finally:
            os.close(descriptor)
        tracker.add(size)
        ensure_eligible_text(relative_path, destination)
        return [
            CollectedFile(
                logical_source=source.name,
                relative_path=relative_path,
                staged_path=destination,
                size_bytes=size,
                mode=normalized_mode(metadata.st_mode),
            )
        ]

    def _collect_directory(
        self,
        source: DirectorySource,
        source_staging: Path,
        tracker: LimitTracker,
    ) -> list[CollectedFile]:
        opened = open_source_directory(source.path, required=source.required)
        if opened is None:
            return []
        root_descriptor, _ = opened
        collected: list[CollectedFile] = []
        try:
            self._walk_directory(
                descriptor=root_descriptor,
                relative_directory=PurePosixPath(),
                source=source,
                source_staging=source_staging,
                tracker=tracker,
                collected=collected,
            )
        finally:
            os.close(root_descriptor)
        collected.sort(key=lambda item: item.relative_path.as_posix())
        return collected

    def _walk_directory(
        self,
        *,
        descriptor: int,
        relative_directory: PurePosixPath,
        source: DirectorySource,
        source_staging: Path,
        tracker: LimitTracker,
        collected: list[CollectedFile],
    ) -> None:
        try:
            entries = list(os.scandir(descriptor))
        except OSError as exc:
            raise CollectionError("A source directory could not be enumerated safely.") from exc
        entries.sort(key=lambda item: item.name)

        for entry in entries:
            relative_path = safe_relative_path(relative_directory / entry.name)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise CollectionError("A source entry could not be inspected safely.") from exc

            if stat.S_ISDIR(metadata.st_mode):
                if _matches(relative_path, source.exclude, directory=True):
                    continue
                child_descriptor, _ = open_child(
                    descriptor,
                    entry.name,
                    metadata,
                    directory=True,
                )
                try:
                    self._walk_directory(
                        descriptor=child_descriptor,
                        relative_directory=relative_path,
                        source=source,
                        source_staging=source_staging,
                        tracker=tracker,
                        collected=collected,
                    )
                finally:
                    os.close(child_descriptor)
                continue

            if _matches(relative_path, source.exclude):
                continue
            if not _matches(relative_path, source.include):
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise PolicyError("Symbolic links are not accepted inside a source.")
            if not stat.S_ISREG(metadata.st_mode):
                raise PolicyError("An allowlisted source contains a special file.")

            tracker.preflight_size(metadata.st_size)
            file_descriptor, opened_metadata = open_child(
                descriptor,
                entry.name,
                metadata,
                directory=False,
            )
            destination = source_staging / relative_path.as_posix()
            try:
                size = _copy_descriptor(
                    file_descriptor,
                    destination,
                    max_file_bytes=source.limits.max_file_bytes,
                )
            finally:
                os.close(file_descriptor)
            tracker.add(size)
            ensure_eligible_text(relative_path, destination)
            collected.append(
                CollectedFile(
                    logical_source=source.name,
                    relative_path=relative_path,
                    staged_path=destination,
                    size_bytes=size,
                    mode=normalized_mode(opened_metadata.st_mode),
                )
            )
