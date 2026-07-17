from __future__ import annotations

from pathlib import Path

import pytest

from agent_backup_toolkit import encryption, orchestrator
from agent_backup_toolkit import restore as restore_module
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.encryption import AGE_HEADER
from agent_backup_toolkit.errors import CryptoError, RestoreError
from agent_backup_toolkit.restore import restore_backup

VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def fake_encrypt(plaintext: Path, encrypted: Path, _recipient: str) -> None:
    encrypted.write_bytes(AGE_HEADER + b"\n" + plaintext.read_bytes())
    encrypted.chmod(0o600)


def fake_decrypt(encrypted: Path, plaintext: Path, _identity: Path) -> None:
    plaintext.write_bytes(encrypted.read_bytes().split(b"\n", 1)[1])
    plaintext.chmod(0o600)


def create_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[ToolkitConfig, str, Path]:
    source = tmp_path / "note.md"
    source.write_text("restored content\n", encoding="utf-8")
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
    monkeypatch.setattr(restore_module, "encrypt_file", fake_encrypt)
    monkeypatch.setattr(restore_module, "decrypt_file", fake_decrypt)
    receipt = orchestrator.run_backup(config)
    return config, receipt.backup_id, identity


def test_restore_defaults_to_pure_preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target = tmp_path / "restore-target"

    result = restore_backup(config, backup_id, identity, target)

    assert result.applied is False
    assert result.preview.additions == ("notes/note.md",)
    assert not target.exists()


def test_apply_creates_new_files_without_deleting_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target = tmp_path / "restore-target"

    result = restore_backup(config, backup_id, identity, target, apply=True)

    assert result.applied is True
    assert result.rollback_path is None
    assert (target / "notes" / "note.md").read_text(encoding="utf-8") == "restored content\n"


def test_collision_requires_explicit_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target_file = tmp_path / "restore-target" / "notes" / "note.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content\n", encoding="utf-8")

    with pytest.raises(RestoreError, match="collisions"):
        restore_backup(config, backup_id, identity, tmp_path / "restore-target", apply=True)
    assert target_file.read_text(encoding="utf-8") == "existing content\n"


def test_overwrite_creates_verified_encrypted_rollback_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target_file = tmp_path / "restore-target" / "notes" / "note.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content\n", encoding="utf-8")

    result = restore_backup(
        config,
        backup_id,
        identity,
        tmp_path / "restore-target",
        apply=True,
        overwrite=True,
    )

    assert result.applied is True
    assert result.rollback_path is not None and result.rollback_path.exists()
    assert target_file.read_text(encoding="utf-8") == "restored content\n"


def test_rollback_failure_prevents_target_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target_file = tmp_path / "restore-target" / "notes" / "note.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content\n", encoding="utf-8")

    def fail_encrypt(*_args: object, **_kwargs: object) -> None:
        raise CryptoError("synthetic rollback failure")

    monkeypatch.setattr(restore_module, "encrypt_file", fail_encrypt)

    with pytest.raises(RestoreError, match="rollback"):
        restore_backup(
            config,
            backup_id,
            identity,
            tmp_path / "restore-target",
            apply=True,
            overwrite=True,
        )
    assert target_file.read_text(encoding="utf-8") == "existing content\n"


def test_symlink_parent_is_rejected_in_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target = tmp_path / "restore-target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "notes").symlink_to(outside, target_is_directory=True)

    result = restore_backup(config, backup_id, identity, target)

    assert result.preview.rejections == ("notes/note.md",)
    assert list(outside.iterdir()) == []


def test_overwrite_flag_without_apply_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)

    with pytest.raises(RestoreError, match="requires --apply"):
        restore_backup(
            config,
            backup_id,
            identity,
            tmp_path / "restore-target",
            overwrite=True,
        )


def test_interrupted_staging_does_not_replace_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config, backup_id, identity = create_backup(monkeypatch, tmp_path)
    target_file = tmp_path / "restore-target" / "notes" / "note.md"
    target_file.parent.mkdir(parents=True)
    target_file.write_text("existing content\n", encoding="utf-8")

    def interrupt(*_args: object, **_kwargs: object) -> Path:
        raise RestoreError("synthetic interrupted staging")

    monkeypatch.setattr(restore_module, "_stage_replacement", interrupt)

    with pytest.raises(RestoreError, match="interrupted staging"):
        restore_backup(
            config,
            backup_id,
            identity,
            tmp_path / "restore-target",
            apply=True,
            overwrite=True,
        )
    assert target_file.read_text(encoding="utf-8") == "existing content\n"
