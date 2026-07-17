from __future__ import annotations

from pathlib import Path

from agent_backup_toolkit import orchestrator
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.encryption import AGE_HEADER
from agent_backup_toolkit.state import latest_success_receipt

VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def test_synthetic_local_backup_flow(monkeypatch: object, tmp_path: Path) -> None:
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "AGENTS.md").write_text("synthetic public fixture\n", encoding="utf-8")
    config = ToolkitConfig.model_validate(
        {
            "schema_version": 1,
            "age_recipient": VALID_RECIPIENT,
            "state_dir": str(tmp_path / "state"),
            "sources": [
                {
                    "type": "directory",
                    "name": "workspace",
                    "path": str(source),
                    "include": ["**/*.md"],
                }
            ],
            "destination": {"type": "local", "path": str(tmp_path / "remote")},
        }
    )

    def fake_encrypt(plaintext: Path, encrypted: Path, _recipient: str) -> None:
        encrypted.write_bytes(AGE_HEADER + b"\n" + plaintext.read_bytes())

    monkeypatch.setattr(orchestrator, "encrypt_file", fake_encrypt)  # type: ignore[attr-defined]

    receipt = orchestrator.run_backup(config)
    status = latest_success_receipt(config.state_dir)

    assert receipt.outcome == "success"
    assert status is not None
    assert status.backup_id == receipt.backup_id
