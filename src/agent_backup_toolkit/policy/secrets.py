"""High-confidence, redacted secret detection."""

from __future__ import annotations

import math
import re
from collections import Counter

from agent_backup_toolkit.errors import SecretDetectedError
from agent_backup_toolkit.models import CollectedFile, SecretFinding

_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private-key", re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    ("openai-key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,255}\b")),
    ("aws-access-key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "structured-token",
        re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
)
_ASSIGNMENT = re.compile(
    rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\b"
    rb"\s*[:=]\s*[\"']([A-Za-z0-9_./+=-]{20,255})[\"']"
)


def _line_number(content: bytes, start: int) -> int:
    return content.count(b"\n", 0, start) + 1


def _entropy(value: bytes) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def scan_file(file: CollectedFile) -> list[SecretFinding]:
    """Scan a staged file and return metadata-only findings."""

    try:
        content = file.staged_path.read_bytes()
    except OSError as exc:
        raise SecretDetectedError("A staged file could not be scanned safely.") from exc

    findings: list[SecretFinding] = []
    seen: set[tuple[str, int]] = set()
    for rule_id, pattern in _PATTERNS:
        for match in pattern.finditer(content):
            line = _line_number(content, match.start())
            key = (rule_id, line)
            if key not in seen:
                findings.append(
                    SecretFinding(
                        logical_source=file.logical_source,
                        relative_file=file.relative_path.as_posix(),
                        rule_id=rule_id,
                        line_number=line,
                    )
                )
                seen.add(key)

    for match in _ASSIGNMENT.finditer(content):
        candidate = match.group(1)
        if _entropy(candidate) < 3.5:
            continue
        line = _line_number(content, match.start())
        key = ("high-entropy-assignment", line)
        if key not in seen:
            findings.append(
                SecretFinding(
                    logical_source=file.logical_source,
                    relative_file=file.relative_path.as_posix(),
                    rule_id="high-entropy-assignment",
                    line_number=line,
                )
            )
            seen.add(key)
    return findings


def enforce_no_secrets(files: list[CollectedFile]) -> None:
    """Block collection when any high-confidence finding is present."""

    findings = [finding for file in files for finding in scan_file(file)]
    if not findings:
        return
    first = findings[0]
    location = f"{first.logical_source}/{first.relative_file}"
    raise SecretDetectedError(
        f"Secret scan blocked the backup: {len(findings)} finding(s); first at {location} "
        f"({first.rule_id})."
    )
