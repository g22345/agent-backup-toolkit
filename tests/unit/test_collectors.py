from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_backup_toolkit.collectors.files import FileCollector
from agent_backup_toolkit.collectors.sqlite import SQLiteCollector
from agent_backup_toolkit.config import DirectorySource, FileSource, SQLiteSource
from agent_backup_toolkit.errors import PolicyError


def test_file_collector_stages_one_text_file(tmp_path: Path) -> None:
    source_path = tmp_path / "AGENTS.md"
    source_path.write_text("durable instructions\n", encoding="utf-8")
    source = FileSource(type="file", name="instructions", path=source_path)

    files = FileCollector().collect(source, tmp_path / "staging")

    assert len(files) == 1
    assert files[0].relative_path.as_posix() == "AGENTS.md"
    assert files[0].staged_path.read_text(encoding="utf-8") == "durable instructions\n"


def test_directory_collector_honors_include_and_exclude(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "keep.md").write_text("keep", encoding="utf-8")
    (source_root / "skip.txt").write_text("not included", encoding="utf-8")
    ignored = source_root / "private"
    ignored.mkdir()
    (ignored / "skip.md").write_text("excluded", encoding="utf-8")
    source = DirectorySource(
        type="directory",
        name="notes",
        path=source_root,
        include=["**/*.md"],
        exclude=["private/**"],
    )

    files = FileCollector().collect(source, tmp_path / "staging")

    assert [file.relative_path.as_posix() for file in files] == ["keep.md"]


def test_unknown_binary_is_rejected(tmp_path: Path) -> None:
    source_path = tmp_path / "opaque.bin"
    source_path.write_bytes(b"\x00\x01\x02")
    source = FileSource(type="file", name="opaque", path=source_path)

    with pytest.raises(PolicyError, match="unsupported file type"):
        FileCollector().collect(source, tmp_path / "staging")


def test_sqlite_collector_uses_consistent_backup_api(tmp_path: Path) -> None:
    database = tmp_path / "agent-state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE durable_notes (id INTEGER PRIMARY KEY, note TEXT)")
        connection.execute("INSERT INTO durable_notes (note) VALUES (?)", ("safe fixture",))
    source = SQLiteSource(type="sqlite", name="agent-state", path=database)

    files = SQLiteCollector().collect(source, tmp_path / "staging")

    assert len(files) == 1
    with sqlite3.connect(files[0].staged_path) as snapshot:
        assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert snapshot.execute("SELECT note FROM durable_notes").fetchone() == ("safe fixture",)
