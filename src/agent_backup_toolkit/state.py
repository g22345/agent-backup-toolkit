"""Read-only access to sanitized local command state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_backup_toolkit.errors import ConfigError


def latest_success_receipt(state_dir: Path) -> dict[str, Any] | None:
    """Return the newest well-formed final success receipt, if one exists."""

    receipts_dir = state_dir / "receipts"
    if not receipts_dir.exists():
        return None
    if not receipts_dir.is_dir() or receipts_dir.is_symlink():
        raise ConfigError("Local receipt state is not a safe directory.")

    candidates = sorted(
        receipts_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(data, dict)
            and data.get("outcome") == "success"
            and data.get("completed_stage") == "final_receipt_verified"
        ):
            return data
    return None
