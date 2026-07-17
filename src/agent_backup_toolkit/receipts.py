"""Sanitized prepared, final, and failure receipts."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from agent_backup_toolkit import __version__
from agent_backup_toolkit.errors import VerifyError

DestinationType = Literal["local", "github", "s3"]
FailureCategory = Literal[
    "config",
    "policy",
    "secret_detected",
    "collection",
    "crypto",
    "destination",
    "verify",
    "restore",
]


class ReceiptBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    tool_version: str = __version__
    backup_id: str
    started_at: datetime
    source_names: tuple[str, ...]
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    destination_type: DestinationType

    @field_validator("backup_id")
    @classmethod
    def validate_backup_id(cls, value: str) -> str:
        try:
            parsed = uuid.UUID(value)
        except ValueError as exc:
            raise ValueError("backup_id must be a UUID") from exc
        if str(parsed) != value:
            raise ValueError("backup_id must use canonical UUID form")
        return value

    @field_validator("started_at")
    @classmethod
    def validate_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("started_at must use UTC")
        return value

    @model_validator(mode="after")
    def source_names_are_safe(self) -> ReceiptBase:
        if tuple(sorted(set(self.source_names))) != self.source_names:
            raise ValueError("source_names must be unique and sorted")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) for name in self.source_names):
            raise ValueError("source_names contain an invalid logical name")
        return self


class ArtifactFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_filename: str
    artifact_bytes: int = Field(gt=0)
    artifact_sha256: str
    manifest_sha256: str

    @field_validator("artifact_filename")
    @classmethod
    def validate_artifact_filename(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f-]{36}\.tar\.gz\.age", value):
            raise ValueError("artifact_filename is invalid")
        return value

    @field_validator("artifact_sha256", "manifest_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("digest must be lowercase SHA-256")
        return value


class PreparedReceipt(ReceiptBase, ArtifactFields):
    outcome: Literal["prepared"] = "prepared"
    completed_stage: Literal["prepared"] = "prepared"

    @model_validator(mode="after")
    def artifact_matches_backup(self) -> PreparedReceipt:
        if self.artifact_filename != f"{self.backup_id}.tar.gz.age":
            raise ValueError("artifact_filename must match backup_id")
        return self


class FinalReceipt(ReceiptBase, ArtifactFields):
    outcome: Literal["success"] = "success"
    completed_stage: Literal["final_receipt_verified"] = "final_receipt_verified"
    completed_at: datetime
    readback_verified: Literal[True] = True

    @model_validator(mode="after")
    def final_fields_are_consistent(self) -> FinalReceipt:
        if self.artifact_filename != f"{self.backup_id}.tar.gz.age":
            raise ValueError("artifact_filename must match backup_id")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() != UTC.utcoffset(
            self.completed_at
        ):
            raise ValueError("completed_at must use UTC")
        return self


class FailureReceipt(ReceiptBase):
    outcome: Literal["failure"] = "failure"
    completed_stage: str
    failed_at: datetime
    error_category: FailureCategory

    @field_validator("completed_stage")
    @classmethod
    def validate_stage(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
            raise ValueError("completed_stage is invalid")
        return value

    @field_validator("failed_at")
    @classmethod
    def validate_failed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("failed_at must use UTC")
        return value


Receipt: TypeAlias = Annotated[
    PreparedReceipt | FinalReceipt | FailureReceipt,
    Field(discriminator="outcome"),
]
_RECEIPT_ADAPTER: TypeAdapter[Receipt] = TypeAdapter(Receipt)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_backup_id() -> str:
    return str(uuid.uuid4())


def canonical_receipt_bytes(receipt: Receipt) -> bytes:
    payload = receipt.model_dump(mode="json")
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def receipt_sha256(receipt: Receipt) -> str:
    return hashlib.sha256(canonical_receipt_bytes(receipt)).hexdigest()


def parse_receipt(content: bytes) -> Receipt:
    try:
        data = json.loads(content)
        return _RECEIPT_ADAPTER.validate_python(data)
    except (ValueError, TypeError) as exc:
        raise VerifyError("Receipt validation failed.") from exc


def finalize_receipt(
    prepared: PreparedReceipt,
    *,
    completed_at: datetime | None = None,
) -> FinalReceipt:
    payload = prepared.model_dump(
        exclude={"outcome", "completed_stage"},
    )
    return FinalReceipt(
        **payload,
        completed_at=completed_at or utc_now(),
    )


def validate_final_matches(prepared: PreparedReceipt, final: FinalReceipt) -> None:
    ignored = {"outcome", "completed_stage", "completed_at", "readback_verified"}
    if prepared.model_dump(exclude=ignored) != final.model_dump(exclude=ignored):
        raise VerifyError("Final receipt does not match its prepared receipt.")
