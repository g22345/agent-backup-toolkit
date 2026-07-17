"""Fail-closed backup orchestration across collection, crypto, and destinations."""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from agent_backup_toolkit.archive import build_archive
from agent_backup_toolkit.collectors.files import FileCollector
from agent_backup_toolkit.collectors.sqlite import SQLiteCollector
from agent_backup_toolkit.config import (
    DirectorySource,
    FileSource,
    GitHubDestination,
    LocalDestination,
    S3Destination,
    SQLiteSource,
    ToolkitConfig,
)
from agent_backup_toolkit.destinations.base import DestinationAdapter
from agent_backup_toolkit.destinations.github import GitHubDestinationAdapter
from agent_backup_toolkit.destinations.local import LocalDestinationAdapter
from agent_backup_toolkit.destinations.s3 import S3DestinationAdapter
from agent_backup_toolkit.encryption import encrypt_file
from agent_backup_toolkit.errors import (
    CryptoError,
    DestinationError,
    ExitCode,
    PolicyError,
    ToolkitError,
    VerifyError,
)
from agent_backup_toolkit.manifest import build_manifest, manifest_sha256, sha256_file
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.secrets import enforce_no_secrets
from agent_backup_toolkit.receipts import (
    FailureCategory,
    FailureReceipt,
    FinalReceipt,
    PreparedReceipt,
    canonical_receipt_bytes,
    finalize_receipt,
    new_backup_id,
    parse_receipt,
    utc_now,
    validate_final_matches,
)


def destination_from_config(config: ToolkitConfig) -> DestinationAdapter:
    destination = config.destination
    if isinstance(destination, LocalDestination):
        return LocalDestinationAdapter(destination)
    if isinstance(destination, GitHubDestination):
        return GitHubDestinationAdapter(destination)
    if isinstance(destination, S3Destination):
        return S3DestinationAdapter(destination)
    raise AssertionError("unreachable destination type")


def _write_state_receipt(state_dir: Path, filename: str, content: bytes) -> None:
    receipts = state_dir / "receipts"
    try:
        if state_dir.is_symlink():
            raise DestinationError("Local receipt state is not a safe directory.")
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state_dir.is_symlink() or not state_dir.is_dir():
            raise DestinationError("Local receipt state is not a safe directory.")
        receipts.mkdir(mode=0o700, parents=True, exist_ok=True)
        if receipts.is_symlink() or not receipts.is_dir():
            raise DestinationError("Local receipt state is not a safe directory.")
        target = receipts / filename
        if target.exists():
            raise DestinationError("Local receipt state already contains this backup identifier.")
        temporary = receipts / f".{filename}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written == 0:
                    raise DestinationError("Local receipt write made no progress.")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, target, follow_symlinks=False)
        temporary.unlink()
    except ToolkitError:
        raise
    except OSError as exc:
        raise DestinationError("Local receipt state could not be written safely.") from exc
    finally:
        if "temporary" in locals():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _collect(config: ToolkitConfig, staging_root: Path) -> list[CollectedFile]:
    files: list[CollectedFile] = []
    file_collector = FileCollector()
    sqlite_collector = SQLiteCollector()
    for source in config.sources:
        if isinstance(source, (FileSource, DirectorySource)):
            files.extend(file_collector.collect(source, staging_root))
        elif isinstance(source, SQLiteSource):
            files.extend(sqlite_collector.collect(source, staging_root))
        else:
            raise AssertionError("unreachable source type")
    return files


def _failure_category(error: ToolkitError) -> FailureCategory:
    mapping: dict[ExitCode, FailureCategory] = {
        ExitCode.CONFIG: "config",
        ExitCode.POLICY: "policy",
        ExitCode.SECRET_DETECTED: "secret_detected",
        ExitCode.COLLECTION: "collection",
        ExitCode.CRYPTO: "crypto",
        ExitCode.DESTINATION: "destination",
        ExitCode.VERIFY: "verify",
        ExitCode.RESTORE: "restore",
    }
    return mapping[error.exit_code]


def _record_failure(
    config: ToolkitConfig,
    *,
    backup_id: str,
    started_at: datetime,
    completed_stage: str,
    files: list[CollectedFile],
    error: ToolkitError,
) -> None:
    receipt = FailureReceipt(
        backup_id=backup_id,
        started_at=started_at,
        source_names=tuple(sorted(source.name for source in config.sources)),
        file_count=len(files),
        total_bytes=sum(file.size_bytes for file in files),
        destination_type=config.destination.type,
        completed_stage=completed_stage,
        failed_at=utc_now(),
        error_category=_failure_category(error),
    )
    try:
        _write_state_receipt(
            config.state_dir,
            f"{backup_id}.failure.json",
            canonical_receipt_bytes(receipt),
        )
    except ToolkitError:
        pass


def run_backup(
    config: ToolkitConfig,
    *,
    destination: DestinationAdapter | None = None,
) -> FinalReceipt:
    """Run every backup stage and report success only after two read-back checks."""

    backup_id = new_backup_id()
    started_at = utc_now()
    stage = "initialized"
    files: list[CollectedFile] = []
    adapter = destination or destination_from_config(config)

    try:
        adapter.preflight()
        stage = "destination_preflight"
        with tempfile.TemporaryDirectory(prefix="agent-backup-toolkit-") as temporary:
            temporary_root = Path(temporary)
            temporary_root.chmod(0o700)
            staging_root = temporary_root / "staging"
            files = _collect(config, staging_root)
            if not files:
                raise PolicyError("No files were collected from the configured sources.")
            stage = "sources_collected"
            enforce_no_secrets(files)
            stage = "secret_scan_passed"

            manifest = build_manifest(files)
            manifest_digest = manifest_sha256(manifest)
            archive_path = temporary_root / f"{backup_id}.tar.gz"
            build_archive(files, manifest, archive_path)
            stage = "archive_created"

            artifact_filename = f"{backup_id}.tar.gz.age"
            encrypted_path = temporary_root / artifact_filename
            encrypt_file(archive_path, encrypted_path, config.age_recipient)
            artifact_digest = sha256_file(encrypted_path)
            try:
                artifact_bytes = encrypted_path.stat().st_size
            except OSError as exc:
                raise CryptoError("Encrypted artifact metadata could not be read.") from exc
            stage = "artifact_encrypted"

            prepared = PreparedReceipt(
                backup_id=backup_id,
                started_at=started_at,
                source_names=tuple(sorted({file.logical_source for file in files})),
                file_count=len(files),
                total_bytes=sum(file.size_bytes for file in files),
                destination_type=adapter.destination_type,
                artifact_filename=artifact_filename,
                artifact_bytes=artifact_bytes,
                artifact_sha256=artifact_digest,
                manifest_sha256=manifest_digest,
            )
            adapter.publish_prepared(backup_id, canonical_receipt_bytes(prepared))
            stage = "prepared_receipt_published"
            adapter.publish_artifact(backup_id, artifact_filename, encrypted_path)
            stage = "artifact_published"

            readback_path = temporary_root / f"readback-{artifact_filename}"
            adapter.read_artifact(
                backup_id,
                artifact_filename,
                readback_path,
                expected_bytes=artifact_bytes,
            )
            try:
                readback_bytes = readback_path.stat().st_size
                readback_digest = sha256_file(readback_path)
            except (OSError, CryptoError) as exc:
                raise DestinationError("Encrypted artifact read-back could not be hashed.") from exc
            if readback_bytes != artifact_bytes:
                raise DestinationError("Encrypted artifact read-back size does not match.")
            if readback_digest != artifact_digest:
                raise DestinationError("Encrypted artifact read-back digest does not match.")
            stage = "artifact_readback_verified"

            final = finalize_receipt(prepared)
            final_bytes = canonical_receipt_bytes(final)
            adapter.publish_final(backup_id, final_bytes)
            stage = "final_receipt_published"
            final_readback_bytes = adapter.read_final(backup_id)
            try:
                parsed_readback = parse_receipt(final_readback_bytes)
                if not isinstance(parsed_readback, FinalReceipt):
                    raise DestinationError("Final receipt read-back has the wrong outcome.")
                validate_final_matches(prepared, parsed_readback)
            except VerifyError as exc:
                raise DestinationError("Final receipt read-back validation failed.") from exc
            if final_readback_bytes != final_bytes:
                raise DestinationError("Final receipt read-back bytes do not match.")
            stage = "final_receipt_verified"

            _write_state_receipt(
                config.state_dir,
                f"{backup_id}.final.json",
                final_bytes,
            )
            return final
    except ToolkitError as error:
        _record_failure(
            config,
            backup_id=backup_id,
            started_at=started_at,
            completed_stage=stage,
            files=files,
            error=error,
        )
        raise
