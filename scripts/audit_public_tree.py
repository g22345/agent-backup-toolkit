#!/usr/bin/env python3
"""Fail when a proposed public tree/history contains high-risk private material."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

MAX_PUBLIC_FILE_BYTES = 1024 * 1024
BLOCKED_SUFFIXES = (
    ".age",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tar.gz",
    ".tgz",
)
BLOCKED_PATH_PARTS = {"snapshots", "backups", "receipts", "customer-data"}
PRIVATE_MARKERS = (
    "/" + "Users" + "/",
    "C:" + "\\" + "Users" + "\\",
    "this" + "filmproduction.com",
    "this" + "filmhk",
    "custom" + "dreamer",
    "agent-" + "shared",
)
CREDENTIAL_PATTERNS = (
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,255}\b"),
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
)


def _git(repo: Path, arguments: list[str]) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable not found")
    result = subprocess.run(  # noqa: S603 - fixed git executable and internal arguments
        [git, "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError("git inspection failed")
    return result.stdout


def _tracked_files(repo: Path) -> list[Path]:
    output = _git(repo, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    return [repo / item.decode("utf-8") for item in output.split(b"\0") if item]


def _path_reason(relative: Path) -> str | None:
    lower = relative.as_posix().lower()
    if lower.endswith(BLOCKED_SUFFIXES):
        return "backup/database artifact extension"
    if any(part.lower() in BLOCKED_PATH_PARTS for part in relative.parts):
        return "private runtime directory name"
    return None


def _content_reasons(content: bytes) -> list[str]:
    reasons: list[str] = []
    lowered = content.lower()
    for marker in PRIVATE_MARKERS:
        if marker.lower().encode() in lowered:
            reasons.append("operator/business marker")
            break
    if any(pattern.search(content) for pattern in CREDENTIAL_PATTERNS):
        reasons.append("credential-shaped content")
    return reasons


def audit(repo: Path) -> list[str]:
    findings: list[str] = []
    for path in _tracked_files(repo):
        relative = path.relative_to(repo)
        path_reason = _path_reason(relative)
        if path_reason:
            findings.append(f"{relative.as_posix()}: {path_reason}")
            continue
        try:
            size = path.stat().st_size
            content = path.read_bytes()
        except OSError:
            findings.append(f"{relative.as_posix()}: unreadable tracked file")
            continue
        if size > MAX_PUBLIC_FILE_BYTES:
            findings.append(f"{relative.as_posix()}: file exceeds 1 MiB")
        if b"\x00" in content:
            findings.append(f"{relative.as_posix()}: binary tracked file")
            continue
        for reason in _content_reasons(content):
            findings.append(f"{relative.as_posix()}: {reason}")

    history = _git(repo, ["log", "--all", "--format=", "--no-ext-diff", "-p"])
    for reason in _content_reasons(history):
        findings.append(f"git-history: {reason}")
    history_paths = _git(repo, ["log", "--all", "--name-only", "--format="])
    for raw_path in history_paths.splitlines():
        if not raw_path:
            continue
        try:
            relative = Path(raw_path.decode("utf-8"))
        except UnicodeDecodeError:
            findings.append("git-history: non-UTF-8 path")
            continue
        reason = _path_reason(relative)
        if reason:
            findings.append(f"git-history/{relative.as_posix()}: {reason}")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    repo = arguments.repo.resolve()
    findings = audit(repo)
    if findings:
        print("public-tree audit: FAIL")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("public-tree audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
