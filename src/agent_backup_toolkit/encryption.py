"""Safe subprocess wrapper for mandatory age encryption and decryption."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from agent_backup_toolkit.errors import CryptoError

AGE_HEADER = b"age-encryption.org/v1"


def _age_binary() -> str:
    binary = shutil.which("age")
    if binary is None:
        raise CryptoError("Required dependency 'age' was not found on PATH.")
    return binary


def _run_age(arguments: list[str], *, timeout_seconds: int) -> None:
    try:
        result = subprocess.run(  # noqa: S603 - executable is resolved by shutil.which
            arguments,
            check=False,
            timeout=timeout_seconds,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except subprocess.TimeoutExpired as exc:
        raise CryptoError("The age operation timed out.") from exc
    except OSError as exc:
        raise CryptoError("The age process could not be started.") from exc
    if result.returncode != 0:
        raise CryptoError("The age operation failed; its output was withheld for safety.")


def validate_age_envelope(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            header = handle.read(len(AGE_HEADER))
    except OSError as exc:
        raise CryptoError("Encrypted output could not be inspected.") from exc
    if header != AGE_HEADER:
        raise CryptoError("Encrypted output is not a recognized age envelope.")


def encrypt_file(
    plaintext_path: Path,
    encrypted_path: Path,
    recipient: str,
    *,
    timeout_seconds: int = 120,
) -> None:
    """Encrypt a file with one public recipient and no shell interpolation."""

    if encrypted_path.exists() or plaintext_path == encrypted_path:
        raise CryptoError("Encrypted output must be a new, separate file.")
    encrypted_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    arguments = [
        _age_binary(),
        "--encrypt",
        "--recipient",
        recipient,
        "--output",
        str(encrypted_path),
        str(plaintext_path),
    ]
    _run_age(arguments, timeout_seconds=timeout_seconds)
    try:
        if encrypted_path.stat().st_size <= len(AGE_HEADER):
            raise CryptoError("Encrypted output is unexpectedly empty.")
        encrypted_path.chmod(0o600)
    except OSError as exc:
        raise CryptoError("Encrypted output metadata could not be verified.") from exc
    validate_age_envelope(encrypted_path)


def _validate_identity(identity_path: Path) -> None:
    try:
        metadata = identity_path.lstat()
    except OSError as exc:
        raise CryptoError("The age identity file could not be inspected.") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise CryptoError("The age identity must be a regular, non-symlink file.")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CryptoError("The age identity permissions are too broad; use mode 0600.")


def decrypt_file(
    encrypted_path: Path,
    plaintext_path: Path,
    identity_path: Path,
    *,
    timeout_seconds: int = 120,
) -> None:
    """Decrypt with an operator-supplied private identity path."""

    _validate_identity(identity_path)
    validate_age_envelope(encrypted_path)
    if plaintext_path.exists() or encrypted_path == plaintext_path:
        raise CryptoError("Decrypted output must be a new, separate file.")
    plaintext_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    arguments = [
        _age_binary(),
        "--decrypt",
        "--identity",
        str(identity_path),
        "--output",
        str(plaintext_path),
        str(encrypted_path),
    ]
    _run_age(arguments, timeout_seconds=timeout_seconds)
    try:
        if not plaintext_path.is_file() or plaintext_path.is_symlink():
            raise CryptoError("Decrypted output was not created safely.")
        plaintext_path.chmod(0o600)
    except OSError as exc:
        raise CryptoError("Decrypted output metadata could not be verified.") from exc
