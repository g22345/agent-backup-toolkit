from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agent_backup_toolkit.config import GitHubDestination, LocalDestination, S3Destination
from agent_backup_toolkit.destinations.github import GitHubDestinationAdapter
from agent_backup_toolkit.destinations.local import LocalDestinationAdapter
from agent_backup_toolkit.destinations.s3 import S3DestinationAdapter
from agent_backup_toolkit.errors import DestinationError

BACKUP_ID = "12345678-1234-4234-8234-123456789abc"
ARTIFACT = f"{BACKUP_ID}.tar.gz.age"


def exercise_contract(adapter: Any, tmp_path: Path) -> None:
    artifact = tmp_path / ARTIFACT
    readback = tmp_path / "readback.age"
    artifact.write_bytes(b"encrypted bytes")

    adapter.preflight()
    adapter.publish_prepared(BACKUP_ID, b'{"outcome":"prepared"}\n')
    adapter.publish_artifact(BACKUP_ID, ARTIFACT, artifact)
    adapter.read_artifact(
        BACKUP_ID,
        ARTIFACT,
        readback,
        expected_bytes=artifact.stat().st_size,
    )
    adapter.publish_final(BACKUP_ID, b'{"outcome":"success"}\n')

    assert readback.read_bytes() == artifact.read_bytes()
    assert adapter.read_final(BACKUP_ID) == b'{"outcome":"success"}\n'
    assert adapter.list_backup_ids() == (BACKUP_ID,)


def test_local_destination_contract_and_immutability(tmp_path: Path) -> None:
    adapter = LocalDestinationAdapter(LocalDestination(type="local", path=tmp_path / "remote"))
    exercise_contract(adapter, tmp_path)

    with pytest.raises(DestinationError, match="already exists"):
        adapter.publish_final(BACKUP_ID, b"replacement")


def test_github_preflight_rejects_public_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubDestinationAdapter(
        GitHubDestination(type="github", repository="owner/private-backups")
    )
    monkeypatch.setattr(adapter, "_run", lambda _arguments: b"PUBLIC\n")

    with pytest.raises(DestinationError, match="must be private"):
        adapter.preflight()


class MissingObject(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "404"}}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def head_bucket(self, **_kwargs: object) -> None:
        return None

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, int]:
        if (Bucket, Key) not in self.objects:
            raise MissingObject
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def put_object(self, *, Bucket: str, Key: str, Body: object, **_kwargs: object) -> None:
        if hasattr(Body, "read"):
            content = Body.read()
        else:
            content = Body
        assert isinstance(content, bytes)
        self.objects[(Bucket, Key)] = content

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **_kwargs: object) -> dict[str, object]:
        return {
            "Contents": [
                {"Key": key}
                for bucket, key in self.objects
                if bucket == Bucket and key.startswith(Prefix)
            ]
        }


def test_s3_destination_contract(tmp_path: Path) -> None:
    config = S3Destination(type="s3", bucket="private-backups", prefix="agent-backups")
    adapter = S3DestinationAdapter(config, client=FakeS3Client())

    exercise_contract(adapter, tmp_path)


def test_s3_config_rejects_credentials_in_endpoint() -> None:
    payload = {
        "type": "s3",
        "bucket": "private-backups",
        "endpoint_url": "https://operator:credential@example.invalid",
    }

    with pytest.raises(ValueError, match="must not contain credentials"):
        S3Destination.model_validate(payload)


def test_github_release_list_filters_unrelated_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GitHubDestinationAdapter(
        GitHubDestination(type="github", repository="owner/private-backups")
    )
    output = json.dumps(
        [
            {"tagName": f"agent-backup-{BACKUP_ID}"},
            {"tagName": "unrelated-release"},
        ]
    ).encode()
    monkeypatch.setattr(adapter, "_run", lambda _arguments: output)

    assert adapter.list_backup_ids() == (BACKUP_ID,)
