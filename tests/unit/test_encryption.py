from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_backup_toolkit import encryption
from agent_backup_toolkit.errors import CryptoError


def fake_age_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
    output = Path(arguments[arguments.index("--output") + 1])
    input_path = Path(arguments[-1])
    if "--encrypt" in arguments:
        output.write_bytes(encryption.AGE_HEADER + b"\n" + input_path.read_bytes())
    else:
        content = input_path.read_bytes()
        output.write_bytes(content.split(b"\n", 1)[1])
    return SimpleNamespace(returncode=0)


def test_encrypt_and_decrypt_use_argument_arrays(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plaintext = tmp_path / "archive.tar.gz"
    encrypted = tmp_path / "archive.tar.gz.age"
    restored = tmp_path / "restored.tar.gz"
    identity = tmp_path / "identity.txt"
    plaintext.write_bytes(b"archive")
    identity.write_text("synthetic identity fixture", encoding="utf-8")
    identity.chmod(0o600)
    calls: list[list[str]] = []

    def record(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(arguments)
        return fake_age_run(arguments, **kwargs)

    monkeypatch.setattr(encryption, "_age_binary", lambda: "/usr/bin/age")
    monkeypatch.setattr(encryption.subprocess, "run", record)

    encryption.encrypt_file(plaintext, encrypted, "age1syntheticrecipient")
    encryption.decrypt_file(encrypted, restored, identity)

    assert restored.read_bytes() == b"archive"
    assert all(isinstance(call, list) for call in calls)
    assert calls[0][1] == "--encrypt"
    assert calls[1][1] == "--decrypt"


def test_timeout_error_does_not_reflect_process_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plaintext = tmp_path / "archive.tar.gz"
    encrypted = tmp_path / "archive.tar.gz.age"
    plaintext.write_bytes(b"archive")

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="age", timeout=1, output=b"sensitive process output")

    monkeypatch.setattr(encryption, "_age_binary", lambda: "/usr/bin/age")
    monkeypatch.setattr(encryption.subprocess, "run", timeout)

    with pytest.raises(CryptoError) as caught:
        encryption.encrypt_file(plaintext, encrypted, "age1syntheticrecipient", timeout_seconds=1)
    assert "sensitive process output" not in str(caught.value)


def test_identity_permissions_must_be_private(tmp_path: Path) -> None:
    encrypted = tmp_path / "archive.age"
    output = tmp_path / "plain.tar.gz"
    identity = tmp_path / "identity.txt"
    encrypted.write_bytes(encryption.AGE_HEADER + b"\npayload")
    identity.write_text("synthetic identity fixture", encoding="utf-8")
    identity.chmod(0o644)

    with pytest.raises(CryptoError, match="permissions"):
        encryption.decrypt_file(encrypted, output, identity)


def test_missing_identity_is_rejected_without_running_age(tmp_path: Path) -> None:
    encrypted = tmp_path / "archive.age"
    output = tmp_path / "plain.tar.gz"
    encrypted.write_bytes(encryption.AGE_HEADER + b"\npayload")

    with pytest.raises(CryptoError, match="identity file could not be inspected"):
        encryption.decrypt_file(encrypted, output, tmp_path / "missing-identity.txt")
