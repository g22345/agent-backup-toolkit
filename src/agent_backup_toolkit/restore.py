"""Preview-first restore with explicit overwrite and encrypted rollback."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_backup_toolkit.archive import build_archive
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.destinations.base import DestinationAdapter
from agent_backup_toolkit.encryption import decrypt_file, encrypt_file
from agent_backup_toolkit.errors import RestoreError, ToolkitError
from agent_backup_toolkit.manifest import ManifestEntry, build_manifest, sha256_file
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.paths import normalized_mode, open_source_file, safe_relative_path
from agent_backup_toolkit.verify import VerifiedMaterial, materialize_verified_backup

MAX_ROLLBACK_FILE_BYTES = 1024 * 1024 * 1024
MAX_ROLLBACK_TOTAL_BYTES = 10 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RestorePreview:
    additions: tuple[str, ...]
    collisions: tuple[str, ...]
    rejections: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RestoreResult:
    preview: RestorePreview
    applied: bool
    rollback_path: Path | None = None


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    device: int
    inode: int
    size_bytes: int
    sha256: str


def _target_relative(entry: ManifestEntry) -> PurePosixPath:
    return safe_relative_path(PurePosixPath(entry.logical_source) / entry.relative_path)


def _validate_target_root(target_root: Path) -> Path:
    expanded = target_root.expanduser()
    resolved = expanded.resolve(strict=False)
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve(strict=False):
        raise RestoreError("Restore target must not be a filesystem root or the home directory.")
    if expanded.is_symlink() or (expanded.exists() and not expanded.is_dir()):
        raise RestoreError("Restore target must be a non-symlink directory.")
    return expanded


def _unsafe_parent(target_root: Path, relative: PurePosixPath) -> bool:
    current = target_root
    if current.is_symlink() or (current.exists() and not current.is_dir()):
        return True
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
        if not current.is_dir():
            return True
    return False


def preview_material(verified: VerifiedMaterial, target_root: Path) -> RestorePreview:
    target_root = _validate_target_root(target_root)
    additions: list[str] = []
    collisions: list[str] = []
    rejections: list[str] = []
    for entry in verified.manifest.files:
        relative = _target_relative(entry)
        label = relative.as_posix()
        if _unsafe_parent(target_root, relative):
            rejections.append(label)
            continue
        destination = target_root / relative.as_posix()
        if destination.is_symlink():
            rejections.append(label)
        elif not destination.exists():
            additions.append(label)
        elif not destination.is_file():
            rejections.append(label)
        else:
            collisions.append(label)
    return RestorePreview(
        additions=tuple(sorted(additions)),
        collisions=tuple(sorted(collisions)),
        rejections=tuple(sorted(rejections)),
    )


def _copy_open_descriptor(descriptor: int, destination: Path) -> int:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        output = os.open(destination, flags, 0o600)
        total = 0
        try:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output, view)
                    if written == 0:
                        raise RestoreError("Rollback staging write made no progress.")
                    view = view[written:]
            os.fsync(output)
        finally:
            os.close(output)
        return total
    except OSError as exc:
        raise RestoreError("An existing target file could not be staged for rollback.") from exc


def _capture_collision(
    target_path: Path,
    entry: ManifestEntry,
    staging_root: Path,
) -> tuple[CollectedFile, _Fingerprint]:
    try:
        opened = open_source_file(target_path, required=True)
    except ToolkitError as exc:
        raise RestoreError("A collision changed before rollback capture.") from exc
    if opened is None:
        raise RestoreError("A collision disappeared before rollback capture.")
    descriptor, metadata = opened
    if metadata.st_size > MAX_ROLLBACK_FILE_BYTES:
        os.close(descriptor)
        raise RestoreError("A collision exceeds the rollback per-file size limit.")
    staged = staging_root / entry.logical_source / entry.relative_path
    try:
        size = _copy_open_descriptor(descriptor, staged)
    finally:
        os.close(descriptor)
    try:
        digest = sha256_file(staged)
    except ToolkitError as exc:
        raise RestoreError("A rollback file could not be hashed.") from exc
    collected = CollectedFile(
        logical_source=entry.logical_source,
        relative_path=safe_relative_path(entry.relative_path),
        staged_path=staged,
        size_bytes=size,
        mode=normalized_mode(metadata.st_mode),
    )
    fingerprint = _Fingerprint(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size_bytes=size,
        sha256=digest,
    )
    return collected, fingerprint


def _create_verified_rollback(
    config: ToolkitConfig,
    verified: VerifiedMaterial,
    target_root: Path,
    collisions: tuple[str, ...],
    identity_path: Path,
    temporary_root: Path,
) -> tuple[Path, dict[str, _Fingerprint]]:
    entries = {_target_relative(entry).as_posix(): entry for entry in verified.manifest.files}
    rollback_staging = temporary_root / "rollback-staging"
    collected: list[CollectedFile] = []
    fingerprints: dict[str, _Fingerprint] = {}
    for label in collisions:
        entry = entries[label]
        file, fingerprint = _capture_collision(
            target_root / label,
            entry,
            rollback_staging,
        )
        collected.append(file)
        fingerprints[label] = fingerprint

    if sum(file.size_bytes for file in collected) > MAX_ROLLBACK_TOTAL_BYTES:
        raise RestoreError("Collisions exceed the rollback total-size limit.")
    rollback_archive = temporary_root / "rollback.tar.gz"
    try:
        rollback_manifest = build_manifest(collected)
        build_archive(collected, rollback_manifest, rollback_archive)
    except ToolkitError as exc:
        raise RestoreError("Rollback archive creation failed.") from exc
    rollback_dir = config.state_dir / "rollbacks"
    try:
        if config.state_dir.is_symlink():
            raise RestoreError("Encrypted rollback state directory is unsafe.")
        config.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if config.state_dir.is_symlink() or not config.state_dir.is_dir():
            raise RestoreError("Encrypted rollback state directory is unsafe.")
        rollback_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RestoreError("Encrypted rollback directory could not be created.") from exc
    if rollback_dir.is_symlink() or not rollback_dir.is_dir():
        raise RestoreError("Encrypted rollback directory is unsafe.")
    rollback_path = (
        rollback_dir / f"{verified.receipt.backup_id}-{uuid.uuid4()}.rollback.tar.gz.age"
    )
    decrypted_check = temporary_root / "rollback-check.tar.gz"
    try:
        encrypt_file(rollback_archive, rollback_path, config.age_recipient)
        decrypt_file(rollback_path, decrypted_check, identity_path)
        if sha256_file(rollback_archive) != sha256_file(decrypted_check):
            raise RestoreError("Encrypted rollback verification digest does not match.")
    except ToolkitError as exc:
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError("Encrypted rollback creation or verification failed.") from exc
    return rollback_path, fingerprints


def _current_fingerprint(path: Path) -> _Fingerprint:
    try:
        opened = open_source_file(path, required=True)
    except ToolkitError as exc:
        raise RestoreError("A collision changed after rollback creation.") from exc
    if opened is None:
        raise RestoreError("A collision disappeared after rollback creation.")
    descriptor, metadata = opened
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
    except OSError as exc:
        raise RestoreError("A collision could not be rechecked before overwrite.") from exc
    finally:
        os.close(descriptor)
    return _Fingerprint(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size_bytes=total,
        sha256=digest.hexdigest(),
    )


def _ensure_parent_tree(target_root: Path, relative: PurePosixPath) -> Path:
    current = target_root
    try:
        current.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RestoreError("Restore target could not be created safely.") from exc
    if current.is_symlink() or not current.is_dir():
        raise RestoreError("Restore target changed into an unsafe path.")
    for part in relative.parts[:-1]:
        current = current / part
        try:
            current.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise RestoreError("Restore parent directory could not be created safely.") from exc
        if current.is_symlink() or not current.is_dir():
            raise RestoreError("Restore parent changed into an unsafe path.")
    return current / relative.name


def _stage_replacement(source: Path, destination: Path, entry: ManifestEntry) -> Path:
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.restore"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        output = os.open(temporary, flags, 0o600)
        try:
            digest = hashlib.sha256()
            total = 0
            with source.open("rb") as input_handle:
                while True:
                    chunk = input_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output, view)
                        if written == 0:
                            raise RestoreError("Restore staging write made no progress.")
                        view = view[written:]
            os.fsync(output)
            os.fchmod(output, entry.mode)
        finally:
            os.close(output)
    except OSError as exc:
        raise RestoreError("A restored file could not be staged safely.") from exc
    if total != entry.size_bytes or digest.hexdigest() != entry.sha256:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RestoreError("A staged restored file failed its manifest check.")
    return temporary


def _apply_files(
    verified: VerifiedMaterial,
    target_root: Path,
    preview: RestorePreview,
    fingerprints: dict[str, _Fingerprint],
) -> None:
    collisions = set(preview.collisions)
    pending: list[tuple[ManifestEntry, str, Path, Path]] = []
    try:
        for entry in verified.manifest.files:
            relative = _target_relative(entry)
            label = relative.as_posix()
            destination = _ensure_parent_tree(target_root, relative)
            source = verified.extracted_root / "data" / entry.logical_source / entry.relative_path
            temporary = _stage_replacement(source, destination, entry)
            pending.append((entry, label, destination, temporary))

        for _entry, label, destination, _temporary in pending:
            if label in collisions:
                if _current_fingerprint(destination) != fingerprints[label]:
                    raise RestoreError("A collision changed after rollback verification.")

        for entry, label, destination, temporary in pending:
            if label in collisions:
                if _current_fingerprint(destination) != fingerprints[label]:
                    raise RestoreError("A collision changed during restore commit.")
                os.replace(temporary, destination)
            else:
                os.link(temporary, destination, follow_symlinks=False)
                temporary.unlink()

            current = _current_fingerprint(destination)
            if current.size_bytes != entry.size_bytes or current.sha256 != entry.sha256:
                raise RestoreError("A committed restored file failed read-back verification.")
    except FileExistsError as exc:
        raise RestoreError(
            "A new target file appeared during restore; nothing was overwritten."
        ) from exc
    except OSError as exc:
        raise RestoreError("A restored file could not be committed safely.") from exc
    finally:
        for _entry, _label, _destination, temporary in pending:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def restore_backup(
    config: ToolkitConfig,
    backup_id: str,
    identity_path: Path,
    target_root: Path,
    *,
    apply: bool = False,
    overwrite: bool = False,
    destination: DestinationAdapter | None = None,
) -> RestoreResult:
    """Preview by default; write only with explicit apply and overwrite gates."""

    if overwrite and not apply:
        raise RestoreError("--overwrite requires --apply.")
    target_root = _validate_target_root(target_root)
    with materialize_verified_backup(
        config,
        backup_id,
        identity_path,
        destination=destination,
    ) as verified:
        preview = preview_material(verified, target_root)
        if not apply:
            return RestoreResult(preview=preview, applied=False)
        if preview.rejections:
            raise RestoreError("Restore preview contains rejected unsafe paths.")
        if preview.collisions and not overwrite:
            raise RestoreError(
                "Restore preview contains collisions; rerun with --apply --overwrite."
            )

        with tempfile.TemporaryDirectory(prefix="agent-backup-restore-") as temporary:
            temporary_root = Path(temporary)
            temporary_root.chmod(0o700)
            rollback_path: Path | None = None
            fingerprints: dict[str, _Fingerprint] = {}
            if preview.collisions:
                rollback_path, fingerprints = _create_verified_rollback(
                    config,
                    verified,
                    target_root,
                    preview.collisions,
                    identity_path,
                    temporary_root,
                )
            _apply_files(verified, target_root, preview, fingerprints)
        return RestoreResult(
            preview=preview,
            applied=True,
            rollback_path=rollback_path,
        )
