#!/usr/bin/env python3
"""Run a synthetic local backup, verify, preview, and restore drill."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from agent_backup_toolkit.config import ToolkitConfig
from agent_backup_toolkit.orchestrator import run_backup
from agent_backup_toolkit.restore import restore_backup
from agent_backup_toolkit.verify import verify_backup


def _dependency(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required dependency not found: {name}")
    return path


def _run(arguments: list[str]) -> bytes:
    result = subprocess.run(  # noqa: S603 - every executable is resolved by shutil.which
        arguments,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("synthetic drill dependency command failed")
    return result.stdout


def main() -> int:
    age = _dependency("age")
    age_keygen = _dependency("age-keygen")
    print(f"[1/6] dependencies ready ({Path(age).name}, {Path(age_keygen).name})")

    with tempfile.TemporaryDirectory(prefix="agent-backup-demo-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        source = root / "synthetic-workspace" / "AGENTS.md"
        source.parent.mkdir(mode=0o700)
        expected = "Synthetic durable instruction for disaster-recovery testing.\n"
        source.write_text(expected, encoding="utf-8")

        identity = root / "identity.txt"
        _run([age_keygen, "-o", str(identity)])
        identity.chmod(0o600)
        recipient = _run([age_keygen, "-y", str(identity)]).decode().strip()
        print("[2/6] synthetic source and temporary identity created")

        config = ToolkitConfig.model_validate(
            {
                "schema_version": 1,
                "age_recipient": recipient,
                "state_dir": str(root / "state"),
                "sources": [
                    {
                        "type": "file",
                        "name": "instructions",
                        "path": str(source),
                    }
                ],
                "destination": {"type": "local", "path": str(root / "destination")},
            }
        )

        receipt = run_backup(config)
        print("[3/6] encrypted backup published and read back")
        summary = verify_backup(config, receipt.backup_id, identity)
        if summary.file_count != 1:
            raise RuntimeError("synthetic verification returned the wrong file count")
        print("[4/6] receipt, artifact, archive, manifest, and file verified")

        target = root / "restore-target"
        preview = restore_backup(config, receipt.backup_id, identity, target)
        if (
            preview.applied
            or target.exists()
            or preview.preview.additions != ("instructions/AGENTS.md",)
        ):
            raise RuntimeError("synthetic restore preview was not write-free")
        print("[5/6] preview confirmed zero target writes")

        applied = restore_backup(config, receipt.backup_id, identity, target, apply=True)
        restored = target / "instructions" / "AGENTS.md"
        if not applied.applied or restored.read_text(encoding="utf-8") != expected:
            raise RuntimeError("synthetic restored content did not match")
        print("[6/6] add-only restore applied and content read back")

    print("synthetic disaster-recovery drill: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
