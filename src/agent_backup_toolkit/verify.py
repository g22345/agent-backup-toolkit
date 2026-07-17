"""Remote receipt, artifact, archive, and manifest verification."""

from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agent_backup_toolkit.archive import validate_archive_members
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.destinations.base import DestinationAdapter
from agent_backup_toolkit.errors import CryptoError, DestinationIntegrityError, VerifyError
from agent_backup_toolkit.manifest import (
    Manifest,
    canonical_manifest_bytes,
    sha256_file,
    verify_manifest_files,
)
from agent_backup_toolkit.orchestrator import destination_from_config
from agent_backup_toolkit.receipts import FinalReceipt, parse_receipt

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class VerifiedBackupSummary:
    backup_id: str
    file_count: int
    total_bytes: int
    source_names: tuple[str, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class VerifiedMaterial:
    receipt: FinalReceipt
    manifest: Manifest
    extracted_root: Path


def _write_member(archive: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise VerifyError("An archive member could not be read.")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
        try:
            remaining = member.size
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise VerifyError("An archive member ended before its declared size.")
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    if written == 0:
                        raise VerifyError("Archive extraction made no progress.")
                    view = view[written:]
                remaining -= len(chunk)
            if source.read(1):
                raise VerifyError("An archive member exceeds its declared size.")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise VerifyError("A verified archive member could not be staged safely.") from exc
    finally:
        source.close()


def _extract_and_verify(
    archive_path: Path,
    extracted_root: Path,
    receipt: FinalReceipt,
) -> Manifest:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = validate_archive_members(archive)
            manifest_members = [member for member in members if member.name == "manifest.json"]
            if len(manifest_members) != 1 or manifest_members[0].size > MAX_MANIFEST_BYTES:
                raise VerifyError("The archive manifest is missing, duplicated, or too large.")
            data_bytes = sum(member.size for member in members if member.name != "manifest.json")
            if data_bytes != receipt.total_bytes:
                raise VerifyError("Archive data size does not match its final receipt.")
            for member in members:
                _write_member(archive, member, extracted_root / member.name)
    except VerifyError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise VerifyError("Encrypted backup archive could not be read.") from exc

    manifest_path = extracted_root / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = Manifest.model_validate_json(manifest_bytes)
    except (OSError, ValidationError, ValueError) as exc:
        raise VerifyError("Backup manifest validation failed.") from exc
    if hashlib.sha256(manifest_bytes).hexdigest() != receipt.manifest_sha256:
        raise VerifyError("Backup manifest digest does not match its final receipt.")
    if canonical_manifest_bytes(manifest) != manifest_bytes:
        raise VerifyError("Backup manifest is not in canonical form.")
    if len(manifest.files) != receipt.file_count:
        raise VerifyError("Backup manifest file count does not match its final receipt.")
    if sum(entry.size_bytes for entry in manifest.files) != receipt.total_bytes:
        raise VerifyError("Backup manifest byte count does not match its final receipt.")
    manifest_sources = tuple(sorted({entry.logical_source for entry in manifest.files}))
    if manifest_sources != receipt.source_names:
        raise VerifyError("Backup manifest sources do not match its final receipt.")
    verify_manifest_files(manifest, extracted_root)
    return manifest


@contextmanager
def materialize_verified_backup(
    config: ToolkitConfig,
    backup_id: str,
    identity_path: Path,
    *,
    destination: DestinationAdapter | None = None,
) -> Iterator[VerifiedMaterial]:
    """Yield verified plaintext only inside a protected temporary directory."""

    from agent_backup_toolkit.encryption import decrypt_file

    adapter = destination or destination_from_config(config)
    adapter.preflight()
    receipt = parse_receipt(adapter.read_final(backup_id))
    if not isinstance(receipt, FinalReceipt):
        raise VerifyError("Selected backup does not have a final success receipt.")
    if receipt.backup_id != backup_id:
        raise VerifyError("Final receipt backup identifier does not match the selection.")
    if receipt.destination_type != adapter.destination_type:
        raise VerifyError("Final receipt destination type does not match.")
    if receipt.artifact_bytes > MAX_ARTIFACT_BYTES:
        raise VerifyError("Encrypted artifact exceeds the verification size limit.")

    with tempfile.TemporaryDirectory(prefix="agent-backup-verify-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        encrypted = root / receipt.artifact_filename
        try:
            adapter.read_artifact(
                backup_id,
                receipt.artifact_filename,
                encrypted,
                expected_bytes=receipt.artifact_bytes,
            )
        except DestinationIntegrityError as exc:
            raise VerifyError("Encrypted artifact size does not match its final receipt.") from exc
        try:
            digest = sha256_file(encrypted)
        except CryptoError as exc:
            raise VerifyError("Encrypted artifact could not be hashed.") from exc
        if digest != receipt.artifact_sha256:
            raise VerifyError("Encrypted artifact digest does not match its final receipt.")

        archive_path = root / f"{backup_id}.tar.gz"
        decrypt_file(encrypted, archive_path, identity_path)
        extracted_root = root / "extracted"
        extracted_root.mkdir(mode=0o700)
        manifest = _extract_and_verify(archive_path, extracted_root, receipt)
        yield VerifiedMaterial(receipt=receipt, manifest=manifest, extracted_root=extracted_root)


def verify_backup(
    config: ToolkitConfig,
    backup_id: str,
    identity_path: Path,
    *,
    destination: DestinationAdapter | None = None,
) -> VerifiedBackupSummary:
    with materialize_verified_backup(
        config,
        backup_id,
        identity_path,
        destination=destination,
    ) as verified:
        return VerifiedBackupSummary(
            backup_id=verified.receipt.backup_id,
            file_count=verified.receipt.file_count,
            total_bytes=verified.receipt.total_bytes,
            source_names=verified.receipt.source_names,
            manifest_sha256=verified.receipt.manifest_sha256,
        )
