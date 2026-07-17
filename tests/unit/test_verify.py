from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from agent_backup_toolkit import encryption, orchestrator
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.encryption import AGE_HEADER
from agent_backup_toolkit.errors import VerifyError
from agent_backup_toolkit.verify import verify_backup

VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def fake_encrypt(plaintext: Path, encrypted: Path, _recipient: str) -> None:
    encrypted.write_bytes(AGE_HEADER + b"\n" + plaintext.read_bytes())
    encrypted.chmod(0o600)


def fake_decrypt(encrypted: Path, plaintext: Path, _identity: Path) -> None:
    content = encrypted.read_bytes()
    if not content.startswith(AGE_HEADER + b"\n"):
        raise AssertionError("test fixture is not an age-like envelope")
    plaintext.write_bytes(content.split(b"\n", 1)[1])
    plaintext.chmod(0o600)


def create_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[ToolkitConfig, str, Path]:
    source = tmp_path / "note.md"
    source.write_text("safe durable note\n", encoding="utf-8")
    identity = tmp_path / "identity.txt"
    identity.write_text("synthetic identity fixture", encoding="utf-8")
    identity.chmod(0o600)
    config = ToolkitConfig.model_validate(
        {
            "schema_version": 1,
            "age_recipient": VALID_RECIPIENT,
            "state_dir": str(tmp_path / "state"),
            "sources": [{"type": "file", "name": "notes", "path": str(source)}],
            "destination": {"type": "local", "path": str(tmp_path / "remote")},
        }
    )
    monkeypatch.setattr(orchestrator, "encrypt_file", fake_encrypt)
    monkeypatch.setattr(encryption, "decrypt_file", fake_decrypt)
    receipt = orchestrator.run_backup(config)
    return config, receipt.backup_id, identity


def test_verify_checks_receipt_artifact_archive_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)

    summary = verify_backup(config, backup_id, identity)

    assert summary.backup_id == backup_id
    assert summary.file_count == 1
    assert summary.source_names == ("notes",)


def test_same_size_artifact_tamper_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    artifact = config.destination.path / backup_id / f"{backup_id}.tar.gz.age"  # type: ignore[union-attr]
    content = bytearray(artifact.read_bytes())
    content[-1] ^= 1
    artifact.write_bytes(content)

    with pytest.raises(VerifyError, match="digest does not match"):
        verify_backup(config, backup_id, identity)


def test_size_tamper_is_reported_as_verify_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    artifact = config.destination.path / backup_id / f"{backup_id}.tar.gz.age"  # type: ignore[union-attr]
    with artifact.open("ab") as handle:
        handle.write(b"x")

    with pytest.raises(VerifyError, match="size does not match"):
        verify_backup(config, backup_id, identity)


def test_tampered_final_receipt_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    final = config.destination.path / backup_id / f"{backup_id}.final.json"  # type: ignore[union-attr]
    final.write_text("not valid receipt json", encoding="utf-8")

    with pytest.raises(VerifyError, match="Receipt validation failed"):
        verify_backup(config, backup_id, identity)


def test_manifest_tamper_is_rejected_even_with_updated_artifact_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    backup_root = config.destination.path / backup_id  # type: ignore[union-attr]
    artifact = backup_root / f"{backup_id}.tar.gz.age"
    original_archive = artifact.read_bytes().split(b"\n", 1)[1]
    rewritten = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(original_archive), mode="r:gz") as source_archive,
        tarfile.open(fileobj=rewritten, mode="w:gz") as target_archive,
    ):
        for member in source_archive.getmembers():
            source_file = source_archive.extractfile(member)
            assert source_file is not None
            content = source_file.read()
            if member.name == "manifest.json":
                content = content.replace(b'"tool_version":"0.1.0a1"', b'"tool_version":"0.1.0a2"')
            replacement = tarfile.TarInfo(member.name)
            replacement.size = len(content)
            replacement.mode = member.mode
            target_archive.addfile(replacement, io.BytesIO(content))

    tampered_artifact = AGE_HEADER + b"\n" + rewritten.getvalue()
    artifact.write_bytes(tampered_artifact)
    final_path = backup_root / f"{backup_id}.final.json"
    final_data = json.loads(final_path.read_bytes())
    final_data["artifact_bytes"] = len(tampered_artifact)
    final_data["artifact_sha256"] = hashlib.sha256(tampered_artifact).hexdigest()
    final_path.write_text(
        json.dumps(final_data, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(VerifyError, match="manifest digest does not match"):
        verify_backup(config, backup_id, identity)
