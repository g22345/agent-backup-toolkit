from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_backup_toolkit.collectors.files import FileCollector
from agent_backup_toolkit.config import DirectorySource, SourceLimits
from agent_backup_toolkit.errors import PolicyError, ToolkitError
from agent_backup_toolkit.policy.paths import safe_relative_path


@pytest.mark.parametrize("value", ["../escape", "/absolute", "safe/../../escape", "bad\nname"])
def test_unsafe_relative_paths_are_rejected(value: str) -> None:
    with pytest.raises(PolicyError):
        safe_relative_path(value)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    outside = tmp_path / "outside"
    source_root.mkdir()
    outside.mkdir()
    (outside / "private.md").write_text("outside", encoding="utf-8")
    (source_root / "linked.md").symlink_to(outside / "private.md")
    source = DirectorySource(
        type="directory",
        name="notes",
        path=source_root,
        include=["**/*.md"],
    )

    with pytest.raises(PolicyError, match="Symbolic links"):
        FileCollector().collect(source, tmp_path / "staging")


def test_symlink_swap_is_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    victim = source_root / "victim.md"
    original = source_root / "original.md"
    outside = tmp_path / "outside.md"
    victim.write_text("safe", encoding="utf-8")
    outside.write_text("outside", encoding="utf-8")

    from agent_backup_toolkit.collectors import files as files_module

    real_open_child = files_module.open_child

    def swap_then_open(*args: object, **kwargs: object) -> object:
        if kwargs.get("directory") is False:
            victim.rename(original)
            victim.symlink_to(outside)
        return real_open_child(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(files_module, "open_child", swap_then_open)
    source = DirectorySource(
        type="directory",
        name="notes",
        path=source_root,
        include=["**/*.md"],
    )

    with pytest.raises(ToolkitError, match=r"changed|opened safely"):
        FileCollector().collect(source, tmp_path / "staging")
    assert not (tmp_path / "staging" / "notes" / "victim.md").exists()


@pytest.mark.skipif(os.name == "nt", reason="FIFO is a POSIX special file")
def test_special_file_is_rejected(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    os.mkfifo(source_root / "named-pipe.txt")
    source = DirectorySource(
        type="directory",
        name="notes",
        path=source_root,
        include=["**/*.txt"],
    )

    with pytest.raises(PolicyError, match="special file"):
        FileCollector().collect(source, tmp_path / "staging")


def test_limit_tracker_blocks_total_bytes(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "one.md").write_text("12345", encoding="utf-8")
    (source_root / "two.md").write_text("67890", encoding="utf-8")
    source = DirectorySource(
        type="directory",
        name="notes",
        path=source_root,
        include=["**/*.md"],
        limits=SourceLimits(max_files=10, max_file_bytes=6, max_total_bytes=6),
    )

    with pytest.raises(PolicyError, match="max_total_bytes"):
        FileCollector().collect(source, tmp_path / "staging")
