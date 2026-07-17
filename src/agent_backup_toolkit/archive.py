"""Deterministic tar.gz creation and archive-member safety checks."""

from __future__ import annotations

import gzip
import io
import os
import tarfile
from pathlib import Path, PurePosixPath

from agent_backup_toolkit.errors import CryptoError, VerifyError
from agent_backup_toolkit.manifest import Manifest, canonical_manifest_bytes
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.paths import safe_relative_path


def _tar_info(name: str, *, size: int, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.type = tarfile.REGTYPE
    return info


def build_archive(files: list[CollectedFile], manifest: Manifest, output_path: Path) -> None:
    """Write a deterministic plaintext archive inside protected staging."""

    if output_path.exists():
        raise CryptoError("Archive output already exists; refusing to overwrite it.")
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(output_path, flags, 0o600)
        with (
            os.fdopen(descriptor, "wb") as raw_output,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
        ):
            manifest_bytes = canonical_manifest_bytes(manifest)
            archive.addfile(
                _tar_info("manifest.json", size=len(manifest_bytes), mode=0o600),
                io.BytesIO(manifest_bytes),
            )
            by_key = {(file.logical_source, file.relative_path.as_posix()): file for file in files}
            for entry in manifest.files:
                file = by_key.get((entry.logical_source, entry.relative_path))
                if file is None:
                    raise CryptoError("Manifest and staged files do not match.")
                archive_name = PurePosixPath("data") / entry.logical_source / entry.relative_path
                safe_relative_path(archive_name)
                with file.staged_path.open("rb") as source_handle:
                    archive.addfile(
                        _tar_info(
                            archive_name.as_posix(),
                            size=entry.size_bytes,
                            mode=entry.mode,
                        ),
                        source_handle,
                    )
        output_path.chmod(0o600)
    except (OSError, tarfile.TarError) as exc:
        raise CryptoError("Plaintext archive creation failed.") from exc


def validate_archive_members(archive: tarfile.TarFile) -> tuple[tarfile.TarInfo, ...]:
    """Reject traversal, links, special files, duplicates, and unexpected roots."""

    safe_members: list[tarfile.TarInfo] = []
    names: set[str] = set()
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        try:
            safe_relative_path(path)
        except Exception as exc:
            raise VerifyError("The archive contains an unsafe path.") from exc
        if member.name in names:
            raise VerifyError("The archive contains a duplicate path.")
        names.add(member.name)
        if not member.isfile():
            raise VerifyError("The archive contains a link or special file.")
        if member.name != "manifest.json" and (not path.parts or path.parts[0] != "data"):
            raise VerifyError("The archive contains an unexpected top-level path.")
        safe_members.append(member)
    if "manifest.json" not in names:
        raise VerifyError("The archive does not contain a manifest.")
    return tuple(safe_members)
