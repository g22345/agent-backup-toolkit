from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from agent_backup_toolkit.errors import SecretDetectedError
from agent_backup_toolkit.models import CollectedFile
from agent_backup_toolkit.policy.secrets import enforce_no_secrets, scan_file


def collected(tmp_path: Path, content: bytes) -> CollectedFile:
    path = tmp_path / "settings.txt"
    path.write_bytes(content)
    return CollectedFile(
        logical_source="settings",
        relative_path=PurePosixPath("settings.txt"),
        staged_path=path,
        size_bytes=len(content),
        mode=0o644,
    )


def test_safe_fixture_has_no_findings(tmp_path: Path) -> None:
    file = collected(tmp_path, b"theme = 'warm-cream'\nretry_count = 3\n")

    assert scan_file(file) == []
    enforce_no_secrets([file])


def test_private_key_marker_is_reported_without_value(tmp_path: Path) -> None:
    marker = b"-----BEGIN " + b"PRIVATE KEY-----"
    file = collected(tmp_path, b"heading\n" + marker + b"\nsynthetic-placeholder\n")

    findings = scan_file(file)

    assert len(findings) == 1
    assert findings[0].rule_id == "private-key"
    assert findings[0].line_number == 2
    assert marker.decode() not in repr(findings)


def test_secret_error_never_reflects_matched_value(tmp_path: Path) -> None:
    value = b"sk-" + b"abcdefghijklmnopqrstuvwxyz123456"
    file = collected(tmp_path, b"api_key = '" + value + b"'\n")

    with pytest.raises(SecretDetectedError) as caught:
        enforce_no_secrets([file])

    assert value.decode() not in str(caught.value)
    assert "openai-key" in str(caught.value)
