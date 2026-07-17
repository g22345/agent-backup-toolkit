"""Read-only access to sanitized local command state."""

from __future__ import annotations

from pathlib import Path

from agent_backup_toolkit.errors import ConfigError, VerifyError
from agent_backup_toolkit.receipts import FinalReceipt, parse_receipt


def latest_success_receipt(state_dir: Path) -> FinalReceipt | None:
    """Return the newest well-formed final success receipt, if one exists."""

    receipts_dir = state_dir / "receipts"
    if state_dir.is_symlink():
        raise ConfigError("Local receipt state is not a safe directory.")
    if not receipts_dir.exists():
        return None
    if not receipts_dir.is_dir() or receipts_dir.is_symlink():
        raise ConfigError("Local receipt state is not a safe directory.")

    candidates: list[tuple[float, Path]] = []
    for path in receipts_dir.glob("*.json"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _mtime, path in sorted(candidates, reverse=True):
        try:
            receipt = parse_receipt(path.read_bytes())
        except (OSError, VerifyError):
            continue
        if isinstance(receipt, FinalReceipt):
            return receipt
    return None
