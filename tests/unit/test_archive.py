from __future__ import annotations

import io
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from agent_backup_toolkit.archive import build_archive, validate_archive_members
from agent_backup_toolkit.errors import VerifyError
from agent_backup_toolkit.manifest import build_manifest
from agent_backup_toolkit.models import CollectedFile


def one_file(tmp_path: Path) -> CollectedFile:
    path = tmp_path / "source-root" / "note.md"
    path.parent.mkdir()
    path.write_text("safe note\n", encoding="utf-8")
    return CollectedFile(
        logical_source="notes",
        relative_path=PurePosixPath("note.md"),
        staged_path=path,
        size_bytes=path.stat().st_size,
        mode=0o644,
    )


def test_archive_is_deterministic_and_hides_source_root(tmp_path: Path) -> None:
    file = one_file(tmp_path)
    manifest = build_manifest([file])
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    build_archive([file], manifest, first)
    build_archive([file], manifest, second)

    assert first.read_bytes() == second.read_bytes()
    assert str(file.staged_path.parent).encode() not in first.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        members = validate_archive_members(archive)
        assert [member.name for member in members] == ["manifest.json", "data/notes/note.md"]


def test_malicious_tar_path_is_rejected() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        manifest = tarfile.TarInfo("manifest.json")
        manifest.size = 2
        archive.addfile(manifest, io.BytesIO(b"{}"))
        unsafe = tarfile.TarInfo("../escape")
        unsafe.size = 1
        archive.addfile(unsafe, io.BytesIO(b"x"))
    buffer.seek(0)

    with tarfile.open(fileobj=buffer, mode="r:") as archive:
        with pytest.raises(VerifyError, match="unsafe path"):
            validate_archive_members(archive)


def test_links_are_rejected() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        manifest = tarfile.TarInfo("manifest.json")
        manifest.size = 2
        archive.addfile(manifest, io.BytesIO(b"{}"))
        link = tarfile.TarInfo("data/notes/link.md")
        link.type = tarfile.SYMTYPE
        link.linkname = "/outside"
        archive.addfile(link)
    buffer.seek(0)

    with tarfile.open(fileobj=buffer, mode="r:") as archive:
        with pytest.raises(VerifyError, match="link or special file"):
            validate_archive_members(archive)
