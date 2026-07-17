"""Private GitHub Releases destination through the official gh CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from agent_backup_toolkit.config import GitHubDestination
from agent_backup_toolkit.destinations.base import final_filename, prepared_filename
from agent_backup_toolkit.destinations.local import _copy_new, _safe_backup_id
from agent_backup_toolkit.errors import DestinationError, DestinationIntegrityError


class GitHubDestinationAdapter:
    destination_type: Literal["github"] = "github"

    def __init__(self, config: GitHubDestination, *, timeout_seconds: int = 120) -> None:
        self.repository = config.repository
        self.tag_prefix = config.tag_prefix
        self.timeout_seconds = timeout_seconds

    def _tag(self, backup_id: str) -> str:
        return f"{self.tag_prefix}-{_safe_backup_id(backup_id)}"

    def _run(self, arguments: list[str]) -> bytes:
        binary = shutil.which("gh")
        if binary is None:
            raise DestinationError("GitHub destination requires the 'gh' command on PATH.")
        environment = os.environ.copy()
        environment["GH_PROMPT_DISABLED"] = "1"
        try:
            result = subprocess.run(  # noqa: S603 - executable is resolved by shutil.which
                [binary, *arguments],
                check=False,
                timeout=self.timeout_seconds,
                capture_output=True,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise DestinationError("GitHub destination operation timed out.") from exc
        except OSError as exc:
            raise DestinationError("GitHub destination process could not be started.") from exc
        if result.returncode != 0:
            raise DestinationError(
                "GitHub destination operation failed; command output was withheld for safety."
            )
        return result.stdout

    def preflight(self) -> None:
        visibility = self._run(
            [
                "repo",
                "view",
                self.repository,
                "--json",
                "visibility",
                "--jq",
                ".visibility",
            ]
        )
        if visibility.strip().upper() != b"PRIVATE":
            raise DestinationError("GitHub destination repository must be private.")

    def publish_prepared(self, backup_id: str, content: bytes) -> None:
        filename = prepared_filename(backup_id)
        with tempfile.TemporaryDirectory(prefix="agent-backup-github-") as temporary:
            path = Path(temporary) / filename
            path.write_bytes(content)
            path.chmod(0o600)
            self._run(
                [
                    "release",
                    "create",
                    self._tag(backup_id),
                    str(path),
                    "--repo",
                    self.repository,
                    "--title",
                    f"Encrypted agent backup {backup_id}",
                    "--notes",
                    "Client-side encrypted backup created by agent-backup-toolkit.",
                    "--latest=false",
                ]
            )

    def publish_artifact(self, backup_id: str, filename: str, source_path: Path) -> None:
        if filename != f"{_safe_backup_id(backup_id)}.tar.gz.age":
            raise DestinationError("Destination received an invalid artifact filename.")
        self._run(
            [
                "release",
                "upload",
                self._tag(backup_id),
                str(source_path),
                "--repo",
                self.repository,
            ]
        )

    def _download(
        self,
        backup_id: str,
        filename: str,
        output_path: Path,
        *,
        expected_bytes: int | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-backup-github-") as temporary:
            directory = Path(temporary)
            self._run(
                [
                    "release",
                    "download",
                    self._tag(backup_id),
                    "--pattern",
                    filename,
                    "--dir",
                    str(directory),
                    "--repo",
                    self.repository,
                ]
            )
            downloaded = directory / filename
            if downloaded.is_symlink() or not downloaded.is_file():
                raise DestinationError("GitHub read-back did not return the expected object.")
            if expected_bytes is not None and downloaded.stat().st_size != expected_bytes:
                raise DestinationIntegrityError("GitHub artifact size does not match its receipt.")
            _copy_new(downloaded, output_path)

    def read_artifact(
        self,
        backup_id: str,
        filename: str,
        output_path: Path,
        *,
        expected_bytes: int,
    ) -> None:
        if filename != f"{_safe_backup_id(backup_id)}.tar.gz.age":
            raise DestinationError("Destination received an invalid artifact filename.")
        self._download(backup_id, filename, output_path, expected_bytes=expected_bytes)

    def publish_final(self, backup_id: str, content: bytes) -> None:
        filename = final_filename(backup_id)
        with tempfile.TemporaryDirectory(prefix="agent-backup-github-") as temporary:
            path = Path(temporary) / filename
            path.write_bytes(content)
            path.chmod(0o600)
            self._run(
                [
                    "release",
                    "upload",
                    self._tag(backup_id),
                    str(path),
                    "--repo",
                    self.repository,
                ]
            )

    def read_final(self, backup_id: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="agent-backup-github-") as temporary:
            path = Path(temporary) / "readback.json"
            self._download(backup_id, final_filename(backup_id), path)
            if path.stat().st_size > 1024 * 1024:
                raise DestinationError("Final receipt exceeds the safe size limit.")
            return path.read_bytes()

    def list_backup_ids(self) -> tuple[str, ...]:
        output = self._run(
            [
                "release",
                "list",
                "--repo",
                self.repository,
                "--limit",
                "100",
                "--json",
                "tagName",
            ]
        )
        try:
            releases = json.loads(output)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise DestinationError("GitHub destination returned an invalid release list.") from exc
        prefix = f"{self.tag_prefix}-"
        results: list[str] = []
        if not isinstance(releases, list):
            raise DestinationError("GitHub destination returned an invalid release list.")
        for item in releases:
            if not isinstance(item, dict) or not isinstance(item.get("tagName"), str):
                continue
            tag = item["tagName"]
            if not tag.startswith(prefix):
                continue
            candidate = tag.removeprefix(prefix)
            try:
                results.append(_safe_backup_id(candidate))
            except DestinationError:
                continue
        return tuple(sorted(set(results)))
