from __future__ import annotations

from pathlib import Path

import pytest

from agent_backup_toolkit import orchestrator
from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.destinations.local import LocalDestinationAdapter
from agent_backup_toolkit.encryption import AGE_HEADER
from agent_backup_toolkit.errors import DestinationError

VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def config_for(tmp_path: Path) -> ToolkitConfig:
    source = tmp_path / "instructions.md"
    source.write_text("safe durable instructions\n", encoding="utf-8")
    return ToolkitConfig.model_validate(
        {
            "schema_version": 1,
            "age_recipient": VALID_RECIPIENT,
            "state_dir": str(tmp_path / "state"),
            "sources": [{"type": "file", "name": "instructions", "path": str(source)}],
            "destination": {"type": "local", "path": str(tmp_path / "remote")},
        }
    )


def fake_encrypt(plaintext: Path, encrypted: Path, _recipient: str) -> None:
    encrypted.write_bytes(AGE_HEADER + b"\n" + plaintext.read_bytes())
    encrypted.chmod(0o600)


def test_backup_records_success_only_after_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = config_for(tmp_path)
    monkeypatch.setattr(orchestrator, "encrypt_file", fake_encrypt)

    receipt = orchestrator.run_backup(config)

    assert receipt.outcome == "success"
    assert receipt.readback_verified is True
    assert (config.state_dir / "receipts" / f"{receipt.backup_id}.final.json").exists()
    assert receipt.backup_id in LocalDestinationAdapter(config.destination).list_backup_ids()  # type: ignore[arg-type]


class TamperingLocalAdapter(LocalDestinationAdapter):
    def read_artifact(
        self,
        backup_id: str,
        filename: str,
        output_path: Path,
        *,
        expected_bytes: int,
    ) -> None:
        super().read_artifact(
            backup_id,
            filename,
            output_path,
            expected_bytes=expected_bytes,
        )
        with output_path.open("ab") as handle:
            handle.write(b"tampered")


def test_readback_mismatch_is_failure_not_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = config_for(tmp_path)
    monkeypatch.setattr(orchestrator, "encrypt_file", fake_encrypt)
    adapter = TamperingLocalAdapter(config.destination)  # type: ignore[arg-type]

    with pytest.raises(DestinationError, match="does not match"):
        orchestrator.run_backup(config, destination=adapter)

    receipts = list((config.state_dir / "receipts").glob("*.json"))
    assert len(receipts) == 1
    assert receipts[0].name.endswith(".failure.json")
