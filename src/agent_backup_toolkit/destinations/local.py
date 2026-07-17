"""Immutable local-directory destination with complete read-back."""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Literal

from agent_backup_toolkit.config import LocalDestination
from agent_backup_toolkit.destinations.base import final_filename, prepared_filename
from agent_backup_toolkit.errors import DestinationError, DestinationIntegrityError


def _safe_backup_id(backup_id: str) -> str:
    try:
        parsed = uuid.UUID(backup_id)
    except ValueError as exc:
        raise DestinationError("Destination received an invalid backup identifier.") from exc
    if str(parsed) != backup_id:
        raise DestinationError("Destination received an invalid backup identifier.")
    return backup_id


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written == 0:
            raise DestinationError("A destination write made no progress.")
        view = view[written:]


def _publish_immutable_from_path(source_path: Path, target: Path) -> None:
    """Publish through a same-filesystem temporary inode without replacing a target."""

    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        if source_path.is_symlink() or not source_path.is_file():
            raise DestinationError("Encrypted artifact source is missing or unsafe.")
        with source_path.open("rb") as source, temporary.open("xb") as output:
            os.chmod(temporary, 0o600)
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, target, follow_symlinks=False)
        temporary.unlink()
    except FileExistsError as exc:
        raise DestinationError(
            "Destination object already exists; refusing to replace it."
        ) from exc
    except OSError as exc:
        raise DestinationError("Destination object could not be published safely.") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _publish_immutable_bytes(content: bytes, target: Path) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            _write_all(descriptor, content)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, target, follow_symlinks=False)
        temporary.unlink()
    except FileExistsError as exc:
        raise DestinationError(
            "Destination object already exists; refusing to replace it."
        ) from exc
    except OSError as exc:
        raise DestinationError("Destination object could not be published safely.") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _copy_new(source_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise DestinationError("Read-back output already exists; refusing to replace it.")
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with source_path.open("rb") as source, output_path.open("xb") as output:
            os.chmod(output_path, 0o600)
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    except OSError as exc:
        raise DestinationError("Destination read-back failed.") from exc


class LocalDestinationAdapter:
    destination_type: Literal["local"] = "local"

    def __init__(self, config: LocalDestination) -> None:
        self.root = config.path

    def _backup_root(self, backup_id: str) -> Path:
        return self.root / _safe_backup_id(backup_id)

    def preflight(self) -> None:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = self.root.lstat()
        except OSError as exc:
            raise DestinationError("Local destination could not be prepared.") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise DestinationError("Local destination must be a non-symlink directory.")
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise DestinationError("Local destination is not writable.")

    def publish_prepared(self, backup_id: str, content: bytes) -> None:
        target = self._backup_root(backup_id) / prepared_filename(backup_id)
        _publish_immutable_bytes(content, target)

    def publish_artifact(self, backup_id: str, filename: str, source_path: Path) -> None:
        if filename != f"{_safe_backup_id(backup_id)}.tar.gz.age":
            raise DestinationError("Destination received an invalid artifact filename.")
        _publish_immutable_from_path(source_path, self._backup_root(backup_id) / filename)

    def read_artifact(
        self,
        backup_id: str,
        filename: str,
        output_path: Path,
        *,
        expected_bytes: int,
    ) -> None:
        if filename != f"{_safe_backup_id(backup_id)}.tar.gz.age":
            raise DestinationError("Destination received an invalid artifact filename.")
        source = self._backup_root(backup_id) / filename
        if source.is_symlink() or not source.is_file():
            raise DestinationError("Encrypted artifact is missing or unsafe.")
        try:
            if source.stat().st_size != expected_bytes:
                raise DestinationIntegrityError(
                    "Encrypted artifact size does not match its receipt."
                )
        except OSError as exc:
            raise DestinationError("Encrypted artifact metadata could not be read.") from exc
        _copy_new(source, output_path)

    def publish_final(self, backup_id: str, content: bytes) -> None:
        target = self._backup_root(backup_id) / final_filename(backup_id)
        _publish_immutable_bytes(content, target)

    def read_final(self, backup_id: str) -> bytes:
        target = self._backup_root(backup_id) / final_filename(backup_id)
        if target.is_symlink() or not target.is_file():
            raise DestinationError("Final receipt is missing or unsafe.")
        try:
            if target.stat().st_size > 1024 * 1024:
                raise DestinationError("Final receipt exceeds the safe size limit.")
            return target.read_bytes()
        except OSError as exc:
            raise DestinationError("Final receipt read-back failed.") from exc

    def list_backup_ids(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        results: list[str] = []
        try:
            entries = list(self.root.iterdir())
        except OSError as exc:
            raise DestinationError("Destination backup listing failed.") from exc
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                backup_id = _safe_backup_id(entry.name)
            except DestinationError:
                continue
            receipt = entry / final_filename(backup_id)
            if receipt.is_file() and not receipt.is_symlink():
                results.append(backup_id)
        return tuple(sorted(results))
