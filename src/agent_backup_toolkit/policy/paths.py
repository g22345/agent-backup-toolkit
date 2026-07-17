"""Path, symlink, special-file, and eligible-text policy."""

from __future__ import annotations

import os
import stat
from pathlib import Path, PurePosixPath

from agent_backup_toolkit.errors import CollectionError, PolicyError

ALLOWED_TEXT_SUFFIXES = frozenset(
    {
        ".bash",
        ".cfg",
        ".conf",
        ".css",
        ".csv",
        ".fish",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
        ".zsh",
    }
)
ALLOWED_EXTENSIONLESS_NAMES = frozenset(
    {
        ".editorconfig",
        ".gitignore",
        "AGENTS",
        "CODE_OF_CONDUCT",
        "CONTRIBUTING",
        "Dockerfile",
        "LICENSE",
        "Makefile",
        "README",
        "SECURITY",
    }
)


def safe_relative_path(value: str | PurePosixPath) -> PurePosixPath:
    """Validate an archive-style relative path before staging it."""

    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts:
        raise PolicyError("A source path would escape its logical source.")
    for part in path.parts:
        if part in {"", "."} or any(ord(character) < 32 for character in part):
            raise PolicyError("A source path contains an unsafe component.")
    return path


def normalized_mode(source_mode: int) -> int:
    """Preserve only whether a regular file is executable."""

    return 0o755 if source_mode & 0o111 else 0o644


def _nofollow_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _same_object(before: os.stat_result, after: os.stat_result) -> bool:
    return before.st_dev == after.st_dev and before.st_ino == after.st_ino


def lstat_source(path: Path, *, required: bool) -> os.stat_result | None:
    try:
        result = path.lstat()
    except FileNotFoundError:
        if not required:
            return None
        raise CollectionError("A required source does not exist.") from None
    except OSError as exc:
        raise CollectionError("A source could not be inspected safely.") from exc
    if stat.S_ISLNK(result.st_mode):
        raise PolicyError("Symbolic links are not accepted as source roots.")
    return result


def open_source_file(path: Path, *, required: bool) -> tuple[int, os.stat_result] | None:
    """Open a source file without following a symlink and bind it to its lstat identity."""

    before = lstat_source(path, required=required)
    if before is None:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise PolicyError("A file source is not a regular file.")
    try:
        descriptor = os.open(path, _nofollow_flags())
    except OSError as exc:
        raise CollectionError("A source changed or could not be opened safely.") from exc
    after = os.fstat(descriptor)
    if not stat.S_ISREG(after.st_mode) or not _same_object(before, after):
        os.close(descriptor)
        raise PolicyError("A source changed while it was being opened.")
    return descriptor, after


def open_source_directory(path: Path, *, required: bool) -> tuple[int, os.stat_result] | None:
    """Open a source directory without following a symlink."""

    before = lstat_source(path, required=required)
    if before is None:
        return None
    if not stat.S_ISDIR(before.st_mode):
        raise PolicyError("A directory source is not a directory.")
    try:
        descriptor = os.open(path, _nofollow_flags(directory=True))
    except OSError as exc:
        raise CollectionError("A source directory changed or could not be opened safely.") from exc
    after = os.fstat(descriptor)
    if not stat.S_ISDIR(after.st_mode) or not _same_object(before, after):
        os.close(descriptor)
        raise PolicyError("A source directory changed while it was being opened.")
    return descriptor, after


def open_child(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    *,
    directory: bool,
) -> tuple[int, os.stat_result]:
    """Open a directory entry relative to its already-open parent."""

    safe_relative_path(name)
    try:
        descriptor = os.open(
            name,
            _nofollow_flags(directory=directory),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise CollectionError("A source entry changed or could not be opened safely.") from exc
    result = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(result.st_mode) or not _same_object(expected, result):
        os.close(descriptor)
        raise PolicyError("A source entry changed while it was being opened.")
    return descriptor, result


def ensure_eligible_text(relative_path: PurePosixPath, staged_path: Path) -> None:
    """Reject unknown file types, NUL-bearing files, and invalid UTF-8."""

    name = relative_path.name
    if (
        relative_path.suffix.lower() not in ALLOWED_TEXT_SUFFIXES
        and name not in ALLOWED_EXTENSIONLESS_NAMES
    ):
        raise PolicyError("An allowlisted source contains an unsupported file type.")
    try:
        content = staged_path.read_bytes()
    except OSError as exc:
        raise CollectionError("A staged file could not be checked safely.") from exc
    if b"\x00" in content:
        raise PolicyError("An allowlisted source contains binary data.")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError("An allowlisted source is not valid UTF-8 text.") from exc
