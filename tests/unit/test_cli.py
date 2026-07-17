from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agent_backup_toolkit.cli import app

runner = CliRunner()
VALID_RECIPIENT = "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    data = {
        "schema_version": 1,
        "age_recipient": VALID_RECIPIENT,
        "state_dir": str(tmp_path / "state"),
        "sources": [{"type": "file", "name": "instructions", "path": str(tmp_path / "AGENTS.md")}],
        "destination": {"type": "local", "path": str(tmp_path / "backups")},
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_help_lists_full_command_surface() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("init", "doctor", "backup", "verify", "restore", "status"):
        assert command in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "0.1.0a0" in result.stdout


def test_init_creates_config_and_refuses_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    first = runner.invoke(app, ["init", "--config", str(path)])
    second = runner.invoke(app, ["init", "--config", str(path)])

    assert first.exit_code == 0
    assert path.exists()
    assert second.exit_code == 2
    assert "refusing to overwrite" in second.stderr


def test_doctor_reports_missing_age_without_upload(monkeypatch: object, tmp_path: Path) -> None:
    path = write_config(tmp_path)
    monkeypatch.setattr("agent_backup_toolkit.cli.shutil.which", lambda _name: None)  # type: ignore[attr-defined]

    result = runner.invoke(app, ["doctor", "--config", str(path)])

    assert result.exit_code == 2
    assert "age" in result.stderr
    assert "uploaded" not in result.stdout.lower()


def test_status_ignores_incomplete_receipt(tmp_path: Path) -> None:
    path = write_config(tmp_path)
    receipts = tmp_path / "state" / "receipts"
    receipts.mkdir(parents=True)
    (receipts / "incomplete.json").write_text(
        json.dumps({"outcome": "success", "completed_stage": "artifact_uploaded"}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["status", "--config", str(path)])

    assert result.exit_code == 0
    assert "No fully verified backup" in result.stdout


def test_unimplemented_workflows_fail_instead_of_claiming_success() -> None:
    for command in ("backup", "verify", "restore"):
        result = runner.invoke(app, [command])
        assert result.exit_code == 2
        assert "not available" in result.stderr
