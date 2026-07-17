"""S3-compatible destination with bounded retries and complete downloads."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Literal

from agent_backup_toolkit.config import S3Destination
from agent_backup_toolkit.destinations.base import final_filename, prepared_filename
from agent_backup_toolkit.destinations.local import _safe_backup_id
from agent_backup_toolkit.errors import DestinationError


class S3DestinationAdapter:
    destination_type: Literal["s3"] = "s3"

    def __init__(self, config: S3Destination, *, client: Any | None = None) -> None:
        self.bucket = config.bucket
        self.prefix = config.prefix
        if client is not None:
            self.client = client
            return
        try:
            boto3 = importlib.import_module("boto3")
            botocore_config = importlib.import_module("botocore.config")
        except ImportError as exc:
            raise DestinationError(
                "S3 destination requires the optional boto3 dependency."
            ) from exc
        request_config = botocore_config.Config(
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 4, "mode": "standard"},
        )
        self.client = boto3.client(
            "s3",
            region_name=config.region,
            endpoint_url=config.endpoint_url,
            config=request_config,
        )

    def _key(self, backup_id: str, filename: str) -> str:
        safe_id = _safe_backup_id(backup_id)
        allowed = {
            prepared_filename(safe_id),
            final_filename(safe_id),
            f"{safe_id}.tar.gz.age",
        }
        if filename not in allowed:
            raise DestinationError("Destination received an invalid object filename.")
        return f"{self.prefix}/{safe_id}/{filename}"

    def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return getattr(self.client, method)(**kwargs)
        except Exception as exc:
            raise DestinationError(
                "S3 destination operation failed; provider details were withheld for safety."
            ) from exc

    def _ensure_absent(self, key: str) -> None:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            response = getattr(exc, "response", {})
            error = response.get("Error", {}) if isinstance(response, dict) else {}
            code = str(error.get("Code", "")) if isinstance(error, dict) else ""
            if code in {"404", "NoSuchKey", "NotFound"}:
                return
            raise DestinationError(
                "S3 destination could not verify whether an object already exists."
            ) from exc
        raise DestinationError("Destination object already exists; refusing to replace it.")

    def preflight(self) -> None:
        self._call("head_bucket", Bucket=self.bucket)

    def publish_prepared(self, backup_id: str, content: bytes) -> None:
        key = self._key(backup_id, prepared_filename(backup_id))
        self._ensure_absent(key)
        self._call(
            "put_object",
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/json",
            IfNoneMatch="*",
        )

    def publish_artifact(self, backup_id: str, filename: str, source_path: Path) -> None:
        key = self._key(backup_id, filename)
        self._ensure_absent(key)
        try:
            with source_path.open("rb") as handle:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=handle,
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                )
        except Exception as exc:
            raise DestinationError(
                "S3 artifact upload failed; provider details were withheld for safety."
            ) from exc

    def read_artifact(self, backup_id: str, filename: str, output_path: Path) -> None:
        if output_path.exists():
            raise DestinationError("Read-back output already exists; refusing to replace it.")
        output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = self._key(backup_id, filename)
        try:
            self.client.download_file(self.bucket, key, str(output_path))
            output_path.chmod(0o600)
        except Exception as exc:
            raise DestinationError(
                "S3 artifact read-back failed; provider details were withheld for safety."
            ) from exc
        if output_path.is_symlink() or not output_path.is_file():
            raise DestinationError("S3 artifact read-back did not create a safe file.")

    def publish_final(self, backup_id: str, content: bytes) -> None:
        key = self._key(backup_id, final_filename(backup_id))
        self._ensure_absent(key)
        self._call(
            "put_object",
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/json",
            IfNoneMatch="*",
        )

    def read_final(self, backup_id: str) -> bytes:
        key = self._key(backup_id, final_filename(backup_id))
        response = self._call("get_object", Bucket=self.bucket, Key=key)
        try:
            content = response["Body"].read(1024 * 1024 + 1)
        except Exception as exc:
            raise DestinationError("S3 final receipt read-back failed.") from exc
        if not isinstance(content, bytes) or len(content) > 1024 * 1024:
            raise DestinationError("S3 final receipt exceeds the safe size limit.")
        return content

    def list_backup_ids(self) -> tuple[str, ...]:
        results: list[str] = []
        continuation_token: str | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": f"{self.prefix}/",
                "MaxKeys": 1000,
            }
            if continuation_token is not None:
                request["ContinuationToken"] = continuation_token
            response = self._call("list_objects_v2", **request)
            for item in response.get("Contents", []):
                key = item.get("Key", "")
                if not isinstance(key, str) or not key.endswith(".final.json"):
                    continue
                parts = key.split("/")
                if len(parts) < 3:
                    continue
                candidate = parts[-2]
                if parts[-1] != final_filename(candidate):
                    continue
                try:
                    results.append(_safe_backup_id(candidate))
                except DestinationError:
                    continue
            if not response.get("IsTruncated"):
                break
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                raise DestinationError("S3 destination returned an invalid continuation token.")
            continuation_token = next_token
        return tuple(sorted(set(results)))
