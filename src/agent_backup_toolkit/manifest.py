"""Canonical backup manifests and file digests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_backup_toolkit import __version__
from agent_backup_toolkit.errors import CryptoError, VerifyError
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.paths import safe_relative_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CryptoError("A staged file could not be hashed safely.") from exc
    return digest.hexdigest()


class ManifestEntry(BaseModel):
    """Canonical metadata for one file within one logical source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_source: str
    relative_path: str
    size_bytes: int = Field(ge=0)
    mode: int
    sha256: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        safe_relative_path(value)
        return value

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: int) -> int:
        if value not in {0o644, 0o755}:
            raise ValueError("manifest mode must be normalized")
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("sha256 must be lowercase hexadecimal")
        return value


class Manifest(BaseModel):
    """Deterministic manifest embedded in every plaintext archive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    tool_version: str = __version__
    files: tuple[ManifestEntry, ...]

    @model_validator(mode="after")
    def paths_are_unique_and_sorted(self) -> Manifest:
        keys = [(entry.logical_source, entry.relative_path) for entry in self.files]
        if len(keys) != len(set(keys)):
            raise ValueError("manifest file paths must be unique")
        if keys != sorted(keys):
            raise ValueError("manifest file paths must be sorted")
        return self


def canonical_manifest_bytes(manifest: Manifest) -> bytes:
    payload = manifest.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def manifest_sha256(manifest: Manifest) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def build_manifest(files: list[CollectedFile]) -> Manifest:
    entries: list[ManifestEntry] = []
    for file in sorted(
        files, key=lambda item: (item.logical_source, item.relative_path.as_posix())
    ):
        try:
            actual_size = file.staged_path.stat().st_size
        except OSError as exc:
            raise CryptoError("A staged file disappeared before manifest creation.") from exc
        if actual_size != file.size_bytes:
            raise CryptoError("A staged file changed before manifest creation.")
        entries.append(
            ManifestEntry(
                logical_source=file.logical_source,
                relative_path=file.relative_path.as_posix(),
                size_bytes=file.size_bytes,
                mode=file.mode,
                sha256=sha256_file(file.staged_path),
            )
        )
    return Manifest(files=tuple(entries))


def verify_manifest_files(manifest: Manifest, extracted_root: Path) -> None:
    """Verify every manifest entry and reject missing or extra extracted files."""

    expected: set[Path] = set()
    for entry in manifest.files:
        relative = safe_relative_path(entry.relative_path)
        path = extracted_root / "data" / entry.logical_source / relative.as_posix()
        expected.add(path)
        if not path.is_file() or path.is_symlink():
            raise VerifyError("A manifest file is missing or unsafe.")
        if path.stat().st_size != entry.size_bytes:
            raise VerifyError("A manifest file size does not match.")
        try:
            digest = sha256_file(path)
        except CryptoError as exc:
            raise VerifyError("A manifest file could not be verified.") from exc
        if digest != entry.sha256:
            raise VerifyError("A manifest file digest does not match.")

    actual = {
        path
        for path in (extracted_root / "data").rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != expected:
        raise VerifyError("The archive contains files not declared by its manifest.")
