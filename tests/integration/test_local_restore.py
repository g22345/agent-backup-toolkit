from __future__ import annotations

from pathlib import Path

import pytest

from agent_backup_toolkit import encryption, orchestrator
from agent_backup_toolkit import restore as restore_module
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.encryption import AGE_HEADER
from agent_backup_toolkit.restore import restore_backup

VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def test_local_backup_verify_preview_and_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "AGENTS.md"
    source.write_text("synthetic restore drill\n", encoding="utf-8")
    identity = tmp_path / "identity.txt"
    identity.write_text("synthetic identity fixture", encoding="utf-8")
    identity.chmod(0o600)
    config = ToolkitConfig.model_validate(
        {
            "schema_version": 1,
            "age_recipient": VALID_RECIPIENT,
            "state_dir": str(tmp_path / "state"),
            "sources": [{"type": "file", "name": "instructions", "path": str(source)}],
            "destination": {"type": "local", "path": str(tmp_path / "remote")},
        }
    )

    def encrypt(plaintext: Path, encrypted: Path, _recipient: str) -> None:
        encrypted.write_bytes(AGE_HEADER + b"\n" + plaintext.read_bytes())

    def decrypt(encrypted: Path, plaintext: Path, _identity: Path) -> None:
        plaintext.write_bytes(encrypted.read_bytes().split(b"\n", 1)[1])

    monkeypatch.setattr(orchestrator, "encrypt_file", encrypt)
    monkeypatch.setattr(encryption, "decrypt_file", decrypt)
    monkeypatch.setattr(restore_module, "encrypt_file", encrypt)
    monkeypatch.setattr(restore_module, "decrypt_file", decrypt)

    receipt = orchestrator.run_backup(config)
    target = tmp_path / "restored"
    preview = restore_backup(config, receipt.backup_id, identity, target)
    applied = restore_backup(config, receipt.backup_id, identity, target, apply=True)

    assert preview.applied is False
    assert applied.applied is True
    assert (target / "instructions" / "AGENTS.md").read_text(encoding="utf-8") == (
        "synthetic restore drill\n"
    )
