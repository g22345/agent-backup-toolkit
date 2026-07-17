from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_current_repository_passes_public_tree_audit() -> None:
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # noqa: S603 - sys.executable and repository script are fixed
        [
            sys.executable,
            str(repository / "scripts" / "audit_public_tree.py"),
            "--repo",
            str(repository),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout
