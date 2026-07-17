from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from agent_backup_toolkit.errors import CryptoError
from agent_backup_toolkit.manifest import build_manifest, canonical_manifest_bytes, manifest_sha256
from agent_backup_toolkit.models import CollectedFile


def fixture_file(tmp_path: Path, name: str, content: bytes) -> CollectedFile:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return CollectedFile(
        logical_source="notes",
        relative_path=PurePosixPath(name),
        staged_path=path,
        size_bytes=len(content),
        mode=0o644,
    )


def test_manifest_is_sorted_and_deterministic(tmp_path: Path) -> None:
    second = fixture_file(tmp_path, "second.md", b"second")
    first = fixture_file(tmp_path, "first.md", b"first")

    one = build_manifest([second, first])
    two = build_manifest([first, second])

    assert canonical_manifest_bytes(one) == canonical_manifest_bytes(two)
    assert manifest_sha256(one) == manifest_sha256(two)
    assert [entry.relative_path for entry in one.files] == ["first.md", "second.md"]


def test_manifest_blocks_file_changed_after_collection(tmp_path: Path) -> None:
    file = fixture_file(tmp_path, "note.md", b"before")
    file.staged_path.write_bytes(b"after with a different size")

    with pytest.raises(CryptoError, match="changed"):
        build_manifest([file])
